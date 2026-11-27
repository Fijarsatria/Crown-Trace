"""Compare fine-tuned multiscale weights and their ensemble on the fixed MOPAD split."""
import argparse
from dataclasses import replace
from itertools import chain
import csv
import json
from pathlib import Path
from statistics import mean
from time import perf_counter

import numpy as np
from PIL import Image

from .benchmark import iou_matrix, summarize
from .dataset import DATA, annotations, audit, manifest
from .inference import Detection, fuse, infer

THRESHOLDS = tuple(step / 100 for step in range(5, 96))


def combine(primary, secondary, min_iou=0.45):
    """Pair boxes one to one and retain single-model detections at a lower score."""
    overlaps = iou_matrix(primary, secondary)
    left, right = np.nonzero(overlaps >= min_iou)
    ranked = sorted(zip(left, right), key=lambda pair: (-overlaps[pair], pair))
    used_primary, used_secondary = set(), set()
    merged = []
    for a, b in ranked:
        if a in used_primary or b in used_secondary:
            continue
        used_primary.add(a)
        used_secondary.add(b)
        first, second = primary[a], secondary[b]
        total = first.score + second.score
        box = tuple((x * first.score + y * second.score) / total for x, y in zip(first.box, second.box))
        best = first if first.score >= second.score else second
        merged.append(Detection(box, best.label, total / 2))
    single_view = chain((det for index, det in enumerate(primary) if index not in used_primary),
                        (det for index, det in enumerate(secondary) if index not in used_secondary))
    merged.extend(replace(det, score=det.score * 0.90) for det in single_view)
    return fuse((det for det in merged if det.score >= 0.05), center_ratio=0)


def review_priority(boxes):
    uncertain = sum(0.30 <= det.score < 0.52 for det in boxes)
    confident = sum(det.score >= 0.52 for det in boxes)
    return uncertain / max(1, uncertain + confident)


def selective_predict(image, primary, secondary, cutoff, threshold):
    """Use the calibrated second pass only when the first model's boxes call for it."""
    started = perf_counter()
    boxes, _ = infer(image, primary, "full_1024", threshold=0.05)
    review = review_priority(boxes) >= cutoff
    if review:
        secondary_boxes, _ = infer(image, secondary, "full_1024", threshold=0.05)
        boxes = combine(boxes, secondary_boxes)
    return [det for det in boxes if det.score >= threshold], {
        "elapsed_ms": (perf_counter() - started) * 1000,
        "calls": 1 + int(review), "tiles": [], "threshold": threshold,
    }


def calibrate(rows):
    losses = {threshold: mean(abs(sum(d.score >= threshold for d in preds) - len(truth))
                              for _, truth, preds, _ in rows) for threshold in THRESHOLDS}
    return min(losses, key=lambda t: (losses[t], -t)), losses


def evaluate(data, images, primary, secondary, partition):
    strategies = {name: [] for name in ("baseline_416", "multiscale_416", "ensemble_416", "exhaustive_baseline")}
    priority = []
    for index, sample in enumerate(row for row in data["samples"] if row["split"] == partition):
        name = sample["file_name"]
        with Image.open(images / name) as source:
            image = source.convert("RGB")
        if index == 0:
            for model in (primary, secondary):
                model.predict(image, 640)
                model.predict(image, 1024)
        truth = list(annotations(sample, data["categories"], image.size))
        models = [(primary, "baseline_416", "full_1024"),
                  (secondary, "multiscale_416", "full_1024"),
                  (primary, "exhaustive_baseline", "exhaustive")]
        offset = index % len(models)
        for model, label, strategy in models[offset:] + models[:offset]:
            preds, stats = infer(image, model, strategy, threshold=0.05)
            strategies[label].append((name, truth, preds, stats))
        base = strategies["baseline_416"][-1]
        candidate = strategies["multiscale_416"][-1]
        started = perf_counter()
        priority.append(review_priority(base[2]))
        combined = combine(base[2], candidate[2])
        merge_ms = (perf_counter() - started) * 1000
        strategies["ensemble_416"].append((name, truth, combined, {
            "elapsed_ms": base[3]["elapsed_ms"] + candidate[3]["elapsed_ms"] + merge_ms, "calls": 2}))
    if not priority:
        raise ValueError(f"Missing {partition} images")
    print(f"{partition}: inferred {len(priority)} images", flush=True)
    return strategies, priority


def add_selective(strategies, priority, cutoff):
    result = []
    for score, base, full in zip(priority, strategies["baseline_416"], strategies["ensemble_416"], strict=True):
        # Retiming the lightweight routing decision avoids charging the second model on skipped images.
        chosen = full if score >= cutoff else base
        if chosen is base:
            started = perf_counter()
            review_priority(base[2])
            elapsed = base[3]["elapsed_ms"] + (perf_counter() - started) * 1000
            chosen = (*base[:3], {**base[3], "elapsed_ms": elapsed})
        result.append(chosen)
    strategies["selective_416"] = result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DATA / "mopad_site2_val_manifest.json")
    parser.add_argument("--images", type=Path, default=DATA / "mopad_site2_val_images")
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--secondary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.threads < 1 or args.output.exists():
        parser.error("Use a positive thread count and a new output directory.")
    import torch
    from .compact import CompactDetector
    torch.set_num_threads(args.threads)
    data = manifest(args.manifest)
    audited = audit(data, args.images)
    primary, secondary = CompactDetector(args.primary), CompactDetector(args.secondary)
    validation, val_priority = evaluate(data, args.images, primary, secondary, "val")
    cutoff = float(np.quantile(val_priority, 0.75))
    add_selective(validation, val_priority, cutoff)
    selected = {name: calibrate(rows) for name, rows in validation.items()}
    print("Frozen on validation: " + ", ".join(f"{method}={values[0]:.2f}" for method, values in selected.items()),
          flush=True)
    test, test_priority = evaluate(data, args.images, primary, secondary, "test")
    add_selective(test, test_priority, cutoff)
    result = {"training_scope": "single MOPAD Site 2 training prefix", "threads": args.threads,
              "torch": torch.__version__, "validation_rule": "per-method threshold minimizing validation count MAE",
              "primary": primary.version, "secondary": secondary.version,
              "groups": audited["groups"], "review_cutoff_val_75th_percentile": cutoff,
              "review_fraction": {"val": mean(score >= cutoff for score in val_priority),
                                  "test": mean(score >= cutoff for score in test_priority)},
              "threshold_grid": {name: values[1] for name, values in selected.items()}, "val": {}, "test": {}}
    for partition, strategies in (("val", validation), ("test", test)):
        for name, rows in strategies.items():
            result[partition][name] = summarize(rows, selected[name][0])
        print(f"{partition}: calculated count and detection metrics", flush=True)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (args.output / "per_image.csv").open("w", newline="", encoding="utf-8") as target:
        output = csv.writer(target)
        output.writerow(["partition", "strategy", "image", "reference", "detections", "absolute_error", "calls", "ms"])
        for partition, strategies in (("val", validation), ("test", test)):
            for name, rows in strategies.items():
                threshold = selected[name][0]
                for file, truth, preds, stats in rows:
                    count = sum(det.score >= threshold for det in preds)
                    output.writerow([partition, name, file, len(truth), count, abs(count - len(truth)),
                                     stats["calls"], round(stats["elapsed_ms"], 3)])
    for name, row in result["test"].items():
        print(f"{name}: MAE {row['count_mae']:.2f}, AP50 {row['crown_ap50']:.3f}, "
              f"{row['detector_calls']:.2f} calls, {row['median_ms']:.1f} ms", flush=True)


if __name__ == "__main__":
    main()
