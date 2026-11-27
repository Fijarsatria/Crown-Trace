"""Evaluate and adapt the existing crown detector on the grouped Peru boxes."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from PIL import Image
import torch

from crowntrace.benchmark import summarize
from crowntrace.compact import CompactDetector
from crowntrace.inference import Detection, fuse
from crowntrace.photo import profile
from crowntrace.train_coco_crowns import TASK_CLASSES, Tile, read_split, train_detector


def boxes(data, partition):
    folder = data / partition
    coco = json.loads((folder / "_annotations.coco.json").read_text(encoding="utf-8"))
    by_image = defaultdict(list)
    for item in coco["annotations"]:
        x, y, width, height = item["bbox"]
        by_image[item["image_id"]].append((Detection((x, y, x+width, y+height), TASK_CLASSES[0], 1.), None))
    return [Tile(folder / image["file_name"], image["source_stem"], by_image[image["id"]]) for image in coco["images"]]


def predictions(detector, images):
    records = []
    for tile in images:
        with Image.open(tile.path) as source:
            image = source.convert("RGB")
        start = perf_counter()
        output = fuse(detector.predict(image, 1024), center_ratio=0)
        timing = {"elapsed_ms": (perf_counter() - start) * 1000, "calls": 1}
        records.append((tile.path.name, [item[0] for item in tile.regions], output, timing))
    return records


def calibrate(records):
    thresholds = (.15, .2, .25, .3, .35, .4, .45, .5, .55, .6)
    results = {value: summarize(records, value, labels=()) for value in thresholds}
    cutoff = min(thresholds, key=lambda value: (results[value]["count_mae"],
                                               -results[value]["operating_point"]["f1_50"]))
    return cutoff, results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--size", type=int, default=416)
    parser.add_argument("--include-roboflow", type=Path, help="Also train on and compare the grouped Trees dataset")
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.manual_seed(2026)
    base, _, _, default_threshold = profile("roboflow")
    train, valid = (boxes(args.data, split) for split in ("train", "valid"))
    trees_train, trees_val = [], []
    if args.include_roboflow:
        trees_train, trees_val = (read_split(args.include_roboflow, split) for split in ("train", "val"))
    args.output.mkdir(parents=True, exist_ok=False)
    training = train_detector(train + trees_train, valid + trees_val, SimpleNamespace(
        size=args.size, architecture="multiscale", warm_start=base, lr=.0012, detector_epochs=args.epochs,
        geometric_augment=True, overlap_weight=.75, output=args.output), torch.Generator().manual_seed(2026))
    tuned = CompactDetector(args.output / training["checkpoint"])
    cutoff, threshold_results = calibrate(predictions(tuned, valid))
    test = boxes(args.data, "test")
    baseline = CompactDetector(base)
    original = summarize(predictions(baseline, test), default_threshold, labels=())
    baseline.input_size = args.size
    same_size = summarize(predictions(baseline, test), default_threshold, labels=())
    adapted = summarize(predictions(tuned, test), cutoff, labels=())
    result = {"task": "class-agnostic crown boxes, no condition classification",
              "grouping": "no shared original filename stems or exact file hash across selected photos",
              "geographic_independence_verified": False, "training": training,
              "train_images": {"peru": len(train), "roboflow": len(trees_train)},
              "threshold_selection": "validation count MAE, then F1 at IoU 0.50", "validation": threshold_results,
              "test": {"original_roboflow": original, "original_same_size": same_size, "peru_adapted": adapted}}
    if args.include_roboflow:
        holdout = read_split(args.include_roboflow, "test")
        trees_cutoff, trees_thresholds = calibrate(predictions(tuned, trees_val))
        result["trees_validation"] = trees_thresholds
        result["trees_test"] = {
            "original_roboflow": summarize(predictions(CompactDetector(base), holdout), default_threshold, labels=()),
            "adapted": summarize(predictions(tuned, holdout), trees_cutoff, labels=()),
        }
    (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["test"], indent=2))


if __name__ == "__main__":
    main()
