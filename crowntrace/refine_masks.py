"""Fine-tune a crown mask head on reference boxes and realistic detector crops."""

import argparse
import json
from pathlib import Path
import shutil
from time import perf_counter

import numpy as np
from PIL import Image
import torch
from torch.nn import functional as F

from .benchmark import iou_matrix
from .compact import CompactDetector
from .evaluate_coco_crowns import proposals
from .segment import CrownMaskPredictor, CrownMaskRefiner, SIZE, crop_box
from .train_coco_crowns import digest, mask_features, mask_pixels, read_split, save

MASK_THRESHOLDS = (.25, .30, .35, .40, .45, .50, .55, .60, .65)


def matched_crops(predictions, reference, minimum_iou):
    overlaps = iou_matrix(predictions, reference)
    candidates = np.argwhere(overlaps >= minimum_iou)
    order = np.argsort(-overlaps[candidates[:, 0], candidates[:, 1]], kind="stable")
    used, matches = set(), {}
    for index in order:
        p, r = candidates[index]
        if p not in used and r not in matches:
            matches[r] = (predictions[p].box, float(overlaps[p, r]))
            used.add(p)
    return matches


def jitter(box, rng):
    x, y, right, bottom = box
    width, height = right - x, bottom - y
    dx, dy = rng.uniform(-.16, .16, size=2) * (width, height)
    half_w, half_h = rng.uniform(.76, 1.24, size=2) * (width / 2, height / 2)
    cx, cy = (x + right) / 2 + dx, (y + bottom) / 2 + dy
    return cx - half_w, cy - half_h, cx + half_w, cy + half_h


def make_patch(image, box, polygon, size=SIZE, padding=.13):
    window = crop_box(box, *image.size, padding=padding)
    patch = image.crop(window).resize((size, size), Image.Resampling.BILINEAR)
    mask = mask_pixels(polygon, window).resize((size, size), Image.Resampling.NEAREST)
    pixels = np.array(patch, dtype=np.uint8).transpose(2, 0, 1)
    return pixels, np.asarray(mask) > 0


def prepare(tiles, detector, rng, training, cutoff, size=SIZE, padding=.13):
    oracle, realistic = [], []
    ious, paired_indices = [], []
    for tile in tiles:
        with Image.open(tile.path) as source:
            image = source.convert("RGB")
        boxes = [box for box, _ in tile.regions]
        predicted = [box for box in proposals(detector, image) if box.score >= cutoff]
        matched = matched_crops(predicted, boxes, .40 if training else .50)
        for index, (box, polygon) in enumerate(tile.regions):
            oracle.append(make_patch(image, box.box, polygon, size, padding))
            if index in matched:
                nearby, overlap = matched[index]
                ious.append(overlap)
                paired_indices.append(len(oracle) - 1)
                realistic.append(make_patch(image, nearby, polygon, size, padding))
            elif training:
                candidate = make_patch(image, jitter(box.box, rng), polygon, size, padding)
                realistic.append(candidate if candidate[1].any() else oracle[-1])
    if not oracle or not realistic or not ious:
        raise ValueError("Expected annotated crowns and at least one usable detector match.")
    stacks = []
    for candidates in (oracle, realistic):
        rgb, masks = zip(*candidates, strict=True)
        stacks.append((torch.from_numpy(np.stack(rgb)), torch.from_numpy(np.stack(masks))))
    matched_oracle = None if training else tuple(values[paired_indices] for values in stacks[0])
    stats = {"all_crowns": len(oracle), "matched_detector_crops": len(ious),
             "mean_matched_box_iou": float(np.mean(ious))}
    return (*stacks, matched_oracle, stats)


def foreground_iou(predicted, targets):
    intersection = (predicted & targets).sum((1, 2))
    union = (predicted | targets).sum((1, 2))
    return intersection / union.clamp_min(1)


def evaluate_crops(probabilities, targets, cutoff):
    ious = foreground_iou(probabilities >= cutoff, targets)
    return {"mean_iou": float(ious.mean()), "fraction_iou_ge_75": float((ious >= .75).float().mean())}


def contour_loss(logits, truth):
    probabilities = logits.sigmoid()
    foreground = truth[:, None]
    band = (F.max_pool2d(foreground, 5, 1, 2) - (-F.max_pool2d(-foreground, 5, 1, 2)))[:, 0]
    weights = 1 + 1.5 * band
    error = F.binary_cross_entropy_with_logits(logits, truth, reduction="none")
    dice = (2 * (probabilities * truth).sum((1, 2)) + 1) / ((probabilities + truth).sum((1, 2)) + 1)
    return (error * weights).sum() / weights.sum() + 1 - dice.mean()


@torch.inference_mode()
def predict(model, pixels):
    return torch.cat([model(mask_features(batch.float() / 255).contiguous(memory_format=torch.channels_last))
                      .sigmoid()[:, 0] for batch in pixels.split(48)])


