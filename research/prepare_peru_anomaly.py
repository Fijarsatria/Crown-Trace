"""Prepare source-grouped COCO boxes from the public Peru palm dataset."""

import argparse
from collections import Counter, defaultdict
import csv
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from PIL import Image

PARTITIONS = ("test", "valid", "train")
CLASSES = ("PalmSan", "PalmAnom")
SOURCE = re.compile(r"(imgPalm\d+)_JPG\.rf\.[0-9a-f]+\.jpg", flags=re.IGNORECASE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    categories = [{"id": i, "name": name} for i, name in enumerate(CLASSES, 1)]
    report = {"doi": "10.17632/nh7d23dgnw.1", "original": {}, "prepared": {},
              "rule": "One image per filename stem and exact image hash. Select test before valid before train."}
    groups = {}
    digests = set()
    original_stems = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.archive) as archive, TemporaryDirectory(dir=args.output.parent) as temporary:
        stage = Path(temporary) / "prepared"
        stage.mkdir()
        for partition in PARTITIONS:
            rows = csv.DictReader(StringIO(archive.read(f"{partition}/_annotations.csv").decode("utf-8-sig")))
            labels = defaultdict(list)
            for row in rows:
                labels[row["filename"]].append(row)
            names = sorted({name for name in archive.namelist() if name.startswith(partition + "/") and name.endswith(".jpg")})
            if set(labels) - {Path(name).name for name in names}:
                raise ValueError(f"Annotations refer to missing images in {partition}")
            originals = Counter(row["class"] for group in labels.values() for row in group)
            seen = set()
            images, objects = [], []
            folder = stage / partition
            folder.mkdir()
            for entry in names:
                filename = entry.split("/", 1)[1]
                match = SOURCE.fullmatch(filename)
                if not match:
                    raise ValueError(f"Unexpected image path {entry}")
                stem = match[1].lower()
                seen.add(stem)
                if stem in groups:
                    continue
                raw = archive.read(entry)
                digest = sha256(raw).hexdigest()
                if digest in digests:
                    continue
                with Image.open(BytesIO(raw)) as handle:
                    width, height = handle.size
                    handle.verify()
                image_id = len(images) + 1
                boxes = set()
                for row in labels[filename]:
                    if (int(row["width"]), int(row["height"])) != (width, height):
                        raise ValueError(f"Annotation dimensions differ on {filename}")
                    a, b, c, d = (int(row[key]) for key in ("xmin", "ymin", "xmax", "ymax"))
                    if not (0 <= a < c <= width and 0 <= b < d <= height):
                        raise ValueError(f"Invalid box in {filename}")
                    category = CLASSES.index(row["class"]) + 1
                    boxes.add((category, a, b, c - a, d - b))
                for category, x, y, w, h in sorted(boxes):
                    objects.append({"id": len(objects) + 1, "image_id": image_id, "category_id": category,
                                    "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0})
                groups[stem] = partition
                digests.add(digest)
                (folder / filename).write_bytes(raw)
                images.append({"id": image_id, "file_name": filename, "width": width, "height": height,
                               "source_stem": stem, "sha256": digest})
            original_stems[partition] = seen
            report["original"][partition] = {"images": len(names), "source_stems": len(seen), "labels": originals}
            counts = Counter(CLASSES[row["category_id"] - 1] for row in objects)
            report["prepared"][partition] = {"images": len(images), "labels": counts}
            coco = {"images": images, "annotations": objects, "categories": categories}
            (folder / "_annotations.coco.json").write_text(json.dumps(coco, indent=2) + "\n", encoding="utf-8")
        report["shared_original_stems"] = {f"{a}/{b}": len(original_stems[a] & original_stems[b])
                                            for a, b in (("train", "valid"), ("train", "test"), ("valid", "test"))}
        report["geographic_independence_verified"] = False
        (stage / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stage.rename(args.output)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
