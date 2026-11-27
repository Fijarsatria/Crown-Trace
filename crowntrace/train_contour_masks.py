"""Train a finer crown mask on actual detector crops and shifted reference crops."""

import argparse
import json
from pathlib import Path
import shutil
from time import perf_counter

import numpy as np
import torch

from .compact import CompactDetector
from .refine_masks import MASK_THRESHOLDS, contour_loss, foreground_iou, prepare
from .segment import CrownContourNet, position_grid
from .train_coco_crowns import digest, read_split, save


def features(pixels, position):
    return torch.cat((pixels, position.expand(len(pixels), -1, -1, -1)), dim=1).contiguous(
        memory_format=torch.channels_last)


@torch.inference_mode()
def calibrate_crop(model, rows, position):
    rgb, masks = rows
    probabilities = torch.cat([model(features(part.float() / 255, position)).sigmoid()[:, 0] for part in rgb.split(48)])
    cuts = torch.linspace(.50, .95, 10)
    measured = []
    for threshold in MASK_THRESHOLDS:
        overlap = foreground_iou(probabilities >= threshold, masks)
        success = (overlap[:, None] >= cuts).float().mean()
        measured.append({"threshold": threshold, "strict_overlap_score": float(success),
                         "mean_crop_iou": float(overlap.mean()), "crop_success_at_75": float((overlap >= .75).float().mean())})
    return max(measured, key=lambda row: (row["strict_overlap_score"], row["mean_crop_iou"]))


def run(args):
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    generator = torch.Generator().manual_seed(args.seed)
    reference = json.loads((args.model / "training.json").read_text(encoding="utf-8"))
    hashes = {key: digest(args.data / key / "_annotations.coco.json") for key in ("train", "val", "test")}
    source_detector = args.model / reference["detector"]["checkpoint"]
    if hashes != reference["partition_sha256"] or digest(source_detector) != reference["detector"]["sha256"]:
        raise ValueError("Data or detector do not match the source run.")
    training, validation = (read_split(args.data, key) for key in ("train", "val"))
    if {row.group for row in training} & {row.group for row in validation}:
        raise ValueError("An annotation source family crosses the validation boundary.")
    detector = CompactDetector(source_detector)
    train_exact, train_real, _, train_stats = prepare(training, detector, rng, True, .30, args.size, args.padding)
    _, val_real, _, val_stats = prepare(validation, detector, rng, False, .30, args.size, args.padding)
    position = position_grid(args.size)
    model = CrownContourNet().to(memory_format=torch.channels_last)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.002)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.epochs, eta_min=.00015)
    args.output.mkdir(parents=True)
    best, history = None, []
    for epoch in range(1, args.epochs + 1):
        start = perf_counter()
        model.train()
        total = 0.
        seen = 0
        for batch in torch.randperm(len(train_exact[0]), generator=generator).split(48):
            realistic = torch.rand(len(batch), generator=generator) < .70
            rgb = torch.where(realistic[:, None, None, None], train_real[0][batch], train_exact[0][batch])
            truth = torch.where(realistic[:, None, None], train_real[1][batch], train_exact[1][batch])
            k = int(torch.randint(4, (), generator=generator))
            rgb, truth = rgb.rot90(k, (-2, -1)), truth.rot90(k, (-2, -1))
            flipped = torch.rand(len(batch), generator=generator) < .5
            rgb[flipped], truth[flipped] = rgb[flipped].flip(-1), truth[flipped].flip(-1)
            gain = .80 + .40 * torch.rand((len(batch), 3, 1, 1), generator=generator)
            inputs = features((rgb.float() / 255 * gain).clamp_(0, 1), position)
            optimizer.zero_grad(set_to_none=True)
            error = contour_loss(model(inputs)[:, 0], truth.float())
            error.backward()
            optimizer.step()
            total += error.item() * len(batch)
            seen += len(batch)
        scheduler.step()
        model.eval()
        result = {"epoch": epoch, "train_loss": total / seen, **calibrate_crop(model, val_real, position),
                  "seconds": perf_counter() - start}
        if best is None or result["strict_overlap_score"] > best["strict_overlap_score"]:
            best = result
            save(args.output / "mask.pth", {"architecture": "contour", "weights": model.state_dict(),
                                           "size": args.size, "padding": args.padding,
                                           "name": f"CrownContour-{args.size}", "threshold": result["threshold"]})
        history.append(result)
        print(json.dumps(result), flush=True)

    shutil.copyfile(source_detector, args.output / "detector.pth")
    report = {"partition_sha256": hashes, "seed": args.seed, "threads": args.threads,
              "detector": reference["detector"], "train_crops": train_stats, "val_crops": val_stats,
              "selection": "mean recall across crop IoUs .50:.95 among one-to-one validation detector matches",
              "mask": {"checkpoint": "mask.pth", "sha256": digest(args.output / "mask.pth"),
                       "selected_epoch": best["epoch"], "history": history,
                       "size": args.size, "padding": args.padding, "threshold": best["threshold"]}}
    (args.output / "training.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--size", type=int, default=96)
    parser.add_argument("--padding", type=float, default=.26)
    parser.add_argument("--epochs", type=int, default=18)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if args.output.exists() or min(args.epochs, args.threads) < 1 or not 64 <= args.size <= 256 or args.size % 8:
        parser.error("Use a new destination, positive epochs/threads and a size of 64–256 divisible by eight.")
    if not (0 <= args.padding <= .75):
        parser.error("--padding must be between 0 and 0.75.")
    run(args)


if __name__ == "__main__":
    main()
