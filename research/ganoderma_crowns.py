"""A fixed-block detection experiment on the public Ganoderma V2 RGB data."""

import argparse
from collections import defaultdict
from dataclasses import replace
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace

from PIL import Image, ImageStat
import torch

from crowntrace.benchmark import summarize
from crowntrace.compact import CompactDetector
from crowntrace.dataset import ROOT
from crowntrace.external_benchmark import resolve_images, SOURCE_LABELS
from crowntrace.inference import Detection, fuse
from crowntrace.train_coco_crowns import Tile, train_detector

COUNTS = {"train": (1128, 8001), "validation": (106, 759), "test": (118, 640)}
SCALES = (1., 1.5, 2., 3., 4.)
THRESHOLDS = (.15, .20, .25, .30, .35, .40, .45, .50, .55, .60)
MODELS = {
    "mopad": ROOT / "results/expanded_train/best.pth",
    "trees": ROOT / "results/contour_20261125/selected/detector.pth",
    "peru": ROOT / "results/layers_20261126/peru_transfer/detector.pth",
}


def load_tiles(root, split):
    source = json.loads((root / "annotations" / f"instances_{split}.json").read_text(encoding="utf-8-sig"))
    if (len(source["images"]), len(source["annotations"])) != COUNTS[split]:
        raise ValueError(f"Unexpected official {split} partition size")
    categories = {row["id"]: row["name"] for row in source["categories"]}
    if set(categories.values()) != SOURCE_LABELS:
        raise ValueError("Unexpected official categories")
    paths = resolve_images(root / "images" / split, source["images"])
    objects = defaultdict(list)
    for ann in source["annotations"]:
        x, y, w, h = ann["bbox"]
        box = (max(0, x), max(0, y), min(640, x+w), min(640, y+h))
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError(f"Invalid box {ann['id']}")
        truth = Detection(box, categories[ann["category_id"]], 1.)
        objects[ann["image_id"]].append(truth)
    return [Tile(paths[row["file_name"]], split, [(d, None) for d in objects[row["id"]]])
            for row in source["images"]]


def single_class(tiles):
    return [Tile(t.path, t.group, [(replace(d, label="palm_crown"), points) for d, points in t.regions]) for t in tiles]


def predict_padded(detector, image, factor):
    width, height = image.size
    nw, nh = round(width * factor), round(height * factor)
    x, y = (nw-width)//2, (nh-height)//2
    source = image
    if factor != 1:
        source = Image.new("RGB", (nw, nh), tuple(round(c) for c in ImageStat.Stat(image).mean))
        source.paste(image, (x, y))
    proposals = []
    for d in detector.predict(source, 1024):
        cx, cy = d.center
        if not (x <= cx < x+width and y <= cy < y+height):
            continue
        a, b, c, e = d.box
        box = (max(0, a-x), max(0, b-y), min(width, c-x), min(height, e-y))
        if box[0] < box[2] and box[1] < box[3]:
            proposals.append(Detection(box, "palm_crown", d.score))
    return fuse(proposals, center_ratio=0)


def predict_records(model, tiles, scale):
    rows = []
    for tile in tiles:
        with Image.open(tile.path) as photo:
            image = photo.convert("RGB")
        started = perf_counter()
        predictions = predict_padded(model, image, scale)
        rows.append((tile.path.name, [d for d, _ in tile.regions], predictions,
                     {"elapsed_ms": (perf_counter()-started)*1000, "calls": 1}))
    return rows


def calibrate(rows):
    # Threshold search affects the operating point, while AP uses the same full ranked proposals.
    candidates = []
    from crowntrace.benchmark import operating_matches
    for cutoff in THRESHOLDS:
        tp, fp, fn, count_error = 0, 0, 0, 0
        for _, known, proposals, _ in rows:
            kept = [item for item in proposals if item.score >= cutoff]
            a, b, c = operating_matches(known, kept)
            tp, fp, fn = tp+a, fp+b, fn+c
            count_error += abs(len(known)-len(kept))
        candidates.append((2*tp/max(1, 2*tp+fp+fn), -count_error, cutoff))
    _, _, chosen = max(candidates)
    return summarize(rows, chosen, labels=())


def short(stats):
    return {k: stats[k] for k in ("crown_ap50", "crown_ap50_95", "count_mae", "operating_threshold", "operating_point")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    torch.manual_seed(2026)
    args.output.mkdir(parents=True, exist_ok=False)
    train = single_class(load_tiles(args.root, "train"))
    val = single_class(load_tiles(args.root, "validation"))
    validation = {}
    for name, path in MODELS.items():
        model = CompactDetector(path)
        validation[name] = {}
        for scale in SCALES:
            stats = calibrate(predict_records(model, val, scale))
            validation[name][scale] = stats
            print("validation", name, scale, short(stats), flush=True)
    # All frozen configurations are compared on validation before official test access.
    def quality(item):
        return (item[2]["crown_ap50_95"], item[2]["operating_point"]["f1_50"], -item[2]["count_mae"])
    selected = max(((name, scale, stats) for name, variants in validation.items() for scale, stats in variants.items()), key=quality)
    warm = max(((name, scale, stats) for name in ("trees", "peru") for scale, stats in validation[name].items()), key=quality)
    state = {"source": "10.17632/s23jvbpnr3.2", "selection": "validation AP50:95, matching F1, then count MAE",
             "support": COUNTS, "train_reference_median_box_width": median(d.box[2]-d.box[0] for t in train for d, _ in t.regions),
             "validation_frozen": validation, "selected_frozen": selected[:2], "warm_start": str(MODELS[warm[0]])}
    # Preserve frozen comparison as a checkpoint if the longer training is interrupted.
    (args.output / "validation.json").write_text(json.dumps(state, indent=2)+"\n", encoding="utf-8")
    training = train_detector(train, val, SimpleNamespace(
        size=args.size, architecture="multiscale", warm_start=MODELS[warm[0]], lr=.0013,
        detector_epochs=args.epochs, geometric_augment=True, overlap_weight=.85, output=args.output),
        torch.Generator().manual_seed(2026))
    trained = CompactDetector(args.output / "detector.pth")
    trained_validation = calibrate(predict_records(trained, val, 1.))
    test = single_class(load_tiles(args.root, "test"))
    tests = {}
    for name, model, scale, threshold in (
        ("mopad_unscaled", CompactDetector(MODELS["mopad"]), 1., .40),
        ("selected_frozen", CompactDetector(MODELS[selected[0]]), selected[1], selected[2]["operating_threshold"]),
        ("trained_ganoderma", trained, 1., trained_validation["operating_threshold"]),
    ):
        rows = predict_records(model, test, scale)
        stats = summarize(rows, threshold, labels=())
        tests[name] = {**stats, "scale": scale, "model": model.version}
        print("test", name, short(stats), flush=True)
    state.update(training=training, validation_trained=trained_validation, test=tests)
    (args.output / "summary.json").write_text(json.dumps(state, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
