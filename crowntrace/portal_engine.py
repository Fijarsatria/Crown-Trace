"""Adapter from the managed-service workflow to the existing, versioned CV/GIS engines.

No public upload endpoint executes a model. Only approved jobs created by staff call
this adapter; outputs are immutable, per-run files and are released separately.
"""
from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil
from time import perf_counter

from PIL import Image, ImageOps, ImageDraw
import numpy as np

from .dataset import ROOT
from .survey import DETECT_WEIGHTS, SEGMENT_WEIGHTS, GeoRaster, gdal_launcher, run_survey

PUBLIC_SAMPLE = ROOT / "results/layers_20261126/mopad_layers"
TILE_SAMPLE = ROOT / "data/roboflow_crowns_grouped_v1/val/18aa_jpg.rf.09748715e2783c5e61d6219cea189845.jpg"
INVENTORY_TILE_SAMPLE = ROOT / "data/portal_samples/mopad_inventory_tile.png"
INSPECTION_SAMPLE = ROOT / "data/external/mendeley_ganoderma_v2/Ganoderma_UAV_RGB_Dataset_v2/images/test/P6G_T000068_test_positive_context.png"
GEO_SAMPLE = ROOT / "data/demo_mopad_mosaic.tif"
GEO_BOUNDARY = ROOT / "data/demo_mopad_blocks.geojson"

SERVICES = {
    "inventory": {"name": "Inventaris kebun", "description": "Hitungan pohon, titik dan rekap blok."},
    "segmentation": {"name": "Pemetaan mahkota", "description": "Delineasi tajuk, hitungan dan hasil GIS."},
    "inspection": {"name": "Inspeksi kondisi", "description": "Prioritas inspeksi visual dari tile RGB."},
}
DATA_PLANS = {
    "geotiff": "Orthomosaic GeoTIFF RGB",
    "tile": "Tile drone RGB 640 × 640",
    "field": "Butuh pengambilan/persiapan data",
}
PROFILES = {
    "geo_inventory": {"service": "inventory", "format": "geotiff", "name": "Inventaris GeoTIFF", "note": "Detektor MOPAD, overlap 20%; titik dan rekap blok."},
    "geo_crowns": {"service": "segmentation", "format": "geotiff", "name": "Mahkota GeoTIFF · pilot", "note": "Detektor dan mask pilot MOPAD. Kontur estimasi, belum validasi lintas kebun."},
    "tile_inventory": {"service": "inventory", "format": "tile", "name": "Inventaris tile", "note": "Detektor MOPAD pada tile RGB sebanding MOPAD Site 2; bukan model Trees v3. Koordinat piksel."},
    "detail": {"service": "segmentation", "format": "tile", "name": "CrownPrompt Detail", "note": "Prompt 128, empat rotasi dan ambang adaptif. Untuk tile sebanding Trees v3."},
    "fast": {"service": "segmentation", "format": "tile", "name": "CrownPrompt Fast", "note": "Prompt 128, satu rotasi. Untuk tile sebanding Trees v3."},
    "balanced": {"service": "inspection", "format": "tile", "name": "Balanced", "note": "Model Ganoderma V2 pilot. Kandidat inspeksi, bukan diagnosis."},
    "high_recall": {"service": "inspection", "format": "tile", "name": "High Recall", "note": "Lebih banyak kandidat; lebih banyak peringatan keliru. Bukan diagnosis."},
}

FILES = {
    "summary.json": ("Ringkasan analisis", "application/json"),
    "map.json": ("Data peta", "application/json"),
    "preview.png": ("Citra dasar", "image/png"),
    "overlay.png": ("Pratinjau anotasi", "image/png"),
    "mask-layer.png": ("Lapisan mask transparan", "image/png"),
    "trees.geojson": ("Titik pohon", "application/geo+json"),
    "crowns.geojson": ("Poligon mahkota", "application/geo+json"),
    "blocks.geojson": ("Batas blok", "application/geo+json"),
    "grid.geojson": ("Grid densitas", "application/geo+json"),
    "coverage.geojson": ("Cakupan", "application/geo+json"),
    "review.geojson": ("Indikasi visual", "application/geo+json"),
    "blocks.csv": ("Rekap per blok", "text/csv; charset=utf-8"),
    "trees.csv": ("Data per pohon", "text/csv; charset=utf-8"),
    "crowns.coco.json": ("Mask COCO RLE", "application/json"),
    "crowntrace.gpkg": ("GeoPackage multilayer", "application/geopackage+sqlite3"),
    "inspection.json": ("Hasil penyaringan", "application/json"),
    "report.json": ("Laporan CrownTrace", "application/json"),
}

