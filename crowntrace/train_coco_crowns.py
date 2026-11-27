"""Fit a single-class detector and the CrownTrace mask head on grouped COCO tiles."""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from hashlib import file_digest
import json
from pathlib import Path
import shutil
from time import perf_counter

import numpy as np
from PIL import Image, ImageDraw
import torch
from torch.nn import functional as F

from .coco_mask_audit import image_path
from .compact import CrownNet, MultiScaleCrownNet, loss, targets, to_tensor
from .inference import Detection
from .segment import CrownMaskNet, POSITION, SIZE, crop_box

TASK_CLASSES = ("palm_crown",)
MASK_CUTS = (.30, .35, .40, .45, .50, .55, .60, .65)


@dataclass
class Tile:
    path: Path
    group: str
    regions: list


def read_split(root, split):
    directory = root / split
    document = json.loads((directory / "_annotations.coco.json").read_text(encoding="utf-8-sig"))
    if [row["name"] for row in document["categories"]] != list(TASK_CLASSES):
        raise ValueError("Expected the grouped single-class COCO dataset.")
    regions = defaultdict(list)
    for ann in document["annotations"]:
        ring, = ann["segmentation"]
        points = tuple(zip(ring[::2], ring[1::2], strict=True))
        x, y = zip(*points)
        regions[ann["image_id"]].append((Detection((min(x), min(y), max(x), max(y)), TASK_CLASSES[0], 1.0), points))
    return [Tile(image_path(directory, row["file_name"]), row["source_group"], regions[row["id"]])
            for row in document["images"]]


def mask_pixels(points, window):
    x, y, right, bottom = window
    mask = Image.new("L", (right - x, bottom - y))
    ImageDraw.Draw(mask).polygon([(u - x, v - y) for u, v in points], fill=255)
    return mask


def digest(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def save(path, checkpoint):
    temporary = path.with_suffix(".tmp")
    torch.save(checkpoint, temporary)
    temporary.replace(path)


def detection_inputs(tiles, size):
    pixels, packed = [], []
    for tile in tiles:
        with Image.open(tile.path) as source:
            image = source.convert("RGB")
        pixels.append(to_tensor(image, size))
        packed.append(targets([det for det, _ in tile.regions], *image.size, size, TASK_CLASSES))
    return torch.stack(pixels), tuple(torch.stack(values) for values in zip(*packed, strict=True))


def turn_batch(images, heat, boxes, labels, turns, mirror):
    images, heat, boxes, labels = (value.rot90(turns, (-2, -1)) for value in (images, heat, boxes, labels))
    x, y, width, height = boxes.unbind(dim=1)
    centers = ((x, y), (y, 1 - x), (1 - x, 1 - y), (1 - y, x))
    u, v = centers[turns]
    dimensions = (height, width) if turns % 2 else (width, height)
    boxes = torch.stack((u, v, *dimensions), dim=1)
    if mirror:
        images, heat, boxes, labels = (value.flip(-1) for value in (images, heat, boxes, labels))
        boxes[:, 0] = 1 - boxes[:, 0]
    return images, heat, boxes, labels


def train_detector(train, val, args, generator):
    train_pixels, train_targets = detection_inputs(train, args.size)
    val_pixels, val_targets = detection_inputs(val, args.size)
    architecture = MultiScaleCrownNet if args.architecture == "multiscale" else CrownNet
    model = architecture(len(TASK_CLASSES)).to(memory_format=torch.channels_last)
    if args.warm_start:
        initial = torch.load(args.warm_start, map_location="cpu", weights_only=True)
        if tuple(initial["class_names"]) != TASK_CLASSES:
            raise ValueError("Incompatible warm-start classes.")
        missing, unexpected = model.load_state_dict(initial["weights"], strict=False)
        if unexpected or any(key.startswith(("backbone.", "head.")) for key in missing):
            raise ValueError("Incompatible warm-start detector.")
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.detector_epochs, eta_min=args.lr * .075)
    best_score, history = float("inf"), []
    for epoch in range(1, args.detector_epochs + 1):
        start = perf_counter()
        model.train()
        training = 0.0
        for indices in torch.randperm(len(train_pixels), generator=generator).split(8):
            images = train_pixels[indices]
            expected = tuple(value[indices] for value in train_targets)
            if args.geometric_augment:
                turns = int(torch.randint(4, (), generator=generator))
                mirror = bool(torch.rand((), generator=generator) < .5)
                images, *expected = turn_batch(images, *expected, turns, mirror)
            gain = .85 + .30 * torch.rand((len(indices), 3, 1, 1), generator=generator)
            batch = (images * gain).clamp_(0, 1).contiguous(memory_format=torch.channels_last)
            optimizer.zero_grad(set_to_none=True)
            value = loss(model(batch), *expected, None, args.overlap_weight)
            value.backward()
            optimizer.step()
            training += value.item() * len(indices)
        scheduler.step()
        model.eval()
        validation = 0.0
        with torch.inference_mode():
            for pixels, heat, boxes, labels in zip(val_pixels.split(8), *(v.split(8) for v in val_targets), strict=True):
                prediction = model(pixels.contiguous(memory_format=torch.channels_last))
                validation += loss(prediction, heat, boxes, labels, None, args.overlap_weight).item() * len(pixels)
        record = {"epoch": epoch, "train_loss": training / len(train_pixels),
                  "val_loss": validation / len(val_pixels), "seconds": perf_counter() - start}
        history.append(record)
        if record["val_loss"] < best_score:
            best_score = record["val_loss"]
            save(args.output / "detector.pth", {"weights": model.state_dict(), "class_names": list(TASK_CLASSES),
                                                "input_size": args.size, "epoch": epoch})
        print("detector", json.dumps(record), flush=True)
    return {"checkpoint": "detector.pth", "history": history, "architecture": args.architecture,
            "warm_start_sha256": digest(args.warm_start) if args.warm_start else None,
            "learning_rate": args.lr, "distance_iou_weight": args.overlap_weight,
            "right_angle_and_flip_augmentation": args.geometric_augment,
            "selected_epoch": min(history, key=lambda row: row["val_loss"])["epoch"],
            "input_size": args.size, "target_center_cells": int(train_targets[0].sum()),
            "train_boxes": sum(len(tile.regions) for tile in train)}


