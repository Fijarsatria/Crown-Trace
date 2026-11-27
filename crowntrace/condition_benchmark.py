"""Train matched RGB and mask-guided crop classifiers on fixed MOPAD groups."""

import argparse
from collections import defaultdict
import csv
from hashlib import file_digest
import json
from pathlib import Path
from statistics import mean, stdev

from PIL import Image
import torch
from torch import nn
from torch.nn import functional as F

from .dataset import DATA
from .inference import LABELS
from .segment import CrownMaskPredictor, rgb_tensor
from .survey import SEGMENT_WEIGHTS


def load_crops(root):
    rows = defaultdict(list)
    groups = {}
    with (root / "samples.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            part, group = row["split"], row["group"]
            if part not in ("train", "val", "test") or groups.setdefault(group, part) != part:
                raise ValueError("Each geographic group must use one partition")
            path = (root / row["file"]).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("Crop is outside the dataset")
            with Image.open(path) as image:
                pixels = rgb_tensor(image.convert("RGB"))
            rows[part].append((row["file"], pixels, LABELS.index(row["class"])))
    if any(not rows[part] for part in ("train", "val", "test")):
        raise ValueError("The crop dataset requires train, val and test groups")
    return {part: (names, torch.stack(images), torch.tensor(labels))
            for part, values in rows.items() for names, images, labels in [zip(*values)]}, groups


def metrics(truth, predicted):
    size = len(LABELS)
    matrix = torch.bincount(size * truth + predicted, minlength=size * size).reshape(size, size)
    per_class = {}
    for index, label in enumerate(LABELS):
        tp = int(matrix[index, index])
        actual, output = int(matrix[index].sum()), int(matrix[:, index].sum())
        per_class[label] = {"support": actual, "predicted": output, "correct": tp,
                            "precision": tp / output if output else 0.0,
                            "recall": tp / actual if actual else None,
                            "f1": 2 * tp / (actual + output) if actual else None}
    measured = [row for row in per_class.values() if row["support"]]
    return {"accuracy": int(matrix.diag().sum()) / len(truth),
            "balanced_accuracy": mean(row["recall"] for row in measured),
            "macro_f1": mean(row["f1"] for row in measured), "by_class": per_class,
            "confusion": matrix.tolist()}


def classifier():
    return nn.Sequential(nn.Conv2d(3, 12, 3, padding=1), nn.BatchNorm2d(12), nn.ReLU(), nn.MaxPool2d(2),
                         nn.Conv2d(12, 24, 3, padding=1), nn.BatchNorm2d(24), nn.ReLU(), nn.MaxPool2d(2),
                         nn.Conv2d(24, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
                         nn.AdaptiveAvgPool2d(2), nn.Flatten(), nn.Linear(128, len(LABELS)))


def fit(features, labels, seed, epochs, batch, target):
    torch.manual_seed(seed)
    generator = torch.Generator().manual_seed(seed)
    model = classifier()
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
    weights = torch.bincount(labels["train"], minlength=len(LABELS)).clamp_min(1).float().rsqrt()
    best_f1 = -1
    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        order = torch.randperm(len(labels["train"]), generator=generator)
        for positions in order.split(batch):
            images = features["train"][positions]
            flip = torch.rand(len(positions), generator=generator) < .5
            images[flip] = images[flip].flip(-1)
            optimizer.zero_grad(set_to_none=True)
            loss = F.cross_entropy(model(images), labels["train"][positions], weight=weights)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.inference_mode():
            validation = torch.cat([model(x).argmax(1) for x in features["val"].split(batch)])
        score = metrics(labels["val"], validation)["macro_f1"]
        history.append({"epoch": epoch, "validation_macro_f1": score})
        if score > best_f1:
            best_f1 = score
            torch.save(model.state_dict(), target)
    model.load_state_dict(torch.load(target, weights_only=True, map_location="cpu"))
    with torch.inference_mode():
        test = torch.cat([model(x).argmax(1) for x in features["test"].split(batch)])
    return {"seed": seed, "selected_epoch": max(history, key=lambda x: x["validation_macro_f1"])["epoch"],
            "validation_macro_f1": best_f1, "history": history,
            "test": metrics(labels["test"], test)}, test


def run(args):
    torch.set_num_threads(args.threads)
    crops, groups = load_crops(args.crops)
    labels = {part: values[2] for part, values in crops.items()}
    if any(torch.bincount(labels[part], minlength=len(LABELS)).min() == 0 for part in ("train", "val", "test")):
        raise ValueError("Every appearance class needs examples in each partition")
    predictor = CrownMaskPredictor(args.mask_weights)
    args.output.mkdir(parents=True, exist_ok=False)
    background = crops["train"][1].mean(dim=(0, 2, 3)).reshape(1, 3, 1, 1)
    inputs = {"rgb_box": {}, "predicted_mask": {}}
    empty = {}
    for part, (_, rgb, _) in crops.items():
        raw, masked = [], []
        empty[part] = 0
        for images in rgb.split(args.batch):
            masks = predictor.crop_masks(images)
            empty[part] += int((~masks.flatten(1).any(1)).sum())
            raw.append(F.avg_pool2d(images, 2))
            masked.append(F.avg_pool2d(torch.where(masks[:, None], images, background), 2))
        inputs["rgb_box"][part] = torch.cat(raw)
        inputs["predicted_mask"][part] = torch.cat(masked)
    with (args.crops / "samples.csv").open("rb") as handle:
        source_hash = file_digest(handle, "sha256").hexdigest()
    report = {"scope": "selected reference-box crops, one source site, predicted masks from pilot checkpoint",
              "group_partitions": groups, "samples_csv_sha256": source_hash, "class_order": list(LABELS),
              "mask_model": predictor.version, "epochs": args.epochs, "batch": args.batch,
              "class_weights": "inverse square root of train support", "empty_masks": empty,
              "sample_counts": {part: len(values) for part, values in labels.items()}, "methods": {}}
    with (args.output / "test_predictions.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["method", "seed", "crop", "reference", "prediction"])
        for name, features in inputs.items():
            runs = []
            for seed in args.seeds:
                item, predicted = fit(features, labels, seed, args.epochs, args.batch, args.output / f"{name}_{seed}.pth")
                runs.append(item)
                writer.writerows((name, seed, sample, LABELS[truth], LABELS[guess]) for sample, truth, guess in
                                 zip(crops["test"][0], labels["test"].tolist(), predicted.tolist(), strict=True))
                print(f"{name} seed={seed} test_macro_f1={item['test']['macro_f1']:.4f}", flush=True)
            f1 = [item["test"]["macro_f1"] for item in runs]
            report["methods"][name] = {"test_macro_f1_mean": mean(f1),
                                      "test_macro_f1_sd": stdev(f1) if len(f1) > 1 else 0.0, "runs": runs}
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crops", type=Path, default=DATA / "siit_condition_crops_v1")
    parser.add_argument("--mask-weights", type=Path, default=SEGMENT_WEIGHTS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[2026, 2027, 2028])
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if min(args.epochs, args.batch, args.threads) < 1 or len(set(args.seeds)) != len(args.seeds):
        parser.error("Use positive settings and distinct seeds")
    if not args.mask_weights.is_file():
        parser.error(f"Mask checkpoint does not exist: {args.mask_weights}")
    run(args)


if __name__ == "__main__":
    main()
