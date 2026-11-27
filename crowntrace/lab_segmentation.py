"""Local research-only instance-mask upload API; each image retains its own pixel frame."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import perf_counter
from urllib.parse import parse_qs
from uuid import uuid4
import json
import re
import shutil

from PIL import Image, ImageOps, UnidentifiedImageError
from .lab_batch_map import identify, compose, compose_workspace, BATCH_FILES
from .dataset import ROOT

MAX_UPLOAD = 32 * 1024**2
PROFILES = {"mopad": "MOPAD · Mahkota pilot + kategori visual",
            "mopad_hr": "MOPAD · Resolusi tinggi (lebih sensitif)",
            "roboflow_prompt": "CrownPrompt Detail · Trees v3",
            "roboflow_fast": "CrownPrompt Fast · Trees v3"}
FILES = {"original.png": "image/png", "overlay.png": "image/png",
         "summary.json": "application/json", "trees.csv": "text/csv; charset=utf-8",
         "crowns.coco.json": "application/json"}


class SegmentationLab:
    def __init__(self, store):
        self.root = Path(store) / "segmentation"
        self.lock = Lock()

    def examples(self):
        root=ROOT/'data/UJI_SENDIRI'
        mosaic=sorted((root/'07_MOPAD_MOSAIC_BERDEKATAN').glob('*.jpg'))
        large=sorted((root/'08_MOPAD_120_FOTO').glob('*.jpg'))
        separate=sorted((root/'01_MOPAD_HITUNG_DAN_MASK').glob('*.jpg'))[:1]+sorted((root/'04_PALANGKA_UJI_EKSTERNAL').glob('*.png'))[:1]
        maps=sorted((root/'06_PETA_GEOTIFF').glob('02_*.tif'))
        return {'mosaic':mosaic,'separate':separate,'map':maps,'large':large}

    def status(self):
        from .photo import profile
        output = {}
        for key, label in PROFILES.items():
            try:
                detector, mask, _, _ = profile(key)
                ready = detector.is_file() and mask.is_file()
            except (ValueError, OSError, KeyError):
                ready = False
            output[key] = {"name": label, "ready": ready}
        return {"profiles": output, "max_bytes": MAX_UPLOAD, "coordinate_mode": "pixel", "batch_maps": True, "workspace": 3}

    def get(self, handler, path):
        if path == '/api/segmentation/examples':
            handler.send_json(200,{key:[{'name':f.name,'url':f'/api/segmentation/example/{key}/{i}'} for i,f in enumerate(files)] for key,files in self.examples().items()})
            return
        example=re.fullmatch(r'/api/segmentation/example/(mosaic|separate|map|large)/(\d{1,3})',path)
        if example:
            files=self.examples()[example[1]];i=int(example[2])
            if i<len(files):
                f=files[i];handler.send_path(f,{'.jpg':'image/jpeg','.png':'image/png','.tif':'image/tiff'}[f.suffix.lower()]);return
            handler.send_json(404,{'detail':'Contoh tidak tersedia.'});return
        if path == "/api/segmentation/status":
            handler.send_json(200, self.status())
            return
        batch = re.fullmatch(r"/api/segmentation/batch/([a-f0-9]{32})/([a-z.]+)", path)
        if batch and batch[2] in BATCH_FILES:
            file = self.root / "batch" / batch[1] / batch[2]
            if file.is_file():
                handler.send_path(file, BATCH_FILES[batch[2]])
                return
        match = re.fullmatch(r"/api/segmentation/([a-f0-9]{32})/([a-z.]+)", path)
        if not match or match[2] not in FILES:
            handler.send_json(404, {"detail": "Berkas hasil tidak ditemukan."})
            return
        file = self.root / match[1] / match[2]
        if not file.is_file():
            handler.send_json(404, {"detail": "Berkas hasil tidak ditemukan."})
            return
        handler.send_path(file, FILES[match[2]], match[2] if match[2].endswith((".csv", ".json")) else None)

    def post(self, handler, query):
        params = parse_qs(query)
        model = params.get("profile", ["auto"])[0]
        if model not in {"auto", "workspace"} and model not in PROFILES:
            handler.send_json(400, {"detail": "Profil segmentasi tidak dikenal."})
            return
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_UPLOAD:
                handler.send_json(413, {"detail": "Gunakan satu JPEG/PNG hingga 32 MiB."})
                return
            if handler.headers.get_content_type() not in {"image/jpeg", "image/png", "application/octet-stream"}:
                raise ValueError("Gunakan JPEG atau PNG.")
            handler.connection.settimeout(60)
            data = handler.rfile.read(length)
            if len(data) != length:
                raise ValueError("Unggahan belum lengkap.")
            result = self.analyze_bytes(data, params.get("filename", ["foto"])[0], model)
            handler.send_json(201, result)
        except (ValueError, UnidentifiedImageError, Image.DecompressionBombError, TimeoutError) as error:
            handler.send_json(422, {"detail": str(error) or "Citra tidak dapat dibaca."})
        except OSError:
            handler.send_json(503, {"detail": "Citra, bobot atau penyimpanan lokal tidak dapat diproses."})

    def analyze_bytes(self, data, name, model="workspace"):
        """Shared local engine for direct technical tests and durable projects."""
        from io import BytesIO
        if not 0 < len(data) <= MAX_UPLOAD:
            raise ValueError("Batas satu foto adalah 32 MiB.")
        provenance = identify(data)
        workspace = model == "workspace"
        if workspace:
            model = "roboflow_prompt" if provenance["recommended"] == "roboflow_prompt" else "mopad_hr"
        if model == "auto":
            model = provenance["recommended"]
            if model is None:
                raise ValueError("Citra tidak dikenali. Pilih profil model secara manual.")
        if model not in PROFILES or not self.status()["profiles"][model]["ready"]:
            raise ValueError("Bobot profil belum tersedia.")
        with Image.open(BytesIO(data)) as handle:
            if handle.format not in {"JPEG", "PNG"} or handle.width * handle.height > (25_000_000 if workspace else 9_000_000):
                raise ValueError("Gunakan JPEG/PNG hingga 25 megapiksel untuk ruang kerja utama.")
            image = ImageOps.exif_transpose(handle).convert("RGB")
        name = name.replace("\\", "/").split("/")[-1][:160]
        identifier = uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.root).free < image.width*image.height*9 + 64*1024**2:
            raise ValueError("Ruang penyimpanan kurang untuk menyimpan hasil.")
        target = self.root / identifier
        from .photo import analyze
        import torch
        with self.lock, TemporaryDirectory(dir=self.root) as tmp:
            torch.set_num_threads(4)
            source = Path(tmp) / "input.png"
            image.save(source)
            started = perf_counter()
            result = analyze(source, target, model, large_photo=workspace)
            image.save(target / "original.png")
            result.update(source=name, id=identifier, profile_label=PROFILES[model],
                          placement=provenance["placement"], reference_count=provenance["reference_count"],
                          recommended_profile=provenance["recommended"], source_sha256=provenance["sha256"],
                          elapsed_seconds=round(perf_counter()-started, 3),
                          note="Koordinat piksel per foto, bukan orthomosaic geografis. Kategori visual bukan diagnosis.",
                          files={key: f"/api/segmentation/{identifier}/{key}" for key in FILES})
            coco_path = target / "crowns.coco.json"
            coco = json.loads(coco_path.read_text(encoding="utf-8"))
            coco["images"][0]["file_name"] = name
            coco_path.write_text(json.dumps(coco, ensure_ascii=False), encoding="utf-8")
            (target / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def batch(self, handler, *, workspace=False):
        try:
            size = int(handler.headers.get("Content-Length", "0"))
            if not 0 < size <= 32768 or handler.headers.get_content_type() != "application/json":
                raise ValueError("Kirim daftar hasil analisis dalam JSON.")
            handler.connection.settimeout(30)
            payload = json.loads(handler.rfile.read(size))
            with self.lock:
                make = compose_workspace if workspace else compose
                result = make(self.root, payload.get("ids") if isinstance(payload, dict) else None)
            handler.send_json(201, result)
        except (ValueError, OSError, TypeError, KeyError) as error:
            handler.send_json(422, {"detail": str(error) or "Peta gabungan tidak dapat dibuat."})
