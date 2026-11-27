"""Local HTTP lifecycle for windowed plantation surveys."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
import shutil
from threading import Lock
from time import monotonic
from uuid import uuid4

from .dataset import DATA
from .survey import GeoRaster, gdal_launcher, run_survey

LOGGER = logging.getLogger(__name__)
SAMPLE = DATA / "demo_mopad_mosaic.tif"
SAMPLE_BLOCKS = DATA / "demo_mopad_blocks.geojson"
MAX_SURVEY_BYTES = 12 * 1024**3
MAX_BLOCK_BYTES = 4 * 1024**2
FILES = {"summary.json": "application/json", "map.json": "application/json", "preview.png": "image/png",
         "trees.geojson": "application/geo+json", "crowns.geojson": "application/geo+json",
         "grid.geojson": "application/geo+json", "blocks.geojson": "application/geo+json",
         "coverage.geojson": "application/geo+json", "review.geojson": "application/geo+json",
         "blocks.csv": "text/csv; charset=utf-8", "crowntrace.gpkg": "application/geopackage+sqlite3"}


def length_of(handler, limit):
    value = int(handler.headers.get("Content-Length", "0"))
    if not 0 < value <= limit:
        raise ValueError("Ukuran unggahan kosong atau melebihi batas.")
    return value


class SurveyService:
    def __init__(self, service):
        self.parent = service
        self.root = service.store / "surveys"
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="survey")
        self.lock = Lock()
        self.active = set()

    def health(self):
        gdal = gdal_launcher()
        return {"ready": self.parent.detector is not None and gdal is not None,
                "gdal": gdal is not None, "detector": self.parent.detector is not None,
                "segmenter": self.parent.segment_weights.is_file(), "demo": SAMPLE.is_file(),
                "max_upload_bytes": MAX_SURVEY_BYTES}

    def save(self, folder, details):
        path = folder / "survey.json"
        pending = path.with_suffix(".tmp")
        with self.lock:
            pending.write_text(json.dumps(details, ensure_ascii=False), encoding="utf-8")
            pending.replace(path)

    def load(self, folder):
        with self.lock:
            result = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
            if result["status"] in {"queued", "running"} and result["id"] not in self.active:
                result.update(status="error", detail="Proses sebelumnya terhenti. Mulai ulang survei.")
        return result

    def create(self, name, demo=False):
        identifier = uuid4().hex
        folder = self.root / identifier
        folder.mkdir(parents=True, exist_ok=False)
        details = {"id": identifier, "name": name, "demo": demo, "status": "staged"}
        return folder, details

    def upload(self, handler, name):
        if handler.headers.get_content_type() not in {"application/octet-stream", "image/tiff"}:
            raise ValueError("Unggah berkas GeoTIFF.")
        length = length_of(handler, MAX_SURVEY_BYTES)
        self.root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.root).free < length + 64 * 1024**2:
            raise ValueError("Ruang penyimpanan tidak cukup untuk unggahan.")
        folder, details = self.create(name)
        handler.connection.settimeout(90)
        pending = folder / "input.upload"
        try:
            with pending.open("wb") as output:
                remaining = length
                while remaining:
                    buffer = handler.rfile.read(min(1024**2, remaining))
                    if not buffer:
                        raise ValueError("Unggahan terputus. Coba unggah ulang.")
                    output.write(buffer)
                    remaining -= len(buffer)
            pending.replace(folder / "input.tif")
        except (OSError, ValueError):
            pending.unlink(missing_ok=True)
            raise
        self.save(folder, details)
        return details

    def start(self, folder, details, options):
        if not isinstance(options, dict) or options.get("segment", True) not in (True, False):
            raise ValueError("Pengaturan survei tidak valid.")
        with self.lock:
            if details["id"] in self.active or details["status"] not in {"staged", "error"}:
                raise ValueError("Survei sudah berjalan atau sudah selesai.")
            self.active.add(details["id"])
        details.update(status="queued", phase="antrean", completed=0, total=0, detail="")
        self.save(folder, details)
        self.pool.submit(self.work, folder, details.copy(), options)
        return details

    def work(self, folder, details, options, on_progress=None):
        last = 0
        phase_before = ""

        def progress(phase, completed, total):
            nonlocal last, phase_before
            now = monotonic()
            if phase != phase_before or completed == total or now - last >= 0.25:
                phase_before, last = phase, now
                details.update(status="running", phase=phase, completed=completed, total=total)
                self.save(folder, details)
                if on_progress: on_progress(phase, completed, total)

        try:
            progress("persiapan", 0, 0)
            segmenter = None
            if options.get("segment", True) and self.parent.segment_weights.is_file():
                from .segment import CrownMaskPredictor

                segmenter = CrownMaskPredictor(self.parent.segment_weights)
            source = SAMPLE if details["demo"] else folder / "input.tif"
            blocks = options.get("blocks")
            if details["demo"] and blocks is None and SAMPLE_BLOCKS.is_file():
                blocks = json.loads(SAMPLE_BLOCKS.read_text(encoding="utf-8"))
            with self.parent.lock:
                run_survey(source, folder / "result", self.parent.detector, segmenter, blocks,
                           threshold=self.parent.threshold, progress=progress)
            details.update(status="done", phase="selesai", completed=1, total=1)
        except Exception as error:
            LOGGER.exception("Survei %s gagal", details["id"])
            detail = str(error) if isinstance(error, ValueError) else "Peta tidak dapat diproses. Periksa GeoTIFF dan ruang penyimpanan."
            details.update(status="error", detail=detail)
        finally:
            try:
                self.save(folder, details)
            finally:
                with self.lock:
                    self.active.remove(details["id"])

    def read_body(self, handler):
        if handler.headers.get_content_type() != "application/json":
            raise ValueError("Kirim pengaturan sebagai JSON.")
        size = length_of(handler, MAX_BLOCK_BYTES)
        handler.connection.settimeout(30)
        payload = handler.rfile.read(size)
        if len(payload) != size:
            raise ValueError("Data batas blok belum lengkap.")
        return json.loads(payload)

    def get(self, handler, path, query):
        if path == "/api/surveys/health":
            handler.send_json(200, self.health())
            return
        match = re.fullmatch(r"/api/surveys/([a-f0-9]{32})(?:/([a-z0-9_.]+))?", path)
        if not match:
            handler.send_json(404, {"detail": "Survei tidak ditemukan."})
            return
        identifier, name = match.groups()
        folder = self.root / identifier
        try:
            details = self.load(folder)
        except FileNotFoundError:
            handler.send_json(404, {"detail": "Survei tidak ditemukan."})
            return
        if name is None:
            handler.send_json(200, details)
        elif details["status"] != "done":
            handler.send_json(409, {"detail": "Hasil survei belum tersedia."})
        elif name == "window":
            self.send_window(handler, folder, details, query)
        elif name in FILES:
            attachment = name if name.endswith((".gpkg", ".geojson", ".csv")) else None
            handler.send_path(folder / "result" / name, FILES[name], attachment)
        else:
            handler.send_json(404, {"detail": "Berkas survei tidak ditemukan."})

    def send_window(self, handler, folder, details, query):
        # Limit per-request raster reads to a bounded display window.
        try:
            rect = tuple(int(query[k][0]) for k in ("x0", "y0", "x1", "y1"))
            if not 1 <= rect[2] - rect[0] <= 4096 or not 1 <= rect[3] - rect[1] <= 4096:
                raise ValueError("Jendela tampilan di luar batas.")
            source = SAMPLE if details["demo"] else folder / "input.tif"
            with GeoRaster(source) as raster:
                image = raster.window(rect, limit=1200)
            handler.send_bytes(200, image, "image/png")
        except (ValueError, KeyError, OSError):
            handler.send_json(422, {"detail": "Jendela gambar tidak dapat dibaca."})

    def post(self, handler, path, query):
        match = re.fullmatch(r"/api/surveys/([a-f0-9]{32})/start", path)
        if path not in {"/api/surveys", "/api/surveys/demo"} and not match:
            handler.send_json(404, {"detail": "Jalur survei tidak ditemukan."})
            return
        if not self.health()["ready"]:
            handler.send_json(503, {"detail": "Detektor atau pembaca GeoTIFF belum siap. Periksa QGIS dan bobot model."})
            return
        try:
            if path == "/api/surveys":
                name = query.get("filename", ["Orthomosaic"])[0].replace("\\", "/").split("/")[-1][:160]
                details = self.upload(handler, name)
                handler.send_json(201, details)
            elif path == "/api/surveys/demo":
                if not SAMPLE.is_file():
                    handler.send_json(404, {"detail": "GeoTIFF contoh tidak ditemukan."})
                    return
                folder, details = self.create("Contoh MOPAD", demo=True)
                self.save(folder, details)
                handler.send_json(202, self.start(folder, details, self.read_body(handler)))
            else:
                folder = self.root / match[1]
                details = self.load(folder)
                options = self.read_body(handler)
                handler.send_json(202, self.start(folder, details, options))
        except FileNotFoundError:
            handler.send_json(404, {"detail": "Survei atau berkas GeoTIFF tidak ditemukan."})
        except (ValueError, TimeoutError, OSError) as error:
            detail = str(error) if isinstance(error, ValueError) else "Unggahan tidak selesai atau penyimpanan tidak tersedia."
            handler.send_json(422, {"detail": detail})
