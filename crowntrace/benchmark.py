"""Benchmark identical checkpoints with fixed validation tuning and a held-out group."""
import argparse
from collections import Counter
import csv
from dataclasses import asdict
import json
from pathlib import Path
import platform
from statistics import mean, median

import numpy as np
from PIL import Image

from .dataset import DATA, annotations, audit, manifest
from .inference import LABELS, METHODS, YoloDetector, infer


def iou_matrix(predicted, actual):
    p = np.asarray([d.box for d in predicted], dtype=float).reshape((-1, 4))
    t = np.asarray([d.box for d in actual], dtype=float).reshape((-1, 4))
    intersection = np.maximum(0, np.minimum(p[:, None, 2:], t[None, :, 2:]) -
                              np.maximum(p[:, None, :2], t[None, :, :2])).prod(axis=2)
    union = ((p[:, 2:] - p[:, :2]).prod(axis=1)[:, None] +
             (t[:, 2:] - t[:, :2]).prod(axis=1)[None, :] - intersection)
    return np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)


def accept_match(overlaps, remaining, cutoff):
    eligible = remaining & (overlaps >= cutoff)
    if not eligible.any():
        return False
    reverse_index = np.argmax(np.where(eligible, overlaps, -1)[::-1])
    remaining[len(remaining) - 1 - reverse_index] = False
    return True


def operating_matches(actual, predicted):
    ranked = sorted(predicted, key=lambda row: -row.score)
    remaining = np.ones(len(actual), dtype=bool)
    true_positive = sum(accept_match(overlap, remaining, 0.5) for overlap in iou_matrix(ranked, actual))
    return true_positive, len(predicted) - true_positive, len(actual) - true_positive


def ranked_ap(overlaps, scores, thresholds):
    support = sum(matrix.shape[1] for matrix in overlaps.values())
    if not support:
        return [None] * len(thresholds)
    ranked = sorted(((name, index, score) for name, values in scores.items()
                     for index, score in enumerate(values)), key=lambda entry: -entry[2])
    values = []
    for cutoff in thresholds:
        remaining = {name: np.ones(matrix.shape[1], dtype=bool) for name, matrix in overlaps.items()}
        hits = [accept_match(overlaps[name][index], remaining[name], cutoff) for name, index, _ in ranked]
        tp = np.cumsum(hits)
        recall = tp / support
        precision = tp / np.arange(1, len(hits) + 1)
        values.append(float(np.mean([precision[recall >= r].max(initial=0)
                                     for r in np.linspace(0, 1, 101)])))
    return values


def mean_ap(rows, labels, thresholds):
    average = {}
    for label in labels:
        truths = {name: [d for d in true if label is None or d.label == label]
                  for name, true, *_ in rows}
        support = sum(map(len, truths.values()))
        if not support:
            continue
        predictions = {name: [d for d in predicted if label is None or d.label == label]
                       for name, _, predicted, *_ in rows}
        overlap = {name: iou_matrix(preds, truths[name]) for name, preds in predictions.items()}
        values = ranked_ap(overlap, {name: [d.score for d in preds] for name, preds in predictions.items()}, thresholds)
        average[label or "crown"] = mean(values)
    return average


