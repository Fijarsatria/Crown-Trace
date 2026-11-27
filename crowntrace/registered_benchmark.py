"""Retrospective point-level comparison on MOPAD source frames in pixel coordinates."""
import argparse
import csv
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image
import torch

from .audit_overlap import offsets
from .compact import CompactDetector
from .dataset import DATA, ROOT, annotations, manifest
from .inference import fuse
from .registered_count import registered
from .survey import DETECT_WEIGHTS, candidates, grid, select

TILE_SIZE = 1024
CONFIDENCE = 0.40
SEAM_BAND = 64
MATCH_FRACTIONS = (0.20, 0.25, 0.30)
METHODS = ("no_overlap", "overlap_score", "overlap_core")


class Frame:
    def __init__(self, pixels):
        self.pixels = pixels

    def window(self, rect, **_):
        x1, y1, x2, y2 = rect
        return self.pixels[y1:y2, x1:x2].tobytes()


def assemble(rows, images):
    """Prefer the source crop with most clearance to its nearest edge."""
    extents = [(offsets(row["file_name"]), row["file_name"]) for row in rows]
    width = max(x for (x, _), _ in extents) + 1024
    height = max(y for (_, y), _ in extents) + 1024
    frame = np.zeros((height, width, 4), dtype=np.uint8)
    best = np.full((height, width), -1, dtype=np.int16)
    border = np.minimum(np.arange(1024), np.arange(1024)[::-1]).astype(np.int16)
    clearance = np.minimum(border[:, None], border[None, :])
    for (x, y), name in sorted(extents):
        rows, cols = slice(y, y + 1024), slice(x, x + 1024)
        with Image.open(images / name) as source:
            rgb = np.asarray(source.convert("RGB"))
        patch = frame[rows, cols]
        scores = best[rows, cols]
        replace = clearance > scores
        patch[replace, :3] = rgb[replace]
        patch[replace, 3] = 255
        scores[replace] = clearance[replace]
    return frame


def centers(detections):
    return np.asarray([value.center for value in detections], dtype=np.float64).reshape((-1, 2))


def neighborhoods(a, b, radius):
    graph = []
    limit = radius * radius
    for start in range(0, len(a), 192):
        squared = np.square(a[start:start+192, None, :] - b[None, :, :]).sum(axis=-1)
        for row in squared:
            indices = np.flatnonzero(row <= limit)
            graph.append(sorted(map(int, indices), key=lambda i: (row[i], i)))
    return graph


