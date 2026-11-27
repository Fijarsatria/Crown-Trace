"""Count and delineate individual oil palms in one overhead RGB photo."""

import argparse
from collections import Counter
from dataclasses import replace
from functools import lru_cache
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageDraw, ImageOps
import torch

from .compact import CompactDetector
from .dataset import ROOT
from .inference import fuse, windows
from .prepare_annotation_drafts import encode_rle
from .segment import CrownMaskPredictor
from .survey import DETECT_WEIGHTS, SEGMENT_WEIGHTS

CROWN_RUN = ROOT / "results/contour_20261125/selected"
RESEARCH_PROFILES = {"roboflow_prompt": ROOT / "results/iteration_20261126/selected_detail",
                     "roboflow_fast": ROOT / "results/iteration_20261126/selected_fast"}
COLORS = {"Healthy": (93, 232, 150), "Yellow": (255, 213, 109), "Dead": (255, 135, 133),
          "Small": (155, 207, 252), "palm_crown": (86, 247, 190)}


def profile(name):
    if name in {"mopad", "mopad_hr"}:
        return DETECT_WEIGHTS, SEGMENT_WEIGHTS, 1024 if name == "mopad_hr" else 640, .40
    run = RESEARCH_PROFILES.get(name, CROWN_RUN)
    if name != "roboflow" and name not in RESEARCH_PROFILES:
        raise ValueError("Unknown photo model profile.")
    if not (run / "training.json").is_file():
        raise ValueError("Research model is not available.")
    training = json.loads((run / "training.json").read_text(encoding="utf-8"))
    return (run / training["detector"]["checkpoint"], run / training["mask"]["checkpoint"],
            1024, training["selection"]["selected"]["count_cutoff"])


@lru_cache(maxsize=4)
def _models(weights, mask_weights):
    return CompactDetector(weights), CrownMaskPredictor(mask_weights)


def analyze(path, destination, profile_name, *, large_photo=False):
    weights, mask_weights, input_mode, threshold = profile(profile_name)
    detector, segmenter = _models(weights, mask_weights)
    with Image.open(path) as handle:
        if handle.format not in ("JPEG", "PNG") or handle.width * handle.height > (25_000_000 if large_photo else 9_000_000):
            raise ValueError("Gunakan foto udara JPEG/PNG dalam batas ukuran halaman.")
        image = ImageOps.exif_transpose(handle).convert("RGB")
    if large_photo and profile_name.startswith("mopad") and max(image.size)>1536:
        detections=[]
        for view, rect in enumerate(windows(*image.size,1024,.20),1):
            x,y,_,_=rect
            for d in detector.predict(image.crop(rect),1024):
                if d.score >= threshold:
                    a,b,c,e=d.box
                    detections.append(replace(d,box=(a+x,b+y,c+x,e+y),view=view))
        candidates=fuse(detections, center_ratio=.28)
    else:
        candidates = fuse(detector.predict(image, input_mode), center_ratio=0)
    boxes = [d for d in candidates if d.score >= threshold]
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    rows, annotations = [], []
    palette = {label: index + 1 for index, label in enumerate(detector.labels)}
    for index, (det, (window, predicted)) in enumerate(zip(boxes, segmenter.masks(image, boxes), strict=True), 1):
        x, y, right, bottom = window
        mask = Image.fromarray(predicted).resize((right - x, bottom - y), Image.Resampling.NEAREST)
        binary = np.asarray(mask, dtype=bool)
        area = int(binary.sum())
        color = COLORS[det.label]
        if area:
            annotated.paste(color, window, Image.fromarray(binary.astype(np.uint8) * 112))
            ys, xs = np.nonzero(binary)
            left, top = int(xs.min()), int(ys.min())
            coco_bbox = [x + left, y + top, int(xs.max()) + 1 - left, int(ys.max()) + 1 - top]
            annotations.append({"id": index, "image_id": 1, "category_id": palette[det.label],
                                "segmentation": encode_rle(binary, window, image.size), "area": area,
                                "bbox": coco_bbox, "iscrowd": 0, "score": det.score})
        cx, cy = det.center
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=color, outline="#133323")
        rows.append({"id": index, "label": det.label, "score": round(det.score, 5),
                     "x_px": round(cx, 2), "y_px": round(cy, 2), "mask_px": area})
    counts = Counter(row["label"] for row in rows)
    result = {"source": path.name, "coordinate_unit": "pixel", "width": image.width, "height": image.height,
              "profile": profile_name, "detector": detector.version, "segmenter": segmenter.version,
              "score_threshold": threshold, "trees": len(rows), "crowns": len(annotations),
              "counts": {label: counts[label] for label in detector.labels}}
    coco = {"images": [{"id": 1, "file_name": path.name, "width": image.width, "height": image.height}],
            "categories": [{"id": i, "name": name} for name, i in palette.items()], "annotations": annotations}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as stage:
        folder = Path(stage) / "result"
        folder.mkdir()
        annotated.save(folder / "overlay.png")
        (folder / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (folder / "crowns.coco.json").write_text(json.dumps(coco) + "\n", encoding="utf-8")
        with (folder / "trees.csv").open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=("id", "label", "score", "x_px", "y_px", "mask_px"))
            writer.writeheader()
            writer.writerows(rows)
        if destination.exists():
            raise FileExistsError(destination)
        folder.rename(destination)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("mopad", "mopad_hr", "roboflow", "roboflow_prompt", "roboflow_fast"), default="mopad")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Pilih folder keluaran baru.")
    torch.set_num_threads(4)
    result = analyze(args.image, args.output, args.profile)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
