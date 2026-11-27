"""Measure tile-wide crown detection and masks against grouped COCO polygons."""

import argparse
import csv
from dataclasses import dataclass
from itertools import product
import json
import math
from pathlib import Path
from statistics import mean, median
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
from PIL import Image, ImageDraw
import torch

from .benchmark import accept_match, iou_matrix, ranked_ap
from .compact import CompactDetector
from .inference import MirrorEnsemble, fuse
from .segment import CrownMaskPredictor, SIZE, crop_box
from .train_coco_crowns import digest, mask_inputs, mask_pixels, read_split

IOU_THRESHOLDS = np.linspace(.50, .95, 10)
MASK_METHODS = ("rectangle", "ellipse", "mean_shape", "network")
AP_MAX = 300


@dataclass
class LocalMask:
    window: tuple
    pixels: np.ndarray
    area: int

    @classmethod
    def resized(cls, window, pixels):
        x, y, right, bottom = window
        array = np.array(Image.fromarray(pixels).resize((right - x, bottom - y), Image.Resampling.NEAREST),
                         dtype=bool)
        return cls(window, array, int(array.sum()))


def mask_iou(first, second):
    x, y, right, bottom = first.window
    u, v, end_x, end_y = second.window
    left, top, width_end, height_end = max(x, u), max(y, v), min(right, end_x), min(bottom, end_y)
    if width_end <= left or height_end <= top or not first.area or not second.area:
        return 0.
    a = first.pixels[top-y:height_end-y, left-x:width_end-x]
    b = second.pixels[top-v:height_end-v, left-u:width_end-u]
    intersection = np.count_nonzero(a & b)
    return intersection / (first.area + second.area - intersection)


def bbox_window(box, image):
    x, y, right, bottom = box
    return (max(0, math.floor(x)), max(0, math.floor(y)),
            min(image.width, math.floor(right) + 1), min(image.height, math.floor(bottom) + 1))


def shape_baselines(root, validation):
    _, training = mask_inputs(read_split(root, "train"))
    _, targets = mask_inputs(validation)
    val = targets.numpy()
    area = val.sum((1, 2))

    def quality(shape):
        intersection = (val & shape).sum((1, 2))
        return float((intersection / np.maximum(1, area + shape.sum() - intersection)).mean())

    axis = (np.arange(SIZE) + .5) * 2 / SIZE - 1
    yy, xx = np.meshgrid(axis, axis, indexing="ij")
    ellipses = {}
    for rx, ry, dx, dy in product((.55, .65, .75, .85, .95), (.55, .65, .75, .85, .95),
                                   (-.075, 0., .075), (-.075, 0., .075)):
        ellipses[(rx, ry, dx, dy)] = (((xx - dx) / rx) ** 2 + ((yy - dy) / ry) ** 2 <= 1)
    ellipse_parameters = max(ellipses, key=lambda key: quality(ellipses[key]))
    frequency = training.numpy().mean(axis=0)
    cut = max((.25, .35, .45, .55, .65, .75), key=lambda cutoff: quality(frequency >= cutoff))
    return ellipses[ellipse_parameters], frequency >= cut, {"ellipse": ellipse_parameters, "mean_shape_cut": cut}


def proposals(detector, image):
    return fuse(detector.predict(image, 1024), center_ratio=0)[:AP_MAX]


def load_tiles(rows, detector):
    output = []
    for tile in rows:
        with Image.open(tile.path) as raw:
            image = raw.convert("RGB")
        start = perf_counter()
        predicted = proposals(detector, image)
        output.append((tile, image, predicted, perf_counter() - start))
    return output


def calibrate(tiles):
    candidates = {}
    for threshold in np.arange(.05, .96, .05).round(2).tolist():
        errors = [sum(d.score >= threshold for d in proposed) - len(tile.regions)
                  for tile, _, proposed, _ in tiles]
        candidates[threshold] = {"mae": mean(map(abs, errors)), "bias": mean(errors)}
    threshold = min(candidates, key=lambda t: (candidates[t]["mae"], abs(candidates[t]["bias"]), -t))
    return threshold, candidates


def predicted_masks(image, boxes, segmenter, ellipse, prior):
    values = {name: [] for name in MASK_METHODS}
    for det, (window, pixels) in zip(boxes, segmenter.masks(image, boxes), strict=True):
        rectangle = bbox_window(det.box, image)
        values["rectangle"].append(LocalMask.resized(rectangle, np.ones((1, 1), dtype=bool)))
        fixed_crop = crop_box(det.box, *image.size)
        values["ellipse"].append(LocalMask.resized(fixed_crop, ellipse))
        values["mean_shape"].append(LocalMask.resized(fixed_crop, prior))
        values["network"].append(LocalMask.resized(window, pixels))
    return values