MODEL_NOTES = {
    "geo": "Estimasi berdasarkan citra yang tersedia; validasi hitungan/koordinat lapangan dan QA diperlukan. Label visual bukan kesehatan terkonfirmasi.",
    "tile": "Hasil tile dalam piksel; bukan sensus geografis atau estimasi hektar. Kinerja bergantung skala, pencahayaan, dan distribusi citra.",
    "inspection": "Penyaringan model pada tile Ganoderma V2 yang sebanding. Kandidat adalah prioritas pemeriksaan, bukan diagnosis. Pohon yang tidak ditandai belum tentu sehat.",
}
VISUAL_LABELS = {"Healthy": "Tampak normal", "Yellow": "Tajuk menguning",
                 "Dead": "Indikasi mati", "Small": "Tajuk kecil", "Unassigned": "Belum dinilai",
                 "palm_crown": "Mahkota terdeteksi"}


def allowed_profiles(service, source_format):
    return [{"id": key, **value} for key, value in PROFILES.items()
            if value["service"] == service and value["format"] == source_format]


def engine_status():
    from .inspection import InspectionService
    base = DETECT_WEIGHTS.is_file()
    geo = gdal_launcher() is not None
    return {
        "geo_inventory": base and geo,
        "geo_crowns": base and geo and SEGMENT_WEIGHTS.is_file(),
        "tile_inventory": base,
        "detail": all((ROOT / "results/iteration_20261126/selected_detail" / name).is_file()
                      for name in ("detector.pth", "mask.pth", "training.json")),
        "fast": all((ROOT / "results/iteration_20261126/selected_fast" / name).is_file()
                    for name in ("detector.pth", "mask.pth", "training.json")),
        "balanced": InspectionService().available(),
        "high_recall": InspectionService().available(),
    }


