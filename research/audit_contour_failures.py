"""Diagnose contour failure modes on the validation partition, without retuning."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch

from crowntrace.compact import CompactDetector
from crowntrace.evaluate_coco_crowns import LocalMask, bbox_window, mask_iou, proposals
from crowntrace.segment import CrownMaskPredictor
from crowntrace.train_coco_crowns import read_split, mask_pixels
from research.contour_refit import original_mask, probabilities, overlap_matrix


def box_iou(a, b):
    x, y, r, t = a
    u, v, s, w = b
    intersection = max(0, min(r, s)-max(x, u))*max(0, min(t, w)-max(y, v))
    union = (r-x)*(t-y)+(s-u)*(w-v)-intersection
    return intersection / union if union else 0.


def matched_reference(matrix, scores, threshold):
    remaining = set(range(matrix.shape[1]))
    hits = set()
    for i in np.argsort(-np.asarray(scores), kind="stable"):
        eligible = sorted(remaining)
        if not eligible:
            break
        index = max(eligible, key=lambda j: matrix[i, j])
        if matrix[i, index] >= threshold:
            hits.add(index)
            remaining.remove(index)
    return hits


def collect(tiles, detector, predictor):
    rows = []
    for tile in tiles:
        with Image.open(tile.path) as photo:
            image = photo.convert("RGB")
        refs = [box for box, _ in tile.regions]
        masks = []
        for box, polygon in tile.regions:
            window = bbox_window(box.box, image)
            pixels = np.asarray(mask_pixels(polygon, window)) > 0
            masks.append(LocalMask(window, pixels, int(pixels.sum())))
        predicted = [d for d in proposals(detector, image) if d.score >= .25]
        prompted = [original_mask(p, predictor.threshold) for p in probabilities(predictor, image, refs)]
        outlines = [original_mask(p, predictor.threshold) for p in probabilities(predictor, image, predicted)]
        matrix = overlap_matrix(outlines, masks)
        scores = [d.score for d in predicted]
        hits50 = matched_reference(matrix, scores, .5)
        hits75 = matched_reference(matrix, scores, .75)
        for n, (ref, mask, oracle) in enumerate(zip(refs, masks, prompted, strict=True)):
            x, y, r, b = mask.window
            rows.append({
                "tile": tile.path.name,
                "object": n,
                "mask_area": mask.area,
                "edge_5px": min(x, y, image.width-r, image.height-b) <= 5,
                "max_neighbor_box_iou": max((box_iou(ref.box, other.box) for j, other in enumerate(refs) if j != n), default=0.),
                "best_predicted_box_iou": max((box_iou(ref.box, det.box) for det in predicted), default=0.),
                "oracle_mask_iou": mask_iou(mask, oracle),
                "matched_mask_50": n in hits50,
                "matched_mask_75": n in hits75,
            })
    return rows


def summarize(rows):
    if not rows:
        return {"n": 0}
    return {
        "n": len(rows),
        "mean_oracle_mask_iou": float(np.mean([r["oracle_mask_iou"] for r in rows])),
        "oracle_iou75_rate": float(np.mean([r["oracle_mask_iou"] >= .75 for r in rows])),
        "candidate_box_iou75_rate": float(np.mean([r["best_predicted_box_iou"] >= .75 for r in rows])),
        "matched_mask50_recall": float(np.mean([r["matched_mask_50"] for r in rows])),
        "matched_mask75_recall": float(np.mean([r["matched_mask_75"] for r in rows])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("weights", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    torch.set_num_threads(4)
    args.output.mkdir(parents=True, exist_ok=False)
    rows = collect(read_split(args.data, "val"), CompactDetector(args.weights/"detector.pth"),
                   CrownMaskPredictor(args.weights/"mask.pth"))
    quantiles = np.quantile([r["mask_area"] for r in rows], [.25, .75])
    groups = {
        "all": rows,
        "near_edge": [r for r in rows if r["edge_5px"]],
        "interior": [r for r in rows if not r["edge_5px"]],
        "neighbor_box_iou_at_least_0.15": [r for r in rows if r["max_neighbor_box_iou"] >= .15],
        "neighbor_box_iou_below_0.15": [r for r in rows if r["max_neighbor_box_iou"] < .15],
        "smallest_quartile": [r for r in rows if r["mask_area"] <= quantiles[0]],
        "largest_quartile": [r for r in rows if r["mask_area"] >= quantiles[1]],
    }
    summary = {"scope": "Descriptive Trees validation only. Overlapping strata, no causal inference.",
               "area_quartiles_px": quantiles.tolist(),
               "groups": {name: summarize(group) for name, group in groups.items()}}
    with (args.output/"instances.csv").open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    (args.output/"summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
