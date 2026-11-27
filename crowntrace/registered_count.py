"""Exploratory census in the shared pixel frame of verified MOPAD crop groups."""
import argparse
from collections import defaultdict
import csv
from dataclasses import replace
import json
from pathlib import Path

from .audit_overlap import offsets
from .dataset import annotations, manifest
from .inference import Detection, fuse


def registered(det, row, view):
    dx, dy = offsets(row["file_name"])
    x, y, r, b = det.box
    cx, cy = det.center
    clearance = max(0, min(96, cx, cy, 1024-cx, 1024-cy))
    return replace(det, box=(x+dx, y+dy, r+dx, b+dy), view=view,
                   score=det.score * (0.75 + 0.25 * clearance / 96))


def main():
    parser = argparse.ArgumentParser()
    for key in ("manifest", "benchmark", "overlap", "output"):
        parser.add_argument(f"--{key}", required=True, type=Path)
    parser.add_argument("--group", default="52000_20000")
    args = parser.parse_args()
    source = manifest(args.manifest)
    proof = json.loads(args.overlap.read_text(encoding="utf-8"))["alignment"].get(args.group, {})
    if not proof.get("sampled_pairs") or proof["median_gray_mad_xy"] >= 0.20 * proof["median_gray_mad_offset_control"]:
        parser.error("Audit piksel belum mendukung penempatan potongan pada kerangka bersama.")
    samples = [row for row in source["samples"] if row["group"] == args.group]
    split = samples[0]["split"]
    index = {row["file_name"]: (i, row) for i, row in enumerate(samples, 1)}
    actual = [registered(d, row, i) for i, row in enumerate(samples, 1)
              for d in annotations(row, source["categories"], (1024, 1024))]
    benchmark = json.loads((args.benchmark / "summary.json").read_text(encoding="utf-8"))
    thresholds = benchmark["metrics"][split]
    by_method = defaultdict(list)
    observed = defaultdict(set)
    with (args.benchmark / "predictions.jsonl").open(encoding="utf-8") as file:
        for line in file:
            result = json.loads(line)
            method, name = result["method"], result["image"]
            if name not in index or result["split"] != split:
                continue
            row_id, row = index[name]
            observed[method].add(name)
            threshold = thresholds[method]["operating_threshold"]
            by_method[method].extend(
                registered(Detection(tuple(det["box"]), det["label"], det["score"]), row, row_id)
                for det in result["detections"] if det["score"] >= threshold
            )
    if not by_method or any(len(used) != len(samples) for used in observed.values()):
        parser.error("Satu atau lebih keluaran prediksi tidak mencakup seluruh tile kelompok.")
    radius_grid = (0.18, 0.28, 0.38)
    summaries = {}
    for tolerance in radius_grid:
        reference = len(fuse(actual, center_ratio=tolerance))
        summaries[str(tolerance)] = {
            "registered_annotation_candidates": reference,
            "methods": {
                method: {"tile_occurrences": len(proposals),
                         "registered_prediction_candidates": len(registered_detections),
                         "difference_candidates": len(registered_detections) - reference}
                for method, proposals in by_method.items()
                for registered_detections in (fuse(proposals, center_ratio=tolerance),)
            },
        }
    report = {"group": args.group, "tile_count": len(samples), "raw_annotation_occurrences": len(actual),
              "coordinates": "parent-image-relative pixels; geographic CRS unknown",
              "reference": "deduplicated box annotations. This reference is not a field census.",
              "merge_sensitivity": summaries}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with (args.output / "registered_full_256.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["class", "center_x_group_px", "center_y_group_px", "source_filename"])
        for center in fuse(by_method["full_640"], center_ratio=0.28):
            writer.writerow([center.label, *center.center, samples[center.view - 1]["file_name"]])
    print(json.dumps({"tiles": len(samples), **summaries["0.28"]}, indent=2))


if __name__ == "__main__":
    main()