def summarize(rows, threshold, labels=LABELS):
    totals, classes = [], {label: [] for label in labels}
    match_totals = Counter()
    for _, actual, proposals, *_ in rows:
        predicted = [d for d in proposals if d.score >= threshold]
        totals.append(len(predicted) - len(actual))
        tp, fp, fn = operating_matches(actual, predicted)
        match_totals.update({"tp50": tp, "fp50": fp, "fn50": fn})
        predicted_counts, actual_counts = Counter(d.label for d in predicted), Counter(d.label for d in actual)
        for label in labels:
            classes[label].append(predicted_counts[label] - actual_counts[label])
    actual_count = sum(len(row[1]) for row in rows)
    class_ap = mean_ap(rows, labels, [0.50])
    tp, fp, fn = (match_totals[k] for k in ("tp50", "fp50", "fn50"))
    return {"image_count": len(rows), "ground_truth_crowns": actual_count,
            "count_mae": mean(map(abs, totals)), "count_bias": mean(totals),
            "count_rmse": float(np.sqrt(np.mean(np.square(totals)))),
            "count_wape": sum(map(abs, totals)) / actual_count if actual_count else None,
            "count_mae_by_class": {k: mean(map(abs, v)) for k, v in classes.items()},
            "crown_ap50": mean_ap(rows, [None], [0.50])["crown"],
            "crown_ap50_95": mean_ap(rows, [None], np.linspace(0.5, 0.95, 10))["crown"],
            "condition_macro_ap50": mean(class_ap.values()) if class_ap else None,
            "condition_ap50": class_ap,
            "operating_point": {**match_totals,
                                "precision50": tp / max(1, tp + fp),
                                "recall50": tp / max(1, tp + fn),
                                "f1_50": 2 * tp / max(1, 2 * tp + fp + fn)},
            "median_ms": median(row[3]["elapsed_ms"] for row in rows),
            "p95_ms": float(np.percentile([row[3]["elapsed_ms"] for row in rows], 95)),
            "detector_calls": mean(row[3]["calls"] for row in rows),
            "prediction_floor": 0.05, "operating_threshold": threshold}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--backend", choices=("compact", "yolo"), default="compact")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--images", type=Path, default=DATA / "pilot_images")
    parser.add_argument("--manifest", type=Path, default=DATA / "pilot_manifest.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=2)
    parser.add_argument("--span", type=int, default=512)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--no-center", action="store_true", help="Disable cross-view center merge.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.backend == "compact":
        import torch
        from .compact import CompactDetector
        torch.set_num_threads(args.threads)
        detector = CompactDetector(args.weights, args.device)
    else:
        detector = YoloDetector(args.weights, args.device)
    data = manifest(args.manifest)
    audited = audit(data, args.images)
    splits = {}
    for partition in ("val", "test"):
        results = {method: [] for method in METHODS}
        for sample in data["samples"]:
            if sample["split"] != partition:
                continue
            name = sample["file_name"]
            with Image.open(args.images / name) as source:
                image = source.convert("RGB")
            if not any(results.values()):
                detector.predict(image, 640)
                detector.predict(image, 1024)
            actual = list(annotations(sample, data["categories"], image.size))
            # Rotate method order by image to distribute thermal/cache effects.
            offset = len(results[METHODS[0]]) % len(METHODS)
            for method in METHODS[offset:] + METHODS[:offset]:
                predictions, stats = infer(image, detector, method, args.budget, 0.05, args.span,
                                           args.seed + sample["original_image_id"],
                                           0 if args.no_center else 0.28)
                results[method].append((name, actual, predictions, stats))
        splits[partition] = results
        if partition == "val":
            baseline = results["full_640"]
            grid = [step / 100 for step in range(5, 91, 5)]
            calibration = {v: mean(abs(sum(d.score >= v for d in preds) - len(actual))
                                   for _, actual, preds, _ in baseline) for v in grid}
            threshold = min(grid, key=lambda v: (calibration[v], -v))
            print(f"Validation fixed threshold: {threshold:.2f}", flush=True)
    metrics = {split: {method: summarize(rows, threshold) for method, rows in results.items()}
               for split, results in splits.items()}
    payload = {"model": detector.version, "backend": args.backend, "device": args.device,
               "threads": args.threads, "machine": platform.platform(), "budget": args.budget,
               "span": args.span, "center_merge": not args.no_center, "data_groups": audited["groups"],
               "random_seed": args.seed, "threshold_grid_validation_mae": calibration,
               "metrics": metrics}
    (args.output / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (args.output / "per_image.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target)
        writer.writerow(["split", "method", "image", "true", "pred", "count_error", "passes", "ms",
                         "tile_windows", "tp50", "fp50", "fn50"])
        for split, results in splits.items():
            for method, rows in results.items():
                for name, actual, predicted, stats in rows:
                    operating = [d for d in predicted if d.score >= threshold]
                    count = len(operating)
                    writer.writerow([split, method, name, len(actual), count, count - len(actual),
                                     stats["calls"], round(stats["elapsed_ms"], 3), stats["tiles"],
                                     *operating_matches(actual, operating)])
    with (args.output / "predictions.jsonl").open("w", encoding="utf-8") as target:
        for split, results in splits.items():
            for method, rows in results.items():
                for name, _, predicted, _ in rows:
                    target.write(json.dumps({"split": split, "method": method, "image": name,
                                             "detections": [asdict(d) for d in predicted]}) + "\n")
    for method, row in metrics["test"].items():
        print(f"{method:<12} MAE={row['count_mae']:6.2f}  AP50={row['crown_ap50']:.3f}  "
              f"calls={row['detector_calls']:.1f}  median={row['median_ms']:.1f}ms")


if __name__ == "__main__":
    main()