def validate_source(path, service, data_plan, boundary_path=None):
    """Check the input is a supported format and spatial metadata is interpretable.
    Appropriate GSD, imagery provenance, permissions, and scope still require staff QA.
    """
    path = Path(path)
    with path.open("rb") as f:
        is_tiff = f.read(4) in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
    if is_tiff:
        if service == "inspection" or data_plan == "tile":
            raise ValueError("Layanan ini memerlukan tile JPEG/PNG berukuran 640 × 640.")
    else:
        with Image.open(path) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError("Gunakan GeoTIFF RGB, JPEG, atau PNG.")
            if data_plan in {"geotiff", "field"}:
                raise ValueError("Pekerjaan ini memerlukan orthomosaic GeoTIFF yang telah dipersiapkan.")
            if image.size != (640, 640):
                raise ValueError("Tile model harus tepat 640 × 640 piksel.")
            if image.mode not in {"RGB", "RGBA"}:
                raise ValueError("Gunakan citra warna RGB/RGBA.")
            image.verify()
        if boundary_path:
            raise ValueError("Batas blok tidak dapat digunakan pada tile tanpa koordinat.")
        return {"format": "tile", "width": 640, "height": 640, "crs": None,
                "coordinate_mode": "pixel", "note": MODEL_NOTES["inspection" if service == "inspection" else "tile"]}
    if not gdal_launcher():
        raise ValueError("Pembaca GeoTIFF belum tersedia. Siapkan QGIS/GDAL sebelum validasi.")
    boundary = json.loads(Path(boundary_path).read_text(encoding="utf-8-sig")) if boundary_path else None
    with GeoRaster(path) as raster:
        raster.request({"method": "blocks", "features": boundary["features"] if boundary else None})
        return {"format": "geotiff", "coordinate_mode": "geographic", **raster.info,
                "note": MODEL_NOTES["geo"]}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write(path, document):
    Path(path).write_text(json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")


def _catalog(folder):
    return [{"name": name, "label": label, "mime": mime, "bytes": (folder / name).stat().st_size}
            for name, (label, mime) in FILES.items() if (folder / name).is_file()]


def _geo_report(folder, profile, *, example=False):
    raw = _read(folder / "summary.json")
    blocks = raw["blocks"]
    area = round(sum(b["area_ha"] for b in blocks), 4)
    covered = round(sum(b["covered_ha"] for b in blocks), 4)
    report = {
        "kind": "geographic", "profile": profile, "profile_name": PROFILES[profile]["name"],
        "example": example, "coordinate_mode": "geographic",
        "width": raw["width"], "height": raw["height"], "crs": raw["crs"], "geotransform": raw.get("geotransform"),
        "trees": raw["points"], "crowns": raw["crowns"], "review_candidates": raw.get("review_points", 0),
        "area_ha": area, "covered_ha": covered, "coverage_pct": round(100 * covered / area, 2) if area else None,
        "blocks": blocks, "grid": raw.get("grid", []), "visual_counts": raw.get("visual_counts", {}),
        "detector": raw.get("detector"), "segmenter": raw.get("segmenter"),
        "elapsed_seconds": raw.get("elapsed_seconds"),
        "note": (("Contoh QA penelitian MOPAD; georeferensi demonstrasi, bukan lokasi perkebunan nyata. " if example else "") + MODEL_NOTES["geo"]),
        "layers": ["imagery", "points", "blocks", "density", "coverage", "review"] + (["crowns"] if raw["crowns"] else []),
        "preview_file": "preview.png", "map_file": "map.json",
    }
    return report


def public_sample_report():
    if not (PUBLIC_SAMPLE / "summary.json").is_file():
        raise FileNotFoundError("Contoh QA belum tersedia.")
    rep = _geo_report(PUBLIC_SAMPLE, "geo_crowns", example=True)
    rep["files"] = _catalog(PUBLIC_SAMPLE)
    return rep


def clone_report(source, destination, profile="geo_crowns"):
    """Copy only known, audited example assets; never recursively copy arbitrary paths."""
    destination.mkdir(parents=True, exist_ok=False)
    for name in FILES:
        if name != "report.json" and (source / name).is_file():
            shutil.copyfile(source / name, destination / name)
    report = _geo_report(destination, profile, example=True)
    report["files"] = _catalog(destination)
    _write(destination / "report.json", report)
    return report


def _rle_mask(rle):
    height, width = rle["size"]
    total = height * width
    packed = np.zeros(total, dtype=bool)
    position = 0
    for i, count in enumerate(rle["counts"]):
        if type(count) is not int or count < 0 or position + count > total:
            raise ValueError("Mask COCO tidak konsisten.")
        if i % 2:
            packed[position:position + count] = True
        position += count
    if position != total:
        raise ValueError("Panjang mask COCO tidak lengkap.")
    return packed.reshape((height, width), order="F")


def _tile_report(folder, profile, raw, rows, *, example=False):
    is_inspection = PROFILES[profile]["service"] == "inspection"
    report = {
        "kind": "inspection" if is_inspection else "tile",
        "profile": profile, "profile_name": PROFILES[profile]["name"], "example": example,
        "coordinate_mode": "pixel", "width": raw["width"], "height": raw["height"], "crs": None,
        "trees": len(rows), "crowns": raw.get("crowns", 0),
        "review_candidates": raw.get("review_candidates") if is_inspection else None,
        "review_tiers":raw.get("review_tiers") if is_inspection else None,
        "area_ha": None, "covered_ha": None, "coverage_pct": None, "blocks": [], "grid": [],
        "visual_counts": raw.get("counts", {}),
        "detector": raw.get("detector"), "segmenter": raw.get("segmenter"),
        "classifier": raw.get("classifier"), "elapsed_seconds": raw.get("elapsed_seconds"),
        "note": (("Contoh penelitian, bukan pekerjaan lapangan pelanggan. " if example else "") +
                 MODEL_NOTES["inspection" if is_inspection else "tile"]),
        "layers": ["imagery", "points"] + (["crowns"] if raw.get("crowns") else []) + (["review", "boxes"] if is_inspection else (["boxes"] if profile == "tile_inventory" else [])),
        "preview_file": "preview.png", "map_file": "map.json",
    }
    if is_inspection:
        report["review_cutoff"] = raw["review_cutoff"]
    if (folder / "mask-layer.png").is_file():
        report["mask_file"] = "mask-layer.png"
    return report


class MappingEngine:
    """Single-worker engine; models are loaded lazily and inputs remain local."""
    def __init__(self):
        self.detector = None
        self.segmenter = None
        self.inspection = None

    def validate(self, source, service, data_plan, boundary):
        return validate_source(source, service, data_plan, boundary)

    def available(self):
        return engine_status()

    def _base(self):
        import torch
        from .compact import CompactDetector
        torch.set_num_threads(4)
        if self.detector is None:
            self.detector = CompactDetector(DETECT_WEIGHTS)
        return self.detector

    def run(self, profile, source, boundary, output, progress, *, example=False):
        import torch
        torch.set_num_threads(4)
        start = perf_counter()
        output = Path(output)
        source = Path(source)
        spec = PROFILES[profile]
        if not self.available().get(profile):
            raise ValueError("Bobot atau pembaca raster untuk profil ini belum tersedia.")
        progress("preparing", 0, 1)
        if spec["format"] == "geotiff":
            from .segment import CrownMaskPredictor
            model = self._base()
            segment = None
            if profile == "geo_crowns":
                if self.segmenter is None:
                    self.segmenter = CrownMaskPredictor(SEGMENT_WEIGHTS)
                segment = self.segmenter
            blocks = _read(boundary) if boundary else None
            run_survey(source, output, model, segment, blocks,
                       threshold=.40, progress=progress)
            report = _geo_report(output, profile, example=example)
        elif profile in {"detail", "fast"}:
            from .photo import analyze
            progress("deteksi", 0, 1)
            analyze(source, output, "roboflow_prompt" if profile == "detail" else "roboflow_fast")
            raw = _read(output / "summary.json")
            raw["elapsed_seconds"] = round(perf_counter() - start, 3)
            with Image.open(source) as image:
                ImageOps.exif_transpose(image).convert("RGB").save(output / "preview.png")
            with (output / "trees.csv").open(encoding="utf-8-sig", newline="") as handle:
                tree_rows = list(csv.DictReader(handle))
            coco = _read(output / "crowns.coco.json")
            rgba = np.zeros((raw["height"], raw["width"], 4), dtype=np.uint8)
            for ann in coco["annotations"]:
                mask = _rle_mask(ann["segmentation"])
                padded = np.pad(mask, 1)
                interior = padded[1:-1, 1:-1] & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
                rgba[mask] = (116, 235, 197, 78)
                rgba[mask & ~interior] = (173, 249, 218, 235)
            Image.fromarray(rgba).save(output / "mask-layer.png")
            rows = [{"id": f'T-{int(row["id"]):04d}', "center": [float(row["x_px"]), float(row["y_px"])],
                     "score": float(row["score"]), "visual_label": row["label"], "mask_px": int(row["mask_px"])}
                    for row in tree_rows]
            _write(output / "map.json", {"trees": rows, "blocks": []})
            report = _tile_report(output, profile, raw, rows, example=example)
        else:
            output.mkdir(parents=True, exist_ok=False)
            with Image.open(source) as image:
                original = ImageOps.exif_transpose(image).convert("RGB")
            original.save(output / "preview.png")
            progress("deteksi", 0, 1)
            if profile == "tile_inventory":
                from .inference import fuse
                model = self._base()
                detections = [x for x in fuse(model.predict(original, 640), center_ratio=0) if x.score >= .40]
                rows = [{"id": f"T-{i:04d}", "center": list(d.center), "bbox": list(d.box),
                         "score": d.score, "visual_label": d.label} for i, d in enumerate(detections, 1)]
                raw = {"width": original.width, "height": original.height, "trees": len(rows),
                       "crowns": 0, "counts": dict(Counter(r["visual_label"] for r in rows)), "detector": model.version}
            else:
                from .inspection import InspectionService
                if self.inspection is None:
                    self.inspection = InspectionService()
                raw = self.inspection.analyze(source.read_bytes(), "Tile RGB", profile)
                _write(output / "inspection.json", raw)
                rows = [{"id": f'T-{r["id"]:04d}', "center": r["center_px"], "bbox": r["bbox_px"],
                         "score": r["detector_score"], "review_score": r["review_score"],
                         "review_candidate": r["review_candidate"],"review_tier":r.get("review_tier"),
                         "visual_label": "Kandidat pemeriksaan" if r["review_candidate"] else "Belum dinilai"}
                        for r in raw["records"]]
                raw["counts"] = {"Kandidat pemeriksaan": raw["review_candidates"],
                                 "Belum dinilai": len(rows) - raw["review_candidates"]}
            raw["elapsed_seconds"] = round(perf_counter() - start, 3)
            _write(output / "summary.json", raw)
            _write(output / "map.json", {"trees": rows, "blocks": []})
            annotated = original.copy()
            draw = ImageDraw.Draw(annotated)
            for row in rows:
                color = "#ec7eac" if row.get("review_candidate") else "#a7f6cf"
                draw.rectangle(row["bbox"], outline=color, width=2)
            annotated.save(output / "overlay.png")
            with (output / "trees.csv").open("w", encoding="utf-8-sig", newline="") as sink:
                writer = csv.writer(sink)
                writer.writerow(["id", "x_px", "y_px", "detector_score", "label", "review_score", "candidate"])
                for row in rows:
                    writer.writerow([row["id"], *row["center"], row["score"], row["visual_label"],
                                     row.get("review_score", ""), row.get("review_candidate", "")])
            report = _tile_report(output, profile, raw, rows, example=example)
        progress("ekspor", 1, 1)
        report["files"] = _catalog(output)
        _write(output / "report.json", report)
        return report