def point_matches(reference, prediction, radius):
    """Deterministic maximum-cardinality pairs within the specified distance."""
    graph = neighborhoods(prediction, reference, radius)
    assigned = {}

    def assign(i, visited):
        for j in graph[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in assigned or assign(assigned[j], visited):
                assigned[j] = i
                return True
        return False

    order = sorted((i for i in range(len(graph)) if graph[i]),
                   key=lambda i: (len(graph[i]), np.linalg.norm(prediction[i] - reference[graph[i][0]]), i))
    for i in order:
        assign(i, set())
    return assigned, graph


def spacing(points):
    nearest = []
    for start in range(0, len(points), 192):
        rows = np.arange(start, min(len(points), start + 192))
        values = np.square(points[rows, None, :] - points[None, :, :]).sum(-1)
        values[np.arange(len(rows)), rows] = np.inf
        nearest.extend(np.sqrt(values.min(axis=1)))
    return float(np.median(nearest))


def near_seam(points, width, height, origin=(0, 0)):
    distances = []
    for axis, bound in enumerate((width, height)):
        seam = np.arange(TILE_SIZE - origin[axis] % TILE_SIZE, bound, TILE_SIZE)
        distances.append(np.abs(points[:, axis, None] - seam).min(axis=1)
                         if len(seam) else np.full(len(points), np.inf))
    return np.minimum(*distances) <= SEAM_BAND


def ratio(a, b):
    return round(a / b, 5) if b else None


def measure(reference, prediction, radius, seams):
    matched, graph = point_matches(reference, prediction, radius)
    used = set(matched.values())
    tp = len(used)
    fp, fn = len(prediction) - tp, len(reference) - tp
    duplicate = sum(i not in used and bool(neighbors) for i, neighbors in enumerate(graph))
    distances = [float(np.linalg.norm(reference[j] - prediction[i])) for j, i in matched.items()]
    metrics = {"reference": len(reference), "predicted": len(prediction), "tp": tp, "fp": fp, "fn": fn,
               "unmatched_within_radius_of_reference": duplicate, "count_bias": fp - fn,
               "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
               "f1": ratio(2 * tp, 2 * tp + fp + fn),
               "matched_error_median_px": round(float(np.median(distances)), 3) if distances else None,
               "seam_reference": int(seams.sum()),
               "seam_matched": sum(bool(seams[j]) for j in matched)}
    metrics["seam_recall"] = ratio(metrics["seam_matched"], metrics["seam_reference"])
    return metrics, matched


def build_reference_boxes(rows, categories, pixels):
    """Return the same deduplicated box proposals used by the point benchmark."""
    annotations_in_frame = [
        registered(det, row, i) for i, row in enumerate(rows)
        for det in annotations(row, categories, (1024, 1024))
    ]
    preserved = fuse(annotations_in_frame, center_ratio=0.28)
    points = centers(preserved)
    mask = pixels[:, :, 3]
    keep = [index for index, (x, y) in enumerate(points)
            if 0 <= int(y) < mask.shape[0] and 0 <= int(x) < mask.shape[1] and mask[int(y), int(x)]]
    return [preserved[index] for index in keep]


def build_reference(rows, categories, pixels):
    return centers(build_reference_boxes(rows, categories, pixels))


def infer_frame(pixels, detector, overlap, origin=(0, 0)):
    height, width = pixels.shape[:2]
    windows = list(grid(width, height, TILE_SIZE, overlap, origin))
    start = perf_counter()
    objects, _ = candidates(Frame(pixels), detector, CONFIDENCE, windows, lambda *args: None, TILE_SIZE)
    read_infer = perf_counter() - start
    start = perf_counter()
    score_only = fuse(objects, overlap=0.55, center_ratio=0.24)
    score_time = perf_counter() - start
    start = perf_counter()
    core = select(objects, windows) if overlap else score_only
    core_time = perf_counter() - start if overlap else score_time
    return len(windows), len(objects), read_infer, (score_only, score_time), (core, core_time)


def benchmark(args):
    sources = manifest(args.manifest)
    alignment = json.loads(args.audit.read_text(encoding="utf-8"))["alignment"]
    torch.set_num_threads(args.threads)
    detector = CompactDetector(args.weights)
    report = {"design": "retrospective within-site pixel-frame comparison; one validation and one test group",
              "reference": "registered, deduplicated MOPAD box centers; field census and CRS unavailable",
              "detector": detector.version, "tile_size": TILE_SIZE, "confidence": CONFIDENCE,
              "overlap": 0.20, "seam_band_px": SEAM_BAND, "threads": args.threads,
              "radius_rule": "0.25 times the median nearest-neighbor distance of reference points in validation",
              "results": {}}
    args.output.mkdir(parents=True, exist_ok=False)
    for split in ("val", "test"):
        rows = sorted((row for row in sources["samples"] if row["split"] == split),
                      key=lambda row: row["file_name"])
        group, = {row["group"] for row in rows}
        proof = alignment[group]
        if not proof["sampled_pairs"] or proof["median_gray_mad_xy"] >= 0.20 * proof["median_gray_mad_offset_control"]:
            raise ValueError(f"Offset MOPAD belum lolos audit: {group}")
        pixels = assemble(rows, args.images)
        height, width = pixels.shape[:2]
        reference = build_reference(rows, sources["categories"], pixels)
        if split == "val":
            estimate = spacing(reference)
            report["validation_median_spacing_px"] = round(estimate, 3)
            report["match_radius_px"] = round(0.25 * estimate, 3)
        seams = near_seam(reference, width, height)
        n0, p0, t0, plain, _ = infer_frame(pixels, detector, 0)
        n1, p1, t1, score, core = infer_frame(pixels, detector, 0.20)
        result = {"group": group, "source_crops": len(rows), "frame_size_px": [width, height],
                  "valid_pct": round(100 * np.count_nonzero(pixels[:, :, 3]) / (width * height), 3),
                  "methods": {}}
        with (args.output / f"{split}_points.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["method", "kind", "id", "x", "y", "pair_id", "near_seam"])
            for method, tile_count, raw, predict_time, (values, merge_time) in zip(
                METHODS, (n0, n1, n1), (p0, p1, p1), (t0, t1, t1), (plain, score, core), strict=True
            ):
                predicted = centers(values)
                metric, pairs = measure(reference, predicted, report["match_radius_px"], seams)
                metric.update(windows=tile_count, raw=raw, compute_s=round(predict_time + merge_time, 3))
                metric["tolerance_f1"] = {
                    str(fraction): measure(reference, predicted, fraction * estimate, seams)[0]["f1"]
                    for fraction in MATCH_FRACTIONS
                }
                result["methods"][method] = metric
                inverse = {i: j for j, i in pairs.items()}
                for j, (x, y) in enumerate(reference):
                    writer.writerow([method, "reference", j, x, y, pairs.get(j, ""), bool(seams[j])])
                for i, (x, y) in enumerate(predicted):
                    writer.writerow([method, "prediction", i, x, y, inverse.get(i, ""), ""])
        report["results"][split] = result
        print(f"{split} · {group} · {len(reference)} reference crowns", flush=True)
        if args.origin_audit:
            variants = [origin_result(pixels, detector, reference, report["match_radius_px"], origin)
                        for origin in ((384, 0), (0, 384), (384, 384))]
            result["origin_audit"] = variants
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["results"]["test"], indent=2))


def origin_result(pixels, detector, reference, radius, origin):
    height, width = pixels.shape[:2]
    seams = near_seam(reference, width, height, origin)
    n0, _, _, plain, _ = infer_frame(pixels, detector, 0, origin)
    n1, _, _, score, core = infer_frame(pixels, detector, 0.20, origin)
    methods = {}
    for method, n, (detections, _) in zip(METHODS, (n0, n1, n1), (plain, score, core), strict=True):
        metrics, _ = measure(reference, centers(detections), radius, seams)
        methods[method] = {**metrics, "windows": n}
    return {"origin_shift_px": origin, "methods": methods}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DATA / "mopad_site2_val_manifest.json")
    parser.add_argument("--images", type=Path, default=DATA / "mopad_site2_val_images")
    parser.add_argument("--audit", type=Path, default=ROOT / "results/overlap_screen.json")
    parser.add_argument("--weights", type=Path, default=DETECT_WEIGHTS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--origin-audit", action="store_true",
                        help="Also test three shifted tiling origins on the same two frames.")
    benchmark(parser.parse_args())


if __name__ == "__main__":
    main()