def mask_inputs(tiles):
    patches, labels = [], []
    for tile in tiles:
        with Image.open(tile.path) as source:
            image = source.convert("RGB")
        for detection, polygon in tile.regions:
            window = crop_box(detection.box, *image.size)
            rgb = image.crop(window).resize((SIZE, SIZE), Image.Resampling.BILINEAR)
            mask = mask_pixels(polygon, window).resize((SIZE, SIZE), Image.Resampling.NEAREST)
            pixels = np.array(rgb, dtype=np.uint8).transpose(2, 0, 1)
            labels.append(np.array(mask) > 0)
            patches.append(pixels)
    return torch.from_numpy(np.stack(patches)), torch.from_numpy(np.stack(labels))


def mask_features(pixels):
    return torch.cat((pixels, POSITION.expand(len(pixels), -1, -1, -1)), dim=1)


def perturb_mask_batch(pixels, targets, generator):
    rgb = pixels.float() / 255
    flip = torch.rand(len(pixels), generator=generator) < .5
    rgb[flip] = rgb[flip].flip(-1)
    targets[flip] = targets[flip].flip(-1)
    batch = torch.cat((rgb, targets[:, None]), dim=1)
    offsets = (torch.rand((len(rgb), 4), generator=generator) - .5) * .22
    grid = torch.zeros((len(rgb), 2, 3))
    grid[:, 0, 0], grid[:, 1, 1] = 1 + offsets[:, 0], 1 + offsets[:, 1]
    grid[:, :, 2] = offsets[:, 2:]
    moved = F.grid_sample(batch, F.affine_grid(grid, batch.shape, align_corners=False),
                          padding_mode="border", align_corners=False)
    gain = .82 + .36 * torch.rand((len(pixels), 3, 1, 1), generator=generator)
    return mask_features((moved[:, :3] * gain).clamp_(0, 1)), (moved[:, 3] >= .5).float()


