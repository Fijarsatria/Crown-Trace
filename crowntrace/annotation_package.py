"""Prepare complete MOPAD frames and box proposals for human crown tracing."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .dataset import DATA, ROOT, manifest
from .registered_benchmark import assemble, build_reference_boxes


def prepare(manifest_path, images, overlap_audit, output):
    source = manifest(manifest_path)
    alignment = json.loads(overlap_audit.read_text(encoding="utf-8"))["alignment"]
    output.mkdir(parents=True, exist_ok=False)
    report = {"source": source["source"], "coordinates": "parent-frame pixels, CRS unknown",
              "label_state": "box proposals only; all individual crown polygons need human review",
              "condition_provenance": "Legacy detector mapping merges Grass into Healthy. Re-review all conditions.",
              "splits": {}}
    for split in ("train", "val", "test"):
        rows = sorted((r for r in source["samples"] if r["split"] == split), key=lambda r: r["file_name"])
        group, = {row["group"] for row in rows}
        proof = alignment[group]
        if not proof["sampled_pairs"] or proof["median_gray_mad_xy"] >= .20 * proof["median_gray_mad_offset_control"]:
            raise ValueError(f"Unverified offsets for {group}")
        pixels = assemble(rows, images)
        height, width = pixels.shape[:2]
        boxes = build_reference_boxes(rows, source["categories"], pixels)
        target = output / split
        image_dir = target / "images"
        image_dir.mkdir(parents=True)
        name = f"mopad_site2_{group}.png"
        Image.fromarray(pixels[:, :, :3]).save(image_dir / name)
        Image.fromarray(pixels[:, :, 3]).save(target / "valid_pixels.png")
        coco = {"info": {"description": "MOPAD boxes for annotation, no crown masks"}, "licenses": [],
                "images": [{"id": 1, "file_name": name, "width": width, "height": height,
                            "source_group": group}],
                "categories": [{"id": 1, "name": "palm_crown"}], "annotations": []}
        with (target / "box_proposals.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["proposal_id", "legacy_visual_label", "x1", "y1", "x2", "y2", "cx", "cy"])
            for index, det in enumerate(boxes, 1):
                x, y, right, bottom = det.box
                box = [x, y, right - x, bottom - y]
                coco["annotations"].append({"id": index, "image_id": 1, "category_id": 1, "bbox": box,
                                            "area": box[2] * box[3], "iscrowd": 0, "segmentation": []})
                writer.writerow([index, det.label, *det.box, *det.center])
        (target / "_annotations.coco.json").write_text(json.dumps(coco, indent=2) + "\n", encoding="utf-8")
        report["splits"][split] = {"source_group": group, "source_tiles": len(rows), "frame_px": [width, height],
                                  "covered_pct": round(100 * np.mean(pixels[:, :, 3] != 0), 3),
                                  "box_proposals": len(boxes), "reviewed_crown_masks": 0,
                                  "legacy_visual_labels": dict(Counter(det.label for det in boxes))}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DATA / "mopad_site2_val_manifest.json")
    parser.add_argument("--images", type=Path, default=DATA / "mopad_site2_val_images")
    parser.add_argument("--overlap-audit", type=Path, default=ROOT / "results/overlap_screen.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.manifest, args.images, args.overlap_audit, args.output)


if __name__ == "__main__":
    main()
