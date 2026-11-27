"""Local, single-user WebGIS with a versioned detector and durable exports."""
import argparse
from collections import Counter
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, StringIO
import json
import math
from pathlib import Path
import re
import shutil
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from PIL import Image, ImageDraw, TiffImagePlugin, UnidentifiedImageError

from .dataset import DATA, ROOT, manifest
from .inference import LABELS, Detection, YoloDetector, infer
from .spatial import read_image
from .survey import SEGMENT_WEIGHTS
from .survey_api import SurveyService
from .inspection import InspectionService, SAMPLE as INSPECTION_SAMPLE, MAX_BYTES as INSPECTION_MAX_BYTES
from .lab_segmentation import SegmentationLab
from .workspace_projects import WorkspaceProjects

MAX_BYTES = 45 * 1024 * 1024
WEB = ROOT / "web"
MEDIA = {"/": ("workspace.html", "text/html"), "/foto-lama": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
         "/survei": ("survey.html", "text/html"), "/survey.js": ("survey.js", "text/javascript"),
         "/survey.css": ("survey.css", "text/css"), "/styles.css": ("styles.css", "text/css"),
         "/field-preview.webp": ("field-preview.webp", "image/webp"),
         "/inspeksi": ("inspection.html", "text/html"), "/inspection.js": ("inspection.js", "text/javascript"),
         "/inspection.css": ("inspection.css", "text/css"),
         "/segmentasi": ("workspace.html", "text/html"), "/lab-teknis": ("segmentation.html", "text/html"),
         "/workspace.css": ("workspace.css", "text/css"), "/workspace.js": ("workspace.js", "text/javascript"),
         "/workspace_jobs.js": ("workspace_jobs.js", "text/javascript"),
         "/segmentation.js": ("segmentation.js", "text/javascript"),
         "/segmentation.css": ("segmentation.css", "text/css")}


def mock_raster():
    image = Image.new("RGB", (840, 620), "#393b25")
    draw = ImageDraw.Draw(image)
    draw.polygon([(0, 35), (840, 210), (840, 256), (0, 86)], fill="#8f805d")
    boxes = []
    colors = {"Healthy": "#6a8e4b", "Yellow": "#a59b50", "Dead": "#756556", "Small": "#8da76e"}
    for row in range(5):
        for col in range(8):
            x = 63 + col*97 + (row % 2)*42
            y = 226 + row*77 - col*3
            if row == 0 and col > 4:
                continue
            label = {5: "Dead", 11: "Yellow", 18: "Small", 32: "Yellow"}.get(row*8+col, "Healthy")
            size = 17 if label == "Small" else 26
            for index in range(9):
                angle = (index + (col + row) * 0.17) * 2 * math.pi / 9
                sx, sy = x + math.cos(angle)*size, y + math.sin(angle)*size
                draw.line((x, y, sx, sy), fill=colors[label], width=7)
            boxes.append(Detection((x-size, y-size, x+size, y+size), label,
                                   round(0.48 + ((row*13 + col*5) % 49)/100, 2)))
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[33550] = (0.000001, 0.000001, 0.0)
    tags[33922] = (0.0, 0.0, 0.0, 116.829, -1.266, 0.0)
    tags[34735] = (1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326)
    stream = BytesIO()
    image.save(stream, format="TIFF", tiffinfo=tags)
    return stream.getvalue(), boxes


def serialize(identifier, source, name, image, georef, corners, warning, boxes, version, strategy, stats):
    objects = []
    for index, det in enumerate(boxes, 1):
        x, y = det.center
        location = None
        if georef:
            longitude, latitude = georef.location(x, y)
            location = {"longitude": longitude, "latitude": latitude}
        objects.append({"id": f"palm-{index:04}", "class": det.label, "confidence": det.score,
                        "bbox_px": list(det.box), "center_px": [x, y], "location": location})
    counts = Counter(obj["class"] for obj in objects)
    return {"analysis_id": identifier, "source": source, "coordinate_mode": "geographic" if georef else "pixel",
            "source_image": {"name": name, "width": image.width, "height": image.height},
            "model": version, "method": strategy, "elapsed_ms": stats.get("elapsed_ms"),
            "calls": stats.get("calls"), "threshold": stats.get("threshold"), "tiles": stats.get("tiles", []),
            "counts": {"total": len(objects), **{label: counts[label] for label in LABELS}},
            "crs": georef.crs if georef else None, "footprint": corners,
            "spatial_warning": warning, "detections": objects}