def train_mask(train, val, args, generator):
    train_rgb, train_masks = mask_inputs(train)
    val_rgb, val_masks = mask_inputs(val)
    model = CrownMaskNet()
    optimizer = torch.optim.AdamW(model.parameters(), lr=.002)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.mask_epochs, eta_min=.00015)
    best, history = (-1., .50), []
    for epoch in range(1, args.mask_epochs + 1):
        start = perf_counter()
        model.train()
        training = 0.0
        for indices in torch.randperm(len(train_rgb), generator=generator).split(48):
            inputs, truth = perturb_mask_batch(train_rgb[indices], train_masks[indices], generator)
            optimizer.zero_grad(set_to_none=True)
            logits = model(inputs)[:, 0]
            probability = logits.sigmoid()
            dice = (2 * (probability * truth).sum((1, 2)) + 1) / ((probability + truth).sum((1, 2)) + 1)
            value = F.binary_cross_entropy_with_logits(logits, truth) + (1 - dice.mean())
            value.backward()
            optimizer.step()
            training += value.item() * len(indices)
        scheduler.step()
        model.eval()
        with torch.inference_mode():
            probabilities = torch.cat([model(mask_features(x.float() / 255)).sigmoid()[:, 0]
                                       for x in val_rgb.split(48)])
            scores = {}
            for cutoff in MASK_CUTS:
                predicted = probabilities >= cutoff
                intersection = (predicted & val_masks).sum((1, 2))
                union = (predicted | val_masks).sum((1, 2))
                scores[cutoff] = float((intersection / union.clamp_min(1)).mean())
        cutoff = max(scores, key=lambda threshold: (scores[threshold], -abs(threshold - .5)))
        record = {"epoch": epoch, "train_loss": training / len(train_rgb), "validation_iou64": scores[cutoff],
                  "threshold": cutoff, "seconds": perf_counter() - start}
        history.append(record)
        if scores[cutoff] > best[0]:
            best = scores[cutoff], cutoff
            save(args.output / "mask.pth", {"weights": model.state_dict(), "threshold": cutoff,
                                            "name": "CrownMask-Trees", "epoch": epoch})
        print("mask", json.dumps(record), flush=True)
    return {"checkpoint": "mask.pth", "history": history,
            "selected_epoch": max(history, key=lambda row: row["validation_iou64"])["epoch"],
            "crop_size": SIZE, "threshold": best[1], "train_crops": len(train_rgb)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=416)
    parser.add_argument("--architecture", choices=("baseline", "multiscale"), default="baseline")
    parser.add_argument("--warm-start", type=Path)
    parser.add_argument("--reuse-mask-run", type=Path)
    parser.add_argument("--lr", type=float, default=.002)
    parser.add_argument("--overlap-weight", type=float, default=0.)
    parser.add_argument("--geometric-augment", action="store_true")
    parser.add_argument("--detector-epochs", type=int, default=24)
    parser.add_argument("--mask-epochs", type=int, default=10)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if (args.size < 64 or args.size % 32 or min(args.detector_epochs, args.mask_epochs, args.threads) < 1 or
            args.lr <= 0 or args.overlap_weight < 0):
        parser.error("Use a size divisible by 32 and positive epochs and thread counts.")
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    generator = torch.Generator().manual_seed(args.seed)
    train, val = (read_split(args.data, name) for name in ("train", "val"))
    for name, tiles in (("train", train), ("val", val)):
        if not tiles or any(not tile.regions for tile in tiles):
            raise ValueError(f"Missing crown annotation or tile in {name}.")
    if {tile.group for tile in train} & {tile.group for tile in val}:
        raise ValueError("A source family crosses training and validation.")
    hashes = {key: digest(args.data / key / "_annotations.coco.json") for key in ("train", "val", "test")}
    reuse = None
    if args.reuse_mask_run:
        initial = json.loads((args.reuse_mask_run / "training.json").read_text(encoding="utf-8"))
        mask_source = args.reuse_mask_run / initial["mask"]["checkpoint"]
        if initial["partition_sha256"] != hashes or digest(mask_source) != initial["mask"]["sha256"]:
            raise ValueError("Reused mask must be verified against the same dataset partition.")
        reuse = {**initial["mask"], "reused_from": str(args.reuse_mask_run)}
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"data": str(args.data.resolve()), "seed": args.seed, "torch": torch.__version__,
              "device": "cpu", "threads": args.threads,
              "partition_sha256": hashes,
              "detector": train_detector(train, val, args, generator),
              "mask": reuse or train_mask(train, val, args, generator)}
    if reuse:
        shutil.copyfile(mask_source, args.output / "mask.pth")
    for key in ("detector", "mask"):
        report[key]["sha256"] = digest(args.output / report[key]["checkpoint"])
    (args.output / "training.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