def overlay(image, masks, rgb):
    layer = image.copy()
    for mask in masks:
        if mask.area:
            alpha = Image.fromarray(mask.pixels.astype(np.uint8) * 96)
            layer.paste(rgb, mask.window, alpha)
    return layer


def save_network_matches(output, split, rows, network_overlaps, scores):
    """Save one-to-one hit flags before pooling across image families."""
    pooled_scores, pooled_hits, offsets = [], [], [0]
    for index in range(len(rows)):
        ranks = np.argsort(-np.asarray(scores[index]), kind="stable")
        matrix = network_overlaps[index]
        outcomes = np.empty((len(ranks), len(IOU_THRESHOLDS)), dtype=bool)
        for col, cutoff in enumerate(IOU_THRESHOLDS):
            remaining = np.ones(matrix.shape[1], dtype=bool)
            outcomes[:, col] = [accept_match(matrix[prediction], remaining, cutoff) for prediction in ranks]
        pooled_scores.extend(np.asarray(scores[index])[ranks].tolist())
        pooled_hits.append(outcomes)
        offsets.append(len(pooled_scores))
    np.savez_compressed(output / f"{split}_network_matches.npz", offsets=offsets, scores=pooled_scores,
                        hits=np.concatenate(pooled_hits), images=[r["image"] for r in rows],
                        families=[r["family"] for r in rows], references=[r["reference"] for r in rows])


