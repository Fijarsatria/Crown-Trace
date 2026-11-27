"""Build a spatially grouped visual-appearance classification baseline from SIIT boxes."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .dataset import DATA, manifest
from .inference import LABELS
from .registered_benchmark import build_reference_boxes
from .segment import crop_box


def prepare(manifest_path, frames, output):
    source = manifest(manifest_path)
    output.mkdir(parents=True, exist_ok=False)
    report = {"annotation_source": source["annotation_source"],
              "role": "box-conditioned visual appearance classification; no mask labels or field diagnosis",
              "reference": "deduplicated same-site box candidates in reconstructed pixel frames", "splits": {}}
    with (output / "samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "split", "group", "source_image", "class", "x0", "y0", "x1", "y1", "valid_fraction"])
        for split in ("train", "val", "test"):
            rows = sorted((row for row in source["samples"] if row["split"] == split), key=lambda row: row["file_name"])
            group, = {row["group"] for row in rows}
            folder = frames / split
            description = json.loads((folder / "_annotations.coco.json").read_text(encoding="utf-8"))["images"][0]
            if description["source_group"] != group:
                raise ValueError("Frame source group does not match corrected annotation group")
            with Image.open(folder / "images" / description["file_name"]) as opened:
                image = opened.convert("RGB")
            with Image.open(folder / "valid_pixels.png") as opened:
                coverage = np.asarray(opened).astype(np.uint8)
            pixels = np.concatenate((np.asarray(image), coverage[:, :, None]), axis=2)
            candidates = build_reference_boxes(rows, source["categories"], pixels)
            counts, low_coverage = Counter(), Counter()
            for label in LABELS:
                (output / split / label).mkdir(parents=True)
            for index, candidate in enumerate(candidates, 1):
                left, top, right, bottom = crop_box(candidate.box, *image.size)
                valid = float(np.mean(coverage[top:bottom, left:right] != 0))
                name = Path(split) / candidate.label / f"{group}_{index:05d}.jpg"
                image.crop((left, top, right, bottom)).save(output / name, quality=95, subsampling=0)
                source_image = rows[candidate.view]["file_name"]
                writer.writerow([name.as_posix(), split, group, source_image, candidate.label,
                                 left, top, right, bottom, round(valid, 6)])
                counts[candidate.label] += 1
                if valid < .95:
                    low_coverage[candidate.label] += 1
            report["splits"][split] = {"group": group, "crops": len(candidates), "classes": dict(counts),
                                      "crop_coverage_below_95pct": dict(low_coverage)}
    (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DATA / "siit_detection_v1/manifest.json")
    parser.add_argument("--frames", type=Path, default=DATA / "annotation_mopad_v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.manifest, args.frames, args.output)


if __name__ == "__main__":
    main()
