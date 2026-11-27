"""Draft train-only mask labels for human review; never generate test ground truth."""

import argparse
import csv
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image
import torch

from .dataset import DATA
from .inference import Detection
from .segment import CrownMaskPredictor
from .survey import SEGMENT_WEIGHTS


def encode_rle(mask, crop, frame_size):
    """Encode a local bool mask in full-image COCO column-major coordinates."""
    left, top, right, bottom = crop
    width, height = frame_size
    if mask.shape != (bottom - top, right - left):
        raise ValueError("Mask dimensions differ from crop")
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("Crop outside frame")
    padded = np.pad(mask.astype(np.int8), ((1, 1), (0, 0)))
    changes = np.diff(padded, axis=0).T
    intervals = []
    for column, values in enumerate(changes):
        first = np.flatnonzero(values == 1)
        last = np.flatnonzero(values == -1)
        base = (left + column) * height + top
        for start, end in zip(first, last, strict=True):
            a, b = base + int(start), base + int(end)
            if intervals and intervals[-1][1] == a:
                intervals[-1][1] = b
            else:
                intervals.append([a, b])
    counts, end = [], 0
    for a, b in intervals:
        counts.extend((a - end, b - a))
        end = b
    counts.append(width * height - end)
    return {"size": [height, width], "counts": counts}


def prepare(source, weights, output):
    torch.set_num_threads(4)
    source = source / "train"
    coco = json.loads((source / "_annotations.coco.json").read_text(encoding="utf-8"))
    image_info, = coco["images"]
    name = image_info["file_name"]
    with Image.open(source / "images" / name) as original:
        image = original.convert("RGB")
    with Image.open(source / "valid_pixels.png") as original:
        coverage = np.asarray(original) != 0
    if coverage.shape != (image.height, image.width):
        raise ValueError("Coverage mask differs from frame")
    with (source / "box_proposals.csv").open(newline="", encoding="utf-8") as handle:
        labels = {int(row["proposal_id"]): row for row in csv.DictReader(handle)}
    predictor = CrownMaskPredictor(weights)
    detections = []
    for annotation in coco["annotations"]:
        x, y, w, h = annotation["bbox"]
        detections.append(Detection((x, y, x + w, y + h), "Palm", 1.0))
    output.mkdir(parents=True, exist_ok=False)
    (output / "images").mkdir()
    shutil.copyfile(source / "images" / name, output / "images" / name)
    shutil.copyfile(source / "valid_pixels.png", output / "valid_pixels.png")
    reviewed = 0
    generated = 0
    with (output / "review_queue.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["proposal_id", "legacy_visual_label", "x1", "y1", "x2", "y2",
                                                     "cx", "cy", "draft_mask_pixels", "review_status"])
        writer.writeheader()
        for annotation, (crop, prediction) in zip(coco["annotations"], predictor.masks(image, detections), strict=True):
            left, top, right, bottom = crop
            mask = np.array(Image.fromarray(prediction.astype(np.uint8) * 255).resize(
                (right - left, bottom - top), Image.Resampling.NEAREST)) != 0
            mask &= coverage[top:bottom, left:right]
            area = int(mask.sum())
            annotation["proposal_bbox"] = annotation["bbox"][:]
            annotation["review_status"] = "pending"
            annotation["label_source"] = predictor.version
            if area:
                ys, xs = np.nonzero(mask)
                x0, y0, x1, y1 = int(xs.min()) + left, int(ys.min()) + top, int(xs.max()) + left + 1, int(ys.max()) + top + 1
                annotation["bbox"] = [x0, y0, x1 - x0, y1 - y0]
                annotation["segmentation"] = encode_rle(mask, crop, image.size)
                annotation["area"] = area
                generated += 1
            writer.writerow({**labels[annotation["id"]], "draft_mask_pixels": area, "review_status": "pending"})
    coco["info"]["description"] = "MODEL DRAFTS FOR TRAIN-ONLY ANNOTATION REVIEW; NO GROUND TRUTH"
    (output / "_annotations.coco.json").write_text(json.dumps(coco, separators=(",", ":")) + "\n", encoding="utf-8")
    summary = {"split": "train", "source_frame": image_info["source_group"], "teacher": predictor.version,
               "box_proposals": len(detections), "generated_masks": generated, "human_reviewed_masks": reviewed,
               "role": "annotation drafts, review every object and search for missing crowns; never use as test labels"}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DATA / "annotation_mopad_v1")
    parser.add_argument("--weights", type=Path, default=SEGMENT_WEIGHTS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.weights, args.output)


if __name__ == "__main__":
    main()