def evaluate(tiles, segmenter, ellipse, prior, threshold, output, split, save_matches=False):
    overlaps = {name: {} for name in ("boxes", *MASK_METHODS)}
    scores = {}
    oracle = {name: [] for name in MASK_METHODS}
    rows = []
    for index, (tile, image, predictions, detect_time) in enumerate(tiles):
        truth_boxes = [box for box, _ in tile.regions]
        truth = []
        for box, polygon in tile.regions:
            window = bbox_window(box.box, image)
            pixels = np.asarray(mask_pixels(polygon, window)) > 0
            truth.append(LocalMask(window, pixels, int(pixels.sum())))
        start = perf_counter()
        masks = predicted_masks(image, predictions, segmenter, ellipse, prior)
        seconds = detect_time + perf_counter() - start
        prompted = predicted_masks(image, truth_boxes, segmenter, ellipse, prior)
        for name in MASK_METHODS:
            oracle[name].extend(mask_iou(a, b) for a, b in zip(prompted[name], truth, strict=True))
        scores[index] = [det.score for det in predictions]
        overlaps["boxes"][index] = iou_matrix(predictions, truth_boxes)
        kept = sum(score >= threshold for score in scores[index])
        row = {"image": tile.path.name, "family": tile.group, "reference": len(truth),
               "detected": kept, "count_error": kept - len(truth),
               "nonempty_network_masks": sum(bool(mask.area) for mask in masks["network"][:kept]),
               "milliseconds": 1000 * seconds}
        for name, proposals in masks.items():
            overlaps[name][index] = np.array([[mask_iou(p, t) for t in truth] for p in proposals],
                                            dtype=float).reshape(len(proposals), len(truth))
        for name, lookup in overlaps.items():
            for iou_cutoff in (.50, .75):
                remaining = np.ones(len(truth), dtype=bool)
                row[f"{name}_tp{int(100 * iou_cutoff)}"] = sum(
                    accept_match(match, remaining, iou_cutoff) for match in lookup[index][:kept])
        if split == "test" and index in (0, len(tiles) // 2, len(tiles) - 1):
            draw_panel(image, truth, masks["network"][:kept], tile.path.name,
                       output / f"test_tile_{index+1:02d}.png")
        rows.append(row)
    support, kept = sum(r["reference"] for r in rows), sum(r["detected"] for r in rows)
    result = {"tiles": len(rows), "families": len({row["family"] for row in rows}), "reference_instances": support,
              "point_detections": kept, "count_mae_per_tile": mean(abs(r["count_error"]) for r in rows),
              "count_bias_per_tile": mean(r["count_error"] for r in rows),
              "count_wape": sum(abs(r["count_error"]) for r in rows) / support,
              "median_mask_inference_ms": median(r["milliseconds"] for r in rows),
              "oracle_box_mean_iou": {name: mean(values) for name, values in oracle.items()}, "metrics": {}}
    for name, matrices in overlaps.items():
        aps = ranked_ap(matrices, scores, IOU_THRESHOLDS)
        tp = sum(row[f"{name}_tp50"] for row in rows)
        result["metrics"][name] = {"ap50": aps[0], "ap75": aps[5], "ap50_95": mean(aps),
                                   "precision50_at_count_threshold": tp / max(1, kept),
                                   "recall50_at_count_threshold": tp / support}
    with (output / f"{split}_per_tile.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    if save_matches:
        save_network_matches(output, split, rows, overlaps["network"], scores)
    return result


def draw_panel(image, reference, predicted, label, path):
    caption_height = 42
    size = min(image.width, 460)
    height = round(size * image.height / image.width)
    panel = Image.new("RGB", (size * 3, caption_height + height), "#102219")
    draw = ImageDraw.Draw(panel)
    for i, content in enumerate((image, overlay(image, reference, (255, 186, 68)),
                                  overlay(image, predicted, (75, 248, 187)))):
        panel.paste(content.resize((size, height), Image.Resampling.BILINEAR), (i * size, caption_height))
    for i, heading in enumerate(("RGB", f"COCO reference  |  {len(reference)}",
                                  f"Predicted crowns  |  {len(predicted)}")):
        draw.text((i * size + 12, 12), heading, fill="#f5fff7", font_size=15)
    draw.text((12, 28), label.split(".rf.")[0], fill="#bfdec8")
    panel.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--detector-model", type=Path, help="Use the detector from a second verified run.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--validation-only", action="store_true")
    parser.add_argument("--mirror", action="store_true", help="Merge proposals from mirrored copies of each tile.")
    parser.add_argument("--save-matches", action="store_true", help="Save match outcomes for paired comparison.")
    parser.add_argument("--mask-threshold", type=float, help="Override the mask cutoff during validation.")
    parser.add_argument("--mask-turns", type=int, choices=(0, 1, 2, 3), nargs="+",
                        help="Average spatially aligned mask probabilities from these right-angle turns.")
    parser.add_argument("--solid-outline", action="store_true", help="Extract a single filled mask component.")
    args = parser.parse_args()
    if args.output.exists() or args.threads < 1:
        parser.error("Use a new output directory and positive thread count.")
    if args.mask_threshold is not None and not (0 < args.mask_threshold < 1):
        parser.error("--mask-threshold must be between zero and one.")
    torch.set_num_threads(args.threads)
    training = json.loads((args.model / "training.json").read_text(encoding="utf-8"))
    for split, expected in training["partition_sha256"].items():
        if digest(args.data / split / "_annotations.coco.json") != expected:
            raise ValueError(f"The {split} labels differ from the training record.")
    detector_run = args.detector_model or args.model
    detection_training = json.loads((detector_run / "training.json").read_text(encoding="utf-8"))
    if detection_training["partition_sha256"] != training["partition_sha256"]:
        raise ValueError("The detector and mask used different data partitions.")
    detect_source = detector_run / detection_training["detector"]["checkpoint"]
    mask_source = args.model / training["mask"]["checkpoint"]
    if digest(detect_source) != detection_training["detector"]["sha256"] or digest(mask_source) != training["mask"]["sha256"]:
        raise ValueError("A checkpoint differs from its training record.")
    detector = CompactDetector(detect_source)
    if args.mirror:
        detector = MirrorEnsemble(detector)
    segmenter = CrownMaskPredictor(mask_source)
    if args.mask_threshold is not None:
        segmenter.threshold = args.mask_threshold
    if args.mask_turns:
        segmenter.rotations = tuple(args.mask_turns)
    if args.solid_outline:
        segmenter.solid_outline = True
    validation = read_split(args.data, "val")
    ellipse, prior, calibration = shape_baselines(args.data, validation)
    val = load_tiles(validation, detector)
    count_threshold, grid = calibrate(val)
    print("validation selected count cutoff", count_threshold, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as staging:
        output = Path(staging) / "result"
        output.mkdir()
        results = {"scope": "Single-class tile benchmark against provided COCO annotations. Geographic provenance is unknown.",
                   "ap_protocol": "101-point interpolation, native binary mask IoU, score floor 0.05, 300 detections per image",
                   "detector": detector.version, "segmenter": segmenter.version, "count_cutoff": count_threshold,
                   "mask_settings": {"threshold": segmenter.threshold, "rotations": segmenter.rotations,
                                     "solid_outline": segmenter.solid_outline, "size": segmenter.size,
                                     "padding": segmenter.padding},
                   "count_threshold_validation_grid": grid, "shape_calibration": calibration,
                   "val": evaluate(val, segmenter, ellipse, prior, count_threshold, output, "val", args.save_matches)}
        if not args.validation_only:
            test = read_split(args.data, "test")
            development = {tile.group for tile in read_split(args.data, "train") + validation}
            if development & {tile.group for tile in test}:
                raise ValueError("A source family crosses the test boundary.")
            results["test"] = evaluate(load_tiles(test, detector), segmenter, ellipse, prior, count_threshold,
                                       output, "test", args.save_matches)
        (output / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        if args.output.exists():
            raise FileExistsError(args.output)
        output.rename(args.output)
    print(json.dumps(results["val" if args.validation_only else "test"], indent=2), flush=True)


if __name__ == "__main__":
    main()
