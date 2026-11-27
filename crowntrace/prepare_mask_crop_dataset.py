"""Package the existing manually estimated target contours as RGB/mask crop pairs."""

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .dataset import DATA


def prepare(labels, source_images, output):
    source = json.loads(labels.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    spatial_groups = {}
    for index, row in enumerate(source["samples"], 1):
        split, group = row["split"], row["group"]
        if split not in ("train", "val", "test") or spatial_groups.setdefault(group, split) != split:
            raise ValueError("A source group must belong to only one split")
        if Path(row["image"]).name != row["image"]:
            raise ValueError("Unexpected image filename")
        with Image.open(source_images / row["image"]) as image:
            crop = image.convert("RGB").crop(row["crop"])
        mask = Image.new("L", crop.size)
        ImageDraw.Draw(mask).polygon([(x * crop.width / 128, y * crop.height / 128)
                                      for x, y in row["polygon_128"]], fill=255)
        area = int(np.count_nonzero(np.asarray(mask)))
        if area == 0:
            raise ValueError(f"Empty mask in trace {index}")
        name = f"target_{index:03d}.png"
        image_path, mask_path = Path("images") / split / name, Path("masks") / split / name
        for path in (image_path, mask_path):
            (output / path).parent.mkdir(parents=True, exist_ok=True)
        crop.save(output / image_path)
        mask.save(output / mask_path)
        rows.append({**row, "crop_image": image_path.as_posix(), "binary_mask": mask_path.as_posix(),
                     "mask_pixels": area, "review_status": "single-pass approximate trace"})
    report = {"source": source["source"], "label_method": source["label_method"],
              "scope": "selected target crops, neighboring crowns may be unlabeled; no exhaustive image labels",
              "splits": dict(Counter(row["split"] for row in rows)), "samples": rows}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("label_method", "scope", "splits")}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DATA / "crown_masks_mopad.json")
    parser.add_argument("--images", type=Path, default=DATA / "mopad_site2_val_images")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.labels, args.images, args.output)


if __name__ == "__main__":
    main()