def csv_export(result):
    buf = StringIO()
    out = csv.writer(buf)
    out.writerow(["id", "class", "confidence", "left_px", "top_px", "right_px", "bottom_px",
                  "center_x_px", "center_y_px", "longitude", "latitude"])
    for det in result["detections"]:
        loc = det["location"] or {}
        out.writerow([det["id"], det["class"], det["confidence"], *det["bbox_px"], *det["center_px"],
                      loc.get("longitude"), loc.get("latitude")])
    return buf.getvalue().encode("utf-8-sig")


def geojson_export(result):
    if result["coordinate_mode"] != "geographic":
        raise ValueError("Analisis ini hanya memiliki koordinat piksel.")
    features = []
    for det in result["detections"]:
        loc = det["location"]
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [loc["longitude"], loc["latitude"]]},
                         "properties": {"id": det["id"], "class": det["class"],
                                        "confidence": det["confidence"], "analysis_id": result["analysis_id"]}})
    return {"type": "FeatureCollection", "features": features}


class Service:
    def __init__(self, store, detector=None, kind=None, threshold=None, review=None, segment_weights=SEGMENT_WEIGHTS):
        self.store, self.detector, self.kind = store, detector, kind
        self.threshold = threshold if threshold is not None else (0.35 if kind == "compact" else 0.25)
        self.review = review
        self.segment_weights = segment_weights
        self.lock = Lock()

    def model_result(self, data, name, method):
        image, georef, footprint, warning = read_image(data)
        identifier = uuid4().hex
        version = self.detector.version
        with self.lock:
            if method == "selective" and self.review is not None:
                from .benchmark_v2 import selective_predict

                secondary, cutoff, threshold = self.review
                boxes, stats = selective_predict(image, self.detector, secondary, cutoff, threshold)
                if stats["calls"] == 2:
                    version = f"{version} + {secondary.version}"
            else:
                boxes, stats = infer(image, self.detector, method, budget=2, threshold=self.threshold)
                stats["threshold"] = self.threshold
        payload = serialize(identifier, "model", name, image, georef, footprint, warning, boxes,
                            version, method, stats)
        directory = self.store / identifier
        directory.mkdir(parents=True, exist_ok=False)
        image.thumbnail((1500, 1500))
        image.save(directory / "preview.jpg", quality=88)
        (directory / "analysis.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    def demo(self):
        data, boxes = mock_raster()
        image, georef, corners, warning = read_image(data)
        return serialize("demo", "demo", "Contoh georeferensi sintetis", image, georef, corners, warning, boxes,
                         None, "demo", {})


def make_handler(service):
    surveys = SurveyService(service)
    inspection = InspectionService()
    segmentation = SegmentationLab(service.store)
    projects = WorkspaceProjects(service.store, segmentation, surveys)

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, status, data, content_type, name=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            if name:
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, status, obj, name=None):
            self.send_bytes(status, json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                            "application/geo+json" if name else "application/json; charset=utf-8", name)

        def send_path(self, path, content_type, name=None):
            with path.open("rb") as source:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("X-Content-Type-Options", "nosniff")
                if name:
                    self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.end_headers()
                shutil.copyfileobj(source, self.wfile, length=1024**2)

        def do_GET(self):
            url = urlsplit(self.path)
            path = url.path
            if path=="/api/projects" or path.startswith("/api/projects/"):
                projects.http(self,path,"GET");return
            if path.startswith("/api/segmentation/"):
                segmentation.get(self, path)
                return
            if path.startswith("/api/surveys/"):
                surveys.get(self, path, parse_qs(url.query))
                return
            if path in MEDIA:
                file, mime = MEDIA[path]
                content_type = mime if mime.startswith("image/") else mime + "; charset=utf-8"
                self.send_bytes(200, (WEB / file).read_bytes(), content_type)
                return
            if path == "/api/inspection/status":
                self.send_json(200, {"model_ready": inspection.available(), "sample_ready": INSPECTION_SAMPLE.is_file()})
                return
            if path == "/api/inspection/sample-image":
                if not INSPECTION_SAMPLE.is_file():
                    self.send_json(404, {"detail": "Citra contoh tidak tersedia."})
                else:
                    self.send_path(INSPECTION_SAMPLE, "image/png")
                return
            if path == "/api/health":
                sample = next(r for r in manifest()["samples"] if r["split"] == "test")
                self.send_json(200, {"model_ready": service.detector is not None, "backend": service.kind,
                                     "version": service.detector.version if service.detector else None,
                                     "review_ready": service.review is not None,
                                     "sample_ready": (DATA / "pilot_images" / sample["file_name"]).is_file()})
                return
            if path == "/api/demo":
                self.send_json(200, service.demo())
                return
            match = re.fullmatch(r"/api/analyses/(demo|[a-f0-9]{32})(/image|/exports/csv|/exports/geojson)?", path)
            if not match:
                self.send_json(404, {"detail": "Halaman tidak ditemukan."})
                return
            identifier, endpoint = match.groups()
            directory = service.store / identifier
            if identifier == "demo":
                result = service.demo()
            elif (directory / "analysis.json").is_file():
                result = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
            else:
                self.send_json(404, {"detail": "Analisis tidak ditemukan."})
                return
            if endpoint == "/image":
                if identifier == "demo":
                    data, _ = mock_raster()
                    image, *_ = read_image(data)
                    buf = BytesIO()
                    image.save(buf, format="JPEG", quality=88)
                    output = buf.getvalue()
                else:
                    output = (directory / "preview.jpg").read_bytes()
                self.send_bytes(200, output, "image/jpeg")
            elif endpoint == "/exports/csv":
                self.send_bytes(200, csv_export(result), "text/csv; charset=utf-8", "crowntrace.csv")
            elif endpoint == "/exports/geojson":
                try:
                    self.send_json(200, geojson_export(result), "crowntrace.geojson")
                except ValueError as error:
                    self.send_json(422, {"detail": str(error)})
            else:
                self.send_json(200, result)

        def do_POST(self):
            url = urlsplit(self.path)
            if url.path=="/api/projects" or url.path.startswith("/api/projects/"):
                projects.http(self,url.path,"POST");return
            if url.path == "/api/segmentation/workspace":
                segmentation.batch(self, workspace=True)
                return
            if url.path == "/api/segmentation/batch":
                segmentation.batch(self)
                return
            if url.path == "/api/segmentation":
                segmentation.post(self, url.query)
                return
            if url.path == "/api/surveys" or url.path.startswith("/api/surveys/"):
                surveys.post(self, url.path, parse_qs(url.query))
                return
            if url.path == "/api/inspection":
                try:
                    if not inspection.available():
                        self.send_json(503, {"detail": "Bobot inspeksi belum tersedia."})
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= INSPECTION_MAX_BYTES:
                        self.send_json(413, {"detail": "Kirim satu tile hingga 5 MiB."})
                        return
                    if self.headers.get_content_type() not in {"image/jpeg","image/png","application/octet-stream"}:
                        raise ValueError("Kirim JPEG atau PNG.")
                    self.connection.settimeout(30)
                    data=self.rfile.read(length)
                    if len(data)!=length:
                        raise ValueError("Unggahan belum lengkap.")
                    params=parse_qs(url.query)
                    name=params.get("filename",["tile"])[0].replace("\\","/").split("/")[-1][:160]
                    self.send_json(200,inspection.analyze(data,name,params.get("mode",["balanced"])[0]))
                except (ValueError,TimeoutError) as error:
                    self.send_json(422,{"detail":str(error)})
                except OSError:
                    self.send_json(503,{"detail":"Citra atau model tidak dapat diproses."})
                return
            if url.path not in {"/api/analyses", "/api/sample"}:
                self.send_json(404, {"detail": "Halaman tidak ditemukan."})
                return
            if not service.detector:
                self.send_json(503, {"detail": "Bobot model belum tersedia. Contoh sintetis bisa dibuka."})
                return
            options = parse_qs(url.query)
            method = options.get("method", ["full_640"])[0]
            methods = {"full_640", "full_1024", "adaptive", "exhaustive"}
            if service.review is not None:
                methods.add("selective")
            if method not in methods:
                self.send_json(400, {"detail": "Strategi inferensi tidak dikenal."})
                return
            try:
                if url.path == "/api/sample":
                    row = next(r for r in manifest()["samples"] if r["split"] == "test")
                    name = row["file_name"]
                    path = DATA / "pilot_images" / name
                    if not path.is_file():
                        self.send_json(404, {"detail": "Foto contoh belum tersedia di folder data."})
                        return
                    data = path.read_bytes()
                else:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > MAX_BYTES:
                        self.send_json(413, {"detail": "Batas unggahan 45 MiB."})
                        return
                    if length < 1 or self.headers.get_content_type() not in {
                        "application/octet-stream", "image/jpeg", "image/png", "image/tiff"
                    }:
                        raise ValueError("Kirim berkas JPEG, PNG, atau GeoTIFF.")
                    self.connection.settimeout(30)
                    data = self.rfile.read(length)
                    name = options.get("filename", ["Citra unggahan"])[0].replace("\\", "/").split("/")[-1][:160]
                    if len(data) != length:
                        raise ValueError("Unggahan belum lengkap.")
                self.send_json(201, service.model_result(data, name, method))
            except (UnidentifiedImageError, Image.DecompressionBombError, ValueError, TimeoutError) as error:
                detail = str(error) if isinstance(error, ValueError) else "Berkas citra tidak dapat dibaca."
                self.send_json(422, {"detail": detail})
            except OSError:
                self.send_json(422, {"detail": "Citra atau penyimpanan lokal tidak dapat diproses."})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="Buka WebGIS di browser default.")
    parser.add_argument("--store", type=Path, default=ROOT / "analyses")
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--segment-weights", type=Path, default=SEGMENT_WEIGHTS, help="Bobot mask untuk peta kebun.")
    parser.add_argument("--backend", choices=("compact", "yolo"), default="compact")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--threshold", type=float)
    args = parser.parse_args()
    if args.threshold is not None and not 0.05 <= args.threshold <= 1:
        parser.error("--threshold harus dalam rentang 0.05 hingga 1.")
    if args.segment_weights != SEGMENT_WEIGHTS and not args.segment_weights.is_file():
        parser.error("--segment-weights harus menunjuk ke bobot yang tersedia.")
    chosen = args.weights
    threshold = args.threshold
    if chosen is None and args.backend == "compact":
        expanded = ROOT / "results/expanded_train/best.pth"
        report = ROOT / "results/expanded_benchmark/summary.json"
        pilot = ROOT / "results/pilot_train/best.pth"
        if expanded.is_file() and report.is_file():
            chosen = expanded
            if threshold is None:
                benchmark = json.loads(report.read_text(encoding="utf-8"))
                threshold = benchmark["metrics"]["val"]["full_640"]["operating_threshold"]
        elif pilot.is_file():
            chosen = pilot
    detector = review = None
    if chosen is not None:
        if args.backend == "compact":
            import torch
            from .compact import CompactDetector
            torch.set_num_threads(4)
            detector = CompactDetector(chosen, args.device)
            review_report = ROOT / "results/v2_benchmark/summary.json"
            review_weights = ROOT / "results/multiscale_train/best.pth"
            if args.weights is None and review_report.is_file() and review_weights.is_file():
                evidence = json.loads(review_report.read_text(encoding="utf-8"))
                if evidence.get("primary") == detector.version:
                    candidate = CompactDetector(review_weights, args.device)
                    if evidence.get("secondary") == candidate.version:
                        cutoff = evidence["review_cutoff_val_75th_percentile"]
                        review_threshold = evidence["val"]["selective_416"]["operating_threshold"]
                        review = (candidate, cutoff, review_threshold)
        else:
            detector = YoloDetector(chosen, args.device)
    handler = make_handler(Service(args.store, detector, args.backend if detector else None, threshold, review,
                                   args.segment_weights))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"CrownTrace tersedia di {url}", flush=True)
    try:
        if args.open_browser:
            import webbrowser
            webbrowser.open(url + "/")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
