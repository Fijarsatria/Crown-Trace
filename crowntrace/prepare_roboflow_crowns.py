"""Build a reproducible family-grouped COCO partition from the Trees v3 export."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from .coco_mask_audit import audit, image_path

PARTITIONS = ("train", "val", "test")
SIMILARITY = 0.965


def join_components(size, edges):
    parents = list(range(size))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for left, right in edges:
        first, second = root(left), root(right)
        parents[max(first, second)] = min(first, second)
    groups = defaultdict(list)
    for index in range(size):
        groups[root(index)].append(index)
    return list(groups.values())


def family(name):
    stem = Path(name).stem.casefold()
    numeric = re.fullmatch(r"(\d+)a+", stem)
    if numeric:
        return f"number-{int(numeric[1])}"
    quadrant = re.match(r"^(\d+)-(kiri|kanan)-", stem)
    if quadrant:
        return f"quadrants-{quadrant[1]}"
    return stem


def prepare(source, output, seed):
    inspected = audit(source)
    records, thumbnails, brightness, excluded = [], [], [], []
    for split in PARTITIONS:
        folder = source / ("valid" if split == "val" else split)
        part = inspected["partitions"][split]
        if part["image_errors"] or part["counts"].get("invalid_annotation_reference"):
            raise ValueError(f"Broken references or image files in {split}")
        document = json.loads((folder / "_annotations.coco.json").read_text(encoding="utf-8-sig"))
        names = {row["id"]: row["name"] for row in document["categories"]}
        annotations = defaultdict(list)
        for annotation in document["annotations"]:
            if names[annotation["category_id"]] != "oil-palm-trees-xD2I" or annotation.get("iscrowd", 0):
                raise ValueError("Expected individual oil palm crowns")
            annotations[annotation["image_id"]].append(annotation)
        flags = defaultdict(list)
        for entry in part["mask_flags"]:
            flags[entry["image"]].append(entry["issue"])
        for row in document["images"]:
            original = row["extra"]["name"]
            reasons = list(flags[row["file_name"]])
            if re.match(r"^\d{5}_\d{4,5}_\d+_\d+\.", original):
                reasons.append("mopad_style_name_requires_provenance_review")
            objects = annotations[row["id"]]
            if not objects or any(len(a["segmentation"]) != 1 for a in objects):
                reasons.append("missing_or_multipart_polygon")
            if reasons:
                excluded.append({"split": split, "file_name": row["file_name"], "original_name": original,
                                 "annotations": len(objects), "reasons": reasons})
                continue
            path = image_path(folder, row["file_name"])
            with Image.open(path) as image:
                gray = image.convert("L").resize((40, 40), Image.Resampling.BILINEAR)
                thumb = np.asarray(gray, dtype=np.float32).reshape(-1)
            brightness.append(float(thumb.mean()))
            thumb -= thumb.mean()
            thumbnails.append(thumb / max(1.0, np.linalg.norm(thumb)))
            records.append({"image": row, "path": path, "original_name": original, "objects": objects,
                            "roboflow_split": split})
    pixels = np.stack(thumbnails)
    similar = np.tril(pixels @ pixels.T, k=-1)
    links = list(zip(*np.where(similar >= SIMILARITY), strict=True))
    visual_sets = join_components(len(records), links)
    matches = []
    representatives = []
    for ids in visual_sets:
        preferred = [i for i in ids if records[i]["roboflow_split"] != "train"] or ids
        median = float(np.median([brightness[i] for i in ids]))
        winner = min(preferred, key=lambda i: (abs(brightness[i] - median), records[i]["path"].name))
        representatives.append(winner)
        if len(ids) > 1:
            matches.append({"retained": records[winner]["path"].name,
                            "members": [{"image": records[i]["path"].name, "split": records[i]["roboflow_split"]}
                                        for i in ids]})
    group_links = list(links)
    first_seen = {}
    for i, record in enumerate(records):
        key = family(record["original_name"])
        group_links.append((i, first_seen.setdefault(key, i)))
    retained = set(representatives)
    groups = []
    for ids in join_components(len(records), group_links):
        key = "+".join(sorted({family(records[i]["original_name"]) for i in ids}))
        selected = [i for i in ids if i in retained]
        digest = sha256(f"{seed}/{key}".encode()).hexdigest()
        groups.append((key, selected, digest))
    totals = dict.fromkeys(PARTITIONS, 0)
    sizes = {split: len(retained) * ratio for split, ratio in zip(PARTITIONS, (0.70, .15, .15))}
    plans = defaultdict(list)
    for key, selected, _ in sorted(groups, key=lambda item: (-len(item[1]), item[2])):
        split = max(PARTITIONS, key=lambda part: (sizes[part] - totals[part]) / sizes[part])
        plans[split].extend((key, records[index]) for index in selected)
        totals[split] += len(selected)
    if not all(totals.values()):
        raise ValueError("Expected nonempty training, validation and test sets")
    summary = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as staging:
        destination = Path(staging) / "result"
        for split in PARTITIONS:
            directory = destination / split
            directory.mkdir(parents=True)
            image_rows, objects = [], []
            for group, record in sorted(plans[split], key=lambda p: (p[0], p[1]["path"].name)):
                image = {**record["image"], "id": len(image_rows) + 1, "source_group": group,
                         "roboflow_split": record["roboflow_split"]}
                shutil.copyfile(record["path"], directory / image["file_name"])
                image_rows.append(image)
                for row in record["objects"]:
                    objects.append({**row, "id": len(objects) + 1, "image_id": image["id"], "category_id": 1})
            grouped_coco = {"info": document["info"], "licenses": document["licenses"],
                            "images": image_rows, "annotations": objects,
                            "categories": [{"id": 1, "name": "palm_crown"}]}
            (directory / "_annotations.coco.json").write_text(json.dumps(grouped_coco), encoding="utf-8")
            summary[split] = {"images": len(image_rows), "annotations": len(objects),
                              "families": sorted({row["source_group"] for row in image_rows}),
                              "original_split_counts": dict(Counter(row["roboflow_split"] for row in image_rows))}
        for name in ("README.roboflow.txt", "README.dataset.txt"):
            shutil.copyfile(source / name, destination / name)
        verification = audit(destination)
        leakage_keys = ("cross_split_pixel_duplicates", "cross_split_roboflow_name_matches",
                        "cross_split_source_groups")
        if any(verification[key] for key in leakage_keys) or any(
            part["image_errors"] or part["mask_flags"] for part in verification["partitions"].values()
        ):
            raise ValueError("Regrouped COCO failed image, mask or partition checks")
        report = {
            "source": str(source.resolve()), "seed": seed, "similarity_threshold": SIMILARITY,
            "scope": "Duplicate and filename-family grouping. Geographic origins and full crown coverage are unverified.",
            "source_images": sum(p["images"] for p in inspected["partitions"].values()),
            "excluded": excluded, "removed_visual_variants": len(records) - len(retained),
            "visual_clusters": matches,
            "visual_clusters_cross_original_splits": sum(len({i["split"] for i in m["members"]}) > 1 for m in matches),
            "verified_cross_split_matches": {key: len(verification[key]) for key in leakage_keys},
            "splits": summary,
        }
        (destination / "preparation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            raise FileExistsError(output)
        destination.rename(output)
    print(json.dumps({**totals, "excluded": len(excluded), "visual_variants_removed": len(records) - len(retained),
                      "visual_clusters_cross_original_splits": report["visual_clusters_cross_original_splits"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output directory.")
    prepare(args.source, args.output, args.seed)


if __name__ == "__main__":
    main()
