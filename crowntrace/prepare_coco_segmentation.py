"""Export single-ring, inspected crown polygons as a YOLO segmentation dataset."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil

from .coco_mask_audit import audit, image_path, inspect_mask


def prepare(root, output, selected_names):
    findings = audit(root)
    for key in ("cross_split_pixel_duplicates", "cross_split_roboflow_name_matches", "cross_split_source_groups"):
        if findings[key]:
            raise ValueError(f"Resolve possible leakage before exporting: {key} ({len(findings[key])})")
    if any(part["image_errors"] or part["counts"].get("invalid_annotation_reference")
           for part in findings["partitions"].values()):
        raise ValueError("Broken images or annotation references; run coco_mask_audit for details")
    if output.exists():
        raise FileExistsError(output)
    plans, summary = {}, {}
    for split in ("train", "val", "test"):
        folder = root / split
        if split == "val" and not folder.is_dir():
            folder = root / "valid"
        source = json.loads((folder / "_annotations.coco.json").read_text(encoding="utf-8-sig"))
        categories = {cat["id"]: cat["name"] for cat in source["categories"]}
        if not set(selected_names) <= set(categories.values()):
            raise ValueError(f"Unknown crown categories in {split}: {categories}")
        images = {row["id"]: row for row in source["images"]}
        labels, counts = defaultdict(list), Counter()
        for row in source["annotations"]:
            category = categories[row["category_id"]]
            if category not in selected_names:
                continue
            if row.get("iscrowd", 0):
                raise ValueError("Crowd annotations need review before individual crown export")
            image = images[row["image_id"]]
            width, height = image["width"], image["height"]
            mask = row.get("segmentation")
            status = inspect_mask(mask, width, height)
            if status != "polygon" or len(mask) != 1:
                raise ValueError(f"Review crown mask {row['id']} in {split}: {status}. "
                                 "This export preserves single-ring polygons only; use COCO for other mask types.")
            normalized = [number / (width if index % 2 == 0 else height)
                          for index, number in enumerate(mask[0])]
            labels[row["image_id"]].append("0 " + " ".join(f"{value:.8f}" for value in normalized))
            counts[category] += 1
        if not sum(counts.values()):
            raise ValueError(f"No selected crown annotations in {split}")
        plans[split] = [(image_path(folder, row["file_name"]), row["id"], labels[row["id"]])
                        for row in images.values()]
        summary[split] = {"images": len(images), "selected_objects": sum(counts.values()),
                          "source_classes": dict(counts), "images_without_selected_objects":
                          sum(not labels[row["id"]] for row in images.values())}
    output.mkdir(parents=True, exist_ok=False)
    for split, rows in plans.items():
        image_dir, label_dir = output / "images" / split, output / "labels" / split
        image_dir.mkdir(parents=True)
        label_dir.mkdir(parents=True)
        for source, image_id, lines in rows:
            # Use integer IDs in exported filenames so nesting and repeated source stems are harmless.
            if type(image_id) is not int:
                raise ValueError("COCO image ID must be an integer")
            filename = f"{image_id}_{source.name}"
            shutil.copyfile(source, image_dir / filename)
            (label_dir / Path(filename).with_suffix(".txt")).write_text("\n".join(lines), encoding="utf-8")
    config = {"path": output.resolve().as_posix(), "train": "images/train", "val": "images/val",
              "test": "images/test", "names": ["palm_crown"]}
    (output / "data.yaml").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    report = {"source": str(root.resolve()), "source_categories_selected": selected_names, "splits": summary,
              "scope": "format and duplicate checks; independent complete mask coverage and provenance require review",
              "source_audit": findings}
    (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--categories", nargs="+", required=True)
    args = parser.parse_args()
    prepare(args.root, args.output, args.categories)


if __name__ == "__main__":
    main()