def run(args):
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    generator = torch.Generator().manual_seed(args.seed)
    original = json.loads((args.model / "training.json").read_text(encoding="utf-8"))
    partition_hashes = {split: digest(args.data / split / "_annotations.coco.json")
                        for split in ("train", "val", "test")}
    if original["partition_sha256"] != partition_hashes:
        raise ValueError("Dataset partitions differ from baseline training.")
    detector_path = args.model / original["detector"]["checkpoint"]
    mask_path = args.model / original["mask"]["checkpoint"]
    if digest(detector_path) != original["detector"]["sha256"] or digest(mask_path) != original["mask"]["sha256"]:
        raise ValueError("Checkpoint hashes differ from the baseline training run.")
    detector = CompactDetector(detector_path)
    train_tiles, val_tiles = (read_split(args.data, split) for split in ("train", "val"))
    if {tile.group for tile in train_tiles} & {tile.group for tile in val_tiles}:
        raise ValueError("Source families must be unique to a partition.")
    train_exact, train_real, _, train_stats = prepare(train_tiles, detector, rng, True, args.count_cutoff)
    _, val_real, val_exact, val_stats = prepare(val_tiles, detector, rng, False, args.count_cutoff)

    model = CrownMaskRefiner().to(memory_format=torch.channels_last)
    base = CrownMaskPredictor(mask_path)
    model.load_state_dict(base.model.state_dict(), strict=False)
    model.eval()
    baseline = {name: evaluate_crops(predict(model, rgb), mask, base.threshold)
                for name, (rgb, mask) in (("matched_detector_crops", val_real), ("same_crowns_reference_boxes", val_exact))}
    print("Baseline crop audit", json.dumps(baseline), flush=True)
    args.output.mkdir(parents=True)
    (args.output / "baseline_crop_audit.json").write_text(
        json.dumps({"validation": val_stats, "metrics": baseline}, indent=2) + "\n", encoding="utf-8")
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.epochs, eta_min=.00015)
    history, best = [], None
    for epoch in range(1, args.epochs + 1):
        start = perf_counter()
        model.train()
        loss = 0.
        for batch in torch.randperm(len(train_exact[0]), generator=generator).split(48):
            use_realistic = torch.rand(len(batch), generator=generator) < .65
            pixels = torch.where(use_realistic[:, None, None, None], train_real[0][batch], train_exact[0][batch])
            targets = torch.where(use_realistic[:, None, None], train_real[1][batch], train_exact[1][batch]).float()
            flip_x = torch.rand(len(pixels), generator=generator) < .5
            flip_y = torch.rand(len(pixels), generator=generator) < .5
            pixels[flip_x], targets[flip_x] = pixels[flip_x].flip(-1), targets[flip_x].flip(-1)
            pixels[flip_y], targets[flip_y] = pixels[flip_y].flip(-2), targets[flip_y].flip(-2)
            gain = .85 + .30 * torch.rand((len(pixels), 3, 1, 1), generator=generator)
            features = mask_features((pixels.float().mul_(1 / 255) * gain).clamp_(0, 1))
            optimizer.zero_grad(set_to_none=True)
            error = contour_loss(model(features.contiguous(memory_format=torch.channels_last))[:, 0], targets)
            error.backward()
            optimizer.step()
            loss += error.item() * len(batch)
        scheduler.step()
        model.eval()
        predictions = predict(model, val_real[0])
        validation = [(cutoff, evaluate_crops(predictions, val_real[1], cutoff)) for cutoff in MASK_THRESHOLDS]
        threshold, score = max(validation, key=lambda item: (item[1]["mean_iou"], item[1]["fraction_iou_ge_75"]))
        record = {"epoch": epoch, "train_loss": loss / len(train_exact[0]), "threshold": threshold,
                  **score, "seconds": perf_counter() - start}
        history.append(record)
        if best is None or record["mean_iou"] > best["mean_iou"]:
            best = record
            save(args.output / "mask.pth", {"architecture": "refined", "name": "CrownMask-Refined",
                                           "weights": model.state_dict(), "threshold": threshold})
        print("refined", json.dumps(record), flush=True)
    shutil.copyfile(detector_path, args.output / "detector.pth")
    selected = CrownMaskPredictor(args.output / "mask.pth")
    oracle_best = evaluate_crops(predict(selected.model, val_exact[0]), val_exact[1], selected.threshold)
    report = {"partition_sha256": partition_hashes, "seed": args.seed, "threads": args.threads,
              "detector": original["detector"], "baseline_mask_sha256": digest(mask_path),
              "training_crops": train_stats, "validation_crops": val_stats, "baseline_metrics": baseline,
              "refined_reference_box_metrics": oracle_best,
              "selection_metric": "validation mean crop IoU on one-to-one detector matches at box IoU >= .50",
              "mask": {"checkpoint": "mask.pth", "sha256": digest(args.output / "mask.pth"),
                       "selected_epoch": best["epoch"], "threshold": best["threshold"], "history": history}}
    (args.output / "training.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--count-cutoff", type=float, default=.30)
    args = parser.parse_args()
    if args.output.exists() or min(args.epochs, args.threads) < 1 or not (.05 <= args.count_cutoff <= 1):
        parser.error("Use an unused output path, positive epochs/threads and a valid detection cutoff.")
    run(args)


if __name__ == "__main__":
    main()
