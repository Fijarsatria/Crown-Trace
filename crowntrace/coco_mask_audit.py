"""Inspect COCO instance labels, image validity and possible partition leakage."""

import argparse
from collections import Counter, defaultdict
from hashlib import file_digest, sha256
import json
import math
from pathlib import Path

from PIL import Image


def polygon_area(points):
    return abs(sum(x * ny - nx * y for (x, y), (nx, ny) in
                   zip(points, points[1:] + points[:1]))) / 2


def inspect_mask(segmentation, width, height):
    if not segmentation:
        return "missing_mask"
    if isinstance(segmentation, dict):
        if segmentation.get("size") != [height, width]:
            return "invalid_rle_size"
        counts = segmentation.get("counts")
        if isinstance(counts, str):
            # Compressed COCO masks need a decoder and separate image-alignment checks.
            return "unverified_rle"
        if (not isinstance(counts, list) or not counts or
                not all(type(value) is int and value >= 0 for value in counts) or
                sum(counts) != width * height or sum(counts[1::2]) == 0):
            return "invalid_rle_counts"
        return "uncompressed_rle"
    if not isinstance(segmentation, list):
        return "invalid_polygon"
    polygons = []
    for ring in segmentation:
        if not isinstance(ring, list) or len(ring) < 6 or len(ring) % 2:
            return "invalid_polygon"
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in ring):
            return "invalid_polygon"
        points = list(zip(ring[::2], ring[1::2]))
        if len(set(points)) < 3 or polygon_area(points) < 1:
            return "invalid_polygon"
        if any(not (0 <= x <= width and 0 <= y <= height) for x, y in points):
            return "polygon_outside_image"
        polygons.append(points)
    if len(polygons) == 1:
        p = polygons[0]
        x, y = zip(*p)
        extent = (max(x) - min(x)) * (max(y) - min(y))
        if extent > 0 and polygon_area(p) / extent >= .99:
            return "review_rectangle_like"
    return "polygon"


def image_path(folder, name):
    if not isinstance(name, str) or not name:
        raise ValueError("Missing COCO image filename")
    root = folder.resolve()
    for candidate in (folder / name, folder / "images" / name):
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Image escapes partition: {name}")
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(name)


def repeated_across_splits(index):
    return [{"key": key, "images": members} for key, members in index.items()
            if len({member["split"] for member in members}) > 1]


def audit(root):
    content_hashes, original_names, source_groups = (defaultdict(list) for _ in range(3))
    report = {"root": str(root.resolve()), "partitions": {}}
    for split, names in (("train", ("train",)), ("val", ("val", "valid")), ("test", ("test",))):
        folders = [root / name for name in names if (root / name / "_annotations.coco.json").is_file()]
        if len(folders) != 1:
            raise ValueError(f"Expected one COCO folder for {split}: {names}")
        folder = folders[0]
        label_file = folder / "_annotations.coco.json"
        coco = json.loads(label_file.read_text(encoding="utf-8-sig"))
        images = {row["id"]: row for row in coco["images"]}
        categories = {row["id"]: row["name"] for row in coco["categories"]}
        if len(images) != len(coco["images"]) or len(categories) != len(coco["categories"]):
            raise ValueError(f"Duplicate image or category IDs in {split}")
        annotation_ids = {row["id"] for row in coco["annotations"]}
        if len(annotation_ids) != len(coco["annotations"]):
            raise ValueError(f"Duplicate annotation IDs in {split}")
        stats, class_counts = Counter(), Counter()
        per_image = defaultdict(Counter)
        bad_images = []
        mask_flags = []
        for row in images.values():
            name = row["file_name"]
            member = {"split": split, "image": name}
            source_group = row.get("source_group")
            if source_group:
                source_groups[str(source_group)].append(member)
            else:
                stats["images_without_source_group"] += 1
            if ".rf." in Path(name).name:
                original_names[Path(name).name.split(".rf.", 1)[0].casefold()].append(member)
            try:
                with Image.open(image_path(folder, name)) as image:
                    if image.size != (row["width"], row["height"]):
                        raise ValueError("Image size differs from COCO metadata")
                    rgb = image.convert("RGB")
                    digest = sha256(f"{rgb.width}x{rgb.height}:".encode())
                    digest.update(rgb.tobytes())
                content_hashes[digest.hexdigest()].append(member)
                stats["readable_images"] += 1
            except (OSError, ValueError) as error:
                bad_images.append({"image": name, "error": str(error)})
        for annotation in coco["annotations"]:
            image = images.get(annotation["image_id"])
            if image is None or annotation["category_id"] not in categories:
                stats["invalid_annotation_reference"] += 1
                continue
            class_counts[categories[annotation["category_id"]]] += 1
            if annotation.get("iscrowd", 0):
                stats["crowd_annotations"] += 1
            verdict = inspect_mask(annotation.get("segmentation"), image["width"], image["height"])
            per_image[image["id"]][verdict] += 1
            stats[verdict] += 1
            if verdict not in {"polygon", "uncompressed_rle"}:
                mask_flags.append({"annotation_id": annotation["id"], "image": image["file_name"],
                                   "issue": verdict})
        with label_file.open("rb") as handle:
            label_digest = file_digest(handle, "sha256").hexdigest()
        report["partitions"][split] = {
            "annotation_sha256": label_digest,
            "images": len(images), "annotations": len(coco["annotations"]),
            "classes": dict(class_counts), "image_errors": bad_images,
            "counts": dict(stats), "images_without_annotations": sum(not per_image[i] for i in images),
            "images_with_only_polygon_labels": sum(bool(per_image[i]) and set(per_image[i]) == {"polygon"} for i in images),
            "images_with_only_checked_mask_labels": sum(bool(per_image[i]) and
                set(per_image[i]) <= {"polygon", "uncompressed_rle"} for i in images),
            "mask_flags": mask_flags,
        }
    report["cross_split_pixel_duplicates"] = repeated_across_splits(content_hashes)
    report["cross_split_roboflow_name_matches"] = repeated_across_splits(original_names)
    report["cross_split_source_groups"] = repeated_across_splits(source_groups)
    report["interpretation"] = ("Geometry and duplicate checks only. The report cannot prove that all visible crowns "
                                "are annotated, where the images originated, or that polygons are human-reviewed. "
                                "Review mask flags and validate spatial provenance before use.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    for split, result in report["partitions"].items():
        print(split, result["images"], "images", result["annotations"], "annotations", result["counts"])
    for key in ("cross_split_pixel_duplicates", "cross_split_roboflow_name_matches", "cross_split_source_groups"):
        print(key, len(report[key]))


if __name__ == "__main__":
    main()
