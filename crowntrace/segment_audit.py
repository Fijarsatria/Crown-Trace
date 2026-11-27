"""Audit contour labels and measure where the segmentation pilot breaks."""
import argparse
from collections import Counter, defaultdict
import csv
from itertools import product
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import torch

from .audit_overlap import offsets
from .compact import CompactDetector
from .dataset import DATA, ROOT, manifest
from .inference import Detection, infer, iou
from .segment import CrownMaskPredictor, SIZE, read_labels

SEGMENT = ROOT / "results/segment_pilot/best.pth"
DETECTOR = ROOT / "results/expanded_train/best.pth"
SPLITS = ("train", "val", "test")


def score(mask, truth):
    union = np.count_nonzero(mask | truth)
    return np.count_nonzero(mask & truth) / union if union else 0.0


def raster_mask(row):
    x, y, right, bottom = row["crop"]
    width, height = right - x, bottom - y
    mask = Image.new("1", (width, height))
    ImageDraw.Draw(mask).polygon([(px * width / 128, py * height / 128)
                                  for px, py in row["polygon_128"]], fill=1)
    return np.asarray(mask)


def compare(truth, rect, prediction, window):
    # Register both masks at their original pixel offsets, including any false spill outside the gold crop.
    left, top = min(rect[0], window[0]), min(rect[1], window[1])
    right, bottom = max(rect[2], window[2]), max(rect[3], window[3])
    regions = []
    for coords, mask in ((rect, truth), (window, prediction)):
        ax, ay, bx, by = coords
        resized = Image.fromarray(mask).resize((bx-ax, by-ay), Image.Resampling.NEAREST)
        region = np.zeros((bottom-top, right-left), dtype=bool)
        region[ay-top:by-top, ax-left:bx-left] = np.asarray(resized, dtype=bool)
        regions.append(region)
    return score(*regions)


def best_baselines(examples):
    training = np.stack([mask.numpy().astype(bool) for _, mask in examples["train"]])
    validation = [mask.numpy().astype(bool) for _, mask in examples["val"]]
    axis = (np.arange(SIZE) + .5) / SIZE * 2 - 1
    rows, cols = np.meshgrid(axis, axis, indexing="ij")
    best_ellipse = (-1., None, None)
    for xr, yr, x0, y0 in product(np.arange(.55, .96, .05), np.arange(.55, .96, .05),
                                  (-0.075, 0., 0.075), (-0.075, 0., 0.075)):
        estimate = ((cols - x0) / xr)**2 + ((rows - y0) / yr)**2 <= 1
        value = np.mean([score(estimate, mask) for mask in validation])
        if value > best_ellipse[0]:
            best_ellipse = (value, estimate, [round(float(v), 4) for v in (xr, yr, x0, y0)])
    mean = training.mean(axis=0)
    best_prior = (-1., None, None)
    for threshold in (.25, .35, .45, .5, .55, .65, .75):
        estimate = mean >= threshold
        value = np.mean([score(estimate, mask) for mask in validation])
        if value > best_prior[0]:
            best_prior = (value, estimate, threshold)
    return best_ellipse, best_prior


def metadata(rows, data):
    traced_boxes = defaultdict(set)
    lookup = {row["file_name"]: row for row in data["samples"]}
    intersections = []
    domains = defaultdict(list)
    classes = {split: Counter() for split in SPLITS}
    for index, row in enumerate(rows):
        image = lookup[row["image"]]
        if image["split"] != row["split"] or image["group"] != row["group"]:
            raise ValueError("Kelompok dan partisi kontur berbeda dari manifest MOPAD.")
        box = row["box"]
        category, = [int(cat) for cat, x, y, w, h in image["objects"] if [x, y, x+w, y+h] == box]
        traced_boxes[row["image"]].add(tuple(box))
        classes[row["split"]][data["categories"][str(category)]] += 1
        dx, dy = offsets(row["image"])
        placed = (box[0]+dx, box[1]+dy, box[2]+dx, box[3]+dy)
        for previous, other in domains[row["group"]]:
            if iou(placed, other) >= 0.5:
                intersections.append((previous + 1, index + 1))
        domains[row["group"]].append((index, placed))
    counts = {}
    coverage = {}
    for split in SPLITS:
        images = [image for image in data["samples"] if image["split"] == split]
        traced = [image for image in images if image["file_name"] in traced_boxes]
        traced_count = sum(len(traced_boxes[image["file_name"]]) for image in traced)
        traced_image_boxes = sum(len(image["objects"]) for image in traced)
        counts[split] = {
            "trace_rows": sum(classes[split].values()),
            "images": len(traced),
            "classes": dict(classes[split]),
            "groups": sorted({image["group"] for image in traced}),
        }
        coverage[split] = {
            "images_in_manifest": len(images),
            "images_with_traces": len(traced),
            "images_with_all_manifest_boxes_traced": sum(
                len(traced_boxes[image["file_name"]]) == len(image["objects"]) for image in traced
            ),
            "distinct_image_boxes_traced": traced_count,
            "manifest_boxes_in_traced_images": traced_image_boxes,
            "traced_pct_of_manifest_boxes_in_traced_images": (
                round(100 * traced_count / traced_image_boxes, 3) if traced_image_boxes else None
            ),
        }
    return counts, intersections, coverage


