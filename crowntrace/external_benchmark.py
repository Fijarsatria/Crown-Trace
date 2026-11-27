"""Run CrownTrace's frozen, class-agnostic transfer benchmark on Mendeley v2."""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import file_digest, sha256
from io import BytesIO
import csv
import json
import math
from pathlib import Path, PurePosixPath
import platform
from tempfile import TemporaryDirectory

from PIL import Image

from .benchmark import operating_matches, summarize
from .dataset import ROOT
from .inference import Detection, infer


DATASET_DOI = "10.17632/s23jvbpnr3.2"
SOURCE_LABELS = {"ganoderma_affected_palm", "survey_negative_normal_palm"}
CONFIGS = {"full_416": ("full_1024", 0.40), "exhaustive": ("exhaustive", 0.45)}
PREDICTION_FLOOR = 0.05
SPAN = 512
CENTER_RATIO = 0.28


def load_test_annotations(path):
    if path.name != "instances_test.json":
        raise ValueError("Pass the official instances_test.json, without merging splits.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    categories = {row["id"]: row["name"] for row in data["categories"]}
    if len(categories) != 2 or set(categories.values()) != SOURCE_LABELS:
        raise ValueError("The category names do not match the Mendeley v2 release.")
    images = {row["id"]: row for row in data["images"]}
    names = {row["file_name"] for row in images.values()}
    if len(data["images"]) != 118 or len(images) != 118 or len(names) != 118:
        raise ValueError("Expected 118 distinct tiles in the official test partition.")
    objects = defaultdict(list)
    counts = Counter()
    ids = set()
    for row in data["annotations"]:
        if row["id"] in ids or row["image_id"] not in images or row.get("iscrowd", 0):
            raise ValueError(f"Duplicate, unknown-image, or crowd annotation {row['id']}.")
        ids.add(row["id"])
        x, y, w, h = row["bbox"]
        if not all(map(math.isfinite, (x, y, w, h))) or w <= 0 or h <= 0:
            raise ValueError(f"Invalid source box {row['id']}.")
        box = (max(0, x), max(0, y), min(640, x + w), min(640, y + h))
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError(f"Source box {row['id']} lies outside the image.")
        objects[row["image_id"]].append(Detection(box, "crown", 1.0))
        counts[categories[row["category_id"]]] += 1
    if not ids:
        raise ValueError("The test annotations contain no crowns.")
    return list(images.values()), objects, dict.fromkeys(sorted(SOURCE_LABELS), 0) | dict(counts)


def resolve_images(root, rows):
    root = root.resolve(strict=True)
    by_name = defaultdict(list)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            if path.resolve().is_relative_to(root):
                by_name[path.name].append(path)
    resolved = {}
    for row in rows:
        name = row["file_name"]
        relative = PurePosixPath(name.replace("\\", "/"))
        if relative.is_absolute() or not relative.name or ".." in relative.parts or ":" in name:
            raise ValueError(f"Invalid annotation filename: {name!r}")
        if (row["width"], row["height"]) != (640, 640):
            raise ValueError(f"Unexpected annotated image size: {name!r}")
        candidates = by_name[relative.name]
        matched = [path for path in candidates if tuple(path.parts[-len(relative.parts):]) == relative.parts]
        choices = matched or candidates
        if len(choices) != 1:
            raise ValueError(f"Expected one image for {name!r}, found {len(choices)} under {root}.")
        resolved[name] = choices[0]
    if len(set(resolved.values())) != len(rows):
        raise ValueError("More than one COCO record points to the same image path.")
    return resolved


def write_results(output, metadata, results, images):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix="crowntrace-") as temporary:
        stage = Path(temporary) / "result"
        stage.mkdir()
        (stage / "summary.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        with (stage / "per_tile.csv").open("w", newline="", encoding="utf-8") as csv_file, \
             (stage / "predictions.jsonl").open("w", encoding="utf-8") as json_file:
            writer = csv.writer(csv_file)
            writer.writerow(["method", "image", "sha256", "reference_count", "predicted_count", "error",
                             "passes", "ms", "tp50", "fp50", "fn50"])
            for name, rows in results.items():
                _, threshold = CONFIGS[name]
                for image, actual, prediction, stats in rows:
                    kept = [det for det in prediction if det.score >= threshold]
                    writer.writerow([name, image, images[image], len(actual), len(kept), len(kept) - len(actual),
                                     stats["calls"], round(stats["elapsed_ms"], 3), *operating_matches(actual, kept)])
                    json_file.write(json.dumps({"method": name, "image": image, "tiles": stats["tiles"],
                                                "detections": [asdict(det) for det in prediction]}) + "\n")
        if output.exists():
            raise FileExistsError(output)
        stage.rename(output)


def run(coco, image_root, weights, output, threads):
    import torch
    from .compact import CompactDetector

    if output.exists():
        raise FileExistsError(output)
    rows, objects, support = load_test_annotations(coco)
    paths = resolve_images(image_root, rows)
    torch.set_num_threads(threads)
    detector = CompactDetector(weights)
    results = {name: [] for name in CONFIGS}
    image_hashes = {}
    ordered_configs = tuple(CONFIGS.items())
    for index, row in enumerate(rows):
        name = row["file_name"]
        raw = paths[name].read_bytes()
        image_hashes[name] = sha256(raw).hexdigest()
        with Image.open(BytesIO(raw)) as source:
            image = source.convert("RGB")
        if image.size != (640, 640):
            raise ValueError(f"Decoded tile {name!r} does not have the release dimensions.")
        if index == 0:
            detector.predict(image, 640)
            detector.predict(image, 1024)
        offset = index % len(ordered_configs)
        for label, (method, _) in ordered_configs[offset:] + ordered_configs[:offset]:
            boxes, stats = infer(image, detector, method=method, threshold=PREDICTION_FLOOR,
                                 span=SPAN, center_ratio=CENTER_RATIO)
            results[label].append((name, objects[row["id"]], boxes, stats))
    metrics = {}
    for name, items in results.items():
        _, threshold = CONFIGS[name]
        values = summarize(items, threshold, labels=())
        metrics[name] = {key: value for key, value in values.items()
                         if key != "count_mae_by_class" and not key.startswith("condition_")}
    with weights.open("rb") as source:
        weights_hash = file_digest(source, "sha256").hexdigest()
    metadata = {"dataset_doi": DATASET_DOI, "split": "official test", "source_label_support": support,
                "annotation_sha256": sha256(coco.read_bytes()).hexdigest(), "weights_sha256": weights_hash,
                "model": detector.version, "device": "cpu", "threads": threads, "machine": platform.platform(),
                "span_pixels": SPAN, "center_merge_ratio": CENTER_RATIO, "metrics": metrics}
    write_results(output.resolve(), metadata, results, image_hashes)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coco", required=True, type=Path)
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--weights", type=Path, default=ROOT / "results/expanded_train/best.pth")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be positive.")
    try:
        payload = run(args.coco, args.images, args.weights, args.output, args.threads)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Benchmark stopped: {error}\n")
    for name, result in payload["metrics"].items():
        print(f"{name}: MAE {result['count_mae']:.2f}, AP50 {result['crown_ap50']:.3f}, "
              f"median {result['median_ms']:.1f} ms, {result['detector_calls']:.0f} passes")


if __name__ == "__main__":
    main()
