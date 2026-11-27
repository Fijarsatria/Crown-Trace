"""Verify the public SIIT label release and pair it with the local MOPAD imagery."""

import argparse
from collections import Counter
from hashlib import sha1
import json
import math
from pathlib import Path
import re

import numpy as np

from .dataset import DATA, audit, export_yolo, manifest
from .inference import LABELS


def read_source(source):
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    if tuple(index["classes"]) != LABELS:
        raise ValueError("SIIT category order differs from detector categories")
    expected = {item["name"]: item for item in index["files"]}
    if len(expected) != index["expected_count"]:
        raise ValueError("Duplicate source filenames")
    labels = {}
    for path in sorted(source.glob("labels_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            metadata = expected[row["name"]]
            payload = row["content"].encode("utf-8")
            digest = sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()
            if digest != metadata["sha"] or digest != row["sha"] or len(payload) != metadata["byte_size"]:
                raise ValueError(f"Git blob integrity mismatch: {row['name']}")
            name = re.fullmatch(r"(\d+_\d+_\d+_\d+)_jpg\.rf\.[a-f0-9]+\.txt", row["name"])
            if not name:
                raise ValueError(f"Unrecognized export filename: {row['name']}")
            image = name[1] + ".jpg"
            if image in labels:
                raise ValueError(f"Two labels for {image}")
            labels[image] = row["content"]
    if len(labels) != len(expected):
        raise ValueError(f"Incomplete SIIT source: {len(labels)} / {len(expected)} files")
    return index, labels


def parse_boxes(text, size=1024):
    objects = []
    clipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError("Expected one class and four normalized box coordinates")
        cls = int(fields[0])
        cx, cy, width, height = map(float, fields[1:])
        if cls not in range(len(LABELS)) or not all(math.isfinite(v) for v in (cx, cy, width, height)):
            raise ValueError("Invalid SIIT class or coordinate")
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < width <= 1 and 0 < height <= 1):
            raise ValueError("Coordinates are not normalized boxes")
        x0, y0 = cx - width / 2, cy - height / 2
        x1, y1 = cx + width / 2, cy + height / 2
        if min(x0, y0) < -0.02 or max(x1, y1) > 1.02:
            raise ValueError("Box extends substantially outside the original image")
        if min(x0, y0) < -1e-8 or max(x1, y1) > 1 + 1e-8:
            clipped += 1
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(1, x1), min(1, y1)
        objects.append([cls, x0 * size, y0 * size, (x1 - x0) * size, (y1 - y0) * size])
    return objects, clipped


def compare_boxes(old, new, categories):
    if not old or not new:
        return [], Counter()
    def xyxy(rows):
        values = np.array([obj[1:] for obj in rows], dtype=float)
        values[:, 2:] += values[:, :2]
        return values
    a, b = xyxy(old), xyxy(new)
    a = np.clip(a, 0, 1024)
    intersection = np.maximum(0, np.minimum(a[:, None, 2:], b[None, :, 2:]) -
                              np.maximum(a[:, None, :2], b[None, :, :2])).prod(axis=2)
    union = (a[:, 2:] - a[:, :2]).prod(axis=1)[:, None] + (b[:, 2:] - b[:, :2]).prod(axis=1)[None, :] - intersection
    scores = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
    pairs = np.argwhere(scores >= .5).tolist()
    pairs.sort(key=lambda pair: -scores[tuple(pair)])
    used_old, used_new = set(), set()
    matched, changes = [], Counter()
    for i, j in pairs:
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        matched.append(float(scores[i, j]))
        changes[f"{categories[str(old[i][0])]} -> {LABELS[new[j][0]]}"] += 1
    return matched, changes


def prepare(source_dir, original_path, images, output):
    metadata, labels = read_source(source_dir)
    original = manifest(original_path)
    names = {row["file_name"] for row in original["samples"]}
    if set(labels) != names:
        raise ValueError("Corrected labels do not match the complete MOPAD image list")
    result = {"source": original["source"], "annotation_source": metadata["repository"],
              "annotation_source_tree_sha": metadata["source_listing_tree_sha"],
              "selection": "SIIT corrected boxes, original CrownTrace spatial groups retained",
              "categories": {str(i): name for i, name in enumerate(LABELS)}, "samples": []}
    diagnostics = {split: {"old": Counter(), "new": Counter(), "transitions": Counter(),
                           "ious": [], "clipped": 0, "aligned_images": 0, "images": 0}
                   for split in ("train", "val", "test")}
    for row in original["samples"]:
        objects, clipped = parse_boxes(labels[row["file_name"]])
        evidence = diagnostics[row["split"]]
        scores, changes = compare_boxes(row["objects"], objects, original["categories"])
        evidence["images"] += 1
        evidence["aligned_images"] += len(scores) >= .8 * min(len(objects), len(row["objects"]))
        evidence["old"].update(original["categories"][str(obj[0])] for obj in row["objects"])
        evidence["new"].update(LABELS[obj[0]] for obj in objects)
        evidence["ious"].extend(scores)
        evidence["transitions"].update(changes)
        evidence["clipped"] += clipped
        result["samples"].append({**row, "objects": objects})
    report = {"source_files_sha_verified": len(labels), "image_count": len(names),
              "label_kind": "bounding boxes with visual growth condition categories",
              "partition_policy": "unchanged filename-prefix groups within MOPAD Site 2",
              "splits": {}}
    for split, evidence in diagnostics.items():
        scores = evidence["ious"]
        old, new = sum(evidence["old"].values()), sum(evidence["new"].values())
        report["splits"][split] = {
            "images": evidence["images"], "old_classes": dict(evidence["old"]), "new_classes": dict(evidence["new"]),
            "original_boxes": old, "corrected_boxes": new, "greedy_matches_iou_ge_0_5": len(scores),
            "match_fraction_of_smaller_set": round(len(scores) / min(old, new), 5),
            "matched_median_iou": round(float(np.median(scores)), 5),
            "unmatched_original": old - len(scores), "unmatched_corrected": new - len(scores),
            "images_with_at_least_80pct_match": evidence["aligned_images"],
            "clipped_edge_boxes": evidence["clipped"], "matched_label_transitions": dict(evidence["transitions"]),
        }
    if any(row["match_fraction_of_smaller_set"] < .8 or row["matched_median_iou"] < .9
           for row in report["splits"].values()):
        raise ValueError(f"Geometry may differ between image export and original: {report['splits']}")
    # Reuse the standard export, preserving original imagery and names across the three splits.
    export_yolo(result, images, output)
    report["image_integrity"] = audit(result, images)
    (output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "image_integrity"}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DATA / "siit_corrected_source")
    parser.add_argument("--manifest", type=Path, default=DATA / "mopad_site2_val_manifest.json")
    parser.add_argument("--images", type=Path, default=DATA / "mopad_site2_val_images")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.manifest, args.images, args.output)


if __name__ == "__main__":
    main()