def detected_prompts(rows, detections):
    candidates = sorted(((iou(row["box"], det.box), i, j)
                         for i, row in enumerate(rows) for j, det in enumerate(detections)), reverse=True)
    assignments = {}
    occupied = set()
    for amount, i, j in candidates:
        if amount < 0.5:
            break
        if i not in assignments and j not in occupied:
            assignments[i] = detections[j], amount
            occupied.add(j)
    return assignments


def audit(args):
    torch.set_num_threads(4)
    rows = json.loads(args.labels.read_text(encoding="utf-8"))["samples"]
    examples = read_labels(args.labels, args.images)
    evidence = manifest(args.manifest)
    groups, repeats, coverage = metadata(rows, evidence)
    (_, ellipse, ellipse_params), (prior_val, prior_mask, prior_threshold) = best_baselines(examples)
    predictor = CrownMaskPredictor(args.segment)
    detector = CompactDetector(args.detector)
    results = {}
    object_rows = []
    for split in SPLITS[1:]:
        by_image = defaultdict(list)
        for row in rows:
            if row["split"] == split:
                by_image[row["image"]].append(row)
        records = []
        for name, labels in by_image.items():
            with Image.open(args.images / name) as file:
                image = file.convert("RGB")
            detections, _ = infer(image, detector, method="full_1024", threshold=0.4)
            assignments = detected_prompts(labels, detections)
            for index, row in enumerate(labels):
                gold = raster_mask(row)
                rect = row["crop"]
                trusted_box = Detection(tuple(row["box"]), "Palm", 1.)
                (input_crop, actual), = predictor.masks(image, [trusted_box])
                data = {"split": split, "image": name, "box": row["box"],
                        "oracle_box_iou": compare(gold, rect, actual, input_crop),
                        "ellipse_iou": compare(gold, rect, ellipse, input_crop),
                        "shape_prior_iou": compare(gold, rect, prior_mask, input_crop),
                        "detector_box_iou": 0., "detector_prompt_iou": 0.,
                        "detector_ellipse_iou": 0., "detector_prior_iou": 0.}
                if index in assignments:
                    det, amount = assignments[index]
                    (window, mask), = predictor.masks(image, [det])
                    data["detector_box_iou"] = amount
                    data["detector_prompt_iou"] = compare(gold, rect, mask, window)
                    data["detector_ellipse_iou"] = compare(gold, rect, ellipse, window)
                    data["detector_prior_iou"] = compare(gold, rect, prior_mask, window)
                records.append(data)
        count = len(records)
        matched = [row["detector_prompt_iou"] for row in records if row["detector_box_iou"]]
        results[split] = {
            "traced_targets": count, "detector_prompt_matches": len(matched),
            "detector_prompt_match_rate": round(len(matched)/count, 5),
            **{key: round(float(np.mean([row[key] for row in records])), 5)
               for key in ("oracle_box_iou", "ellipse_iou", "shape_prior_iou", "detector_prompt_iou",
                           "detector_ellipse_iou", "detector_prior_iou")},
            "detector_prompt_iou_matched_only": round(float(np.mean(matched)), 5) if matched else None,
        }
        object_rows.extend(records)
    report = {
        "label_provenance": "Approximate contours traced from RGB crops. One tracing pass. No second reviewer.",
        "coverage": "Selected object crops only; surrounding objects in the images are not exhaustively outlined.",
        "geometry_units": "Parent-image pixels, without field-confirmed spatial reference.",
        "rasterization": "Rasterized contour and predictions aligned at native crop pixels before binary IoU.",
        "label_counts_by_split": groups,
        "manifest_box_coverage_by_split": coverage,
        "possible_repeat_pairs_within_groups_iou_ge_0_50": repeats,
        "mopad_box_occurrences_by_split": {split: sum(len(row["objects"]) for row in evidence["samples"]
                                                    if row["split"] == split) for split in SPLITS},
        "calibration": {"ellipse_xy_radii_xy_offset": ellipse_params, "shape_prior_threshold": prior_threshold,
                        "shape_prior_validation_iou_64px": round(float(prior_val), 5)},
        "detector_prompt_protocol": "At most one predicted box per traced reference; box IoU >= 0.50. "
                                    "Missed target IoU = 0. Metric cannot measure false positives in incompletely labeled images.",
        "checkpoint": predictor.version,
        "results": results,
    }
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with (args.output / "objects.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(object_rows[0]))
        writer.writeheader()
        writer.writerows(object_rows)
    print(json.dumps(report, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DATA / "crown_masks_mopad.json")
    parser.add_argument("--images", type=Path, default=DATA / "mopad_site2_val_images")
    parser.add_argument("--manifest", type=Path, default=DATA / "mopad_site2_val_manifest.json")
    parser.add_argument("--segment", type=Path, default=SEGMENT)
    parser.add_argument("--detector", type=Path, default=DETECTOR)
    parser.add_argument("--output", type=Path, required=True)
    audit(parser.parse_args())


if __name__ == "__main__":
    main()
