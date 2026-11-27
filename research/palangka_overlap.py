"""Audit photogrammetric overlap between the published Palangka Raya JPGs."""

import argparse
import json
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def photo_features(path, width, sift):
    with Image.open(path) as image:
        height = round(image.height * width / image.width)
        gray = np.asarray(image.convert("L").resize((width, height), Image.Resampling.LANCZOS))
    keypoints, descriptors = sift.detectAndCompute(gray, None)
    points = np.asarray([p.pt for p in keypoints], dtype=np.float32).reshape(-1, 2)
    index = cv2.flann_Index(descriptors, {"algorithm": 1, "trees": 4}) if len(points) >= 2 else None
    return points, descriptors, index


def ratio_matches(index, descriptors):
    neighbors, distances = index.knnSearch(descriptors, 2, params={"checks": 24})
    return np.where(distances[:, 0] < 0.78**2 * distances[:, 1], neighbors[:, 0], -1)


def project(points, matrix):
    return cv2.perspectiveTransform(points[None], matrix)[0]


def coverage(points, width, height):
    return cv2.contourArea(cv2.convexHull(points)) / (width * height)


def compare(left, right, width, height):
    ap, ad, ai = left
    bp, bd, bi = right
    if ai is None or bi is None:
        return None, 0
    forward = ratio_matches(bi, ad)
    rows = np.flatnonzero(forward >= 0)
    if len(rows) < 32:
        return None, len(rows)
    reverse = ratio_matches(ai, bd)
    rows = rows[reverse[forward[rows]] == rows]
    if len(rows) < 32:
        return None, len(rows)
    a, b = ap[rows], bp[forward[rows]]
    order = np.random.default_rng(841).permutation(len(rows))
    is_holdout = np.arange(len(order)) % 4 == 0
    train, hold = order[~is_holdout], order[is_holdout]
    matrix, matches = cv2.findHomography(a[train], b[train], cv2.USAC_MAGSAC, 3.0, maxIters=3000)
    if matrix is None or matches.sum() < 22:
        return None, len(rows)
    valid = train[matches.ravel().astype(bool)]
    invert = np.linalg.inv(matrix) if abs(np.linalg.det(matrix)) > 1e-10 else None
    if invert is None:
        return None, len(rows)
    error = np.maximum(np.linalg.norm(project(a[hold], matrix) - b[hold], axis=1),
                       np.linalg.norm(project(b[hold], invert) - a[hold], axis=1))
    confirmed = hold[error <= 4.0]
    if len(confirmed) < 8 or len(confirmed) / len(hold) < 0.55:
        return None, len(rows)
    matched = np.concatenate([valid, confirmed])
    spread = min(coverage(a[matched], width, height), coverage(b[matched], width, height))
    if spread < 0.035:
        return None, len(rows)
    corners = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    transformed = project(corners, matrix)
    area = cv2.contourArea(transformed, oriented=True)
    if not cv2.isContourConvex(transformed) or not 0.50 < area / (width * height) < 2.0:
        return None, len(rows)
    intersection, _ = cv2.intersectConvexConvex(transformed, corners)
    overlap = intersection / min(area, width * height)
    if overlap < 0.17:
        return None, len(rows)
    result = {
        "tentative": len(rows), "fit_inliers": len(valid), "holdout_total": len(hold),
        "holdout_inliers": len(confirmed), "holdout_median_px": float(np.median(error)),
        "holdout_inlier_median_px": float(np.median(error[error <= 4.0])),
        "hull_fraction": float(spread), "overlap_fraction": float(overlap),
        "a_to_b": matrix.tolist(),
        "fit_points_a": a[valid].tolist(), "fit_points_b": b[valid].tolist(),
        "hold_points_a": a[confirmed].tolist(), "hold_points_b": b[confirmed].tolist(),
    }
    return result, len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photos", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--features", type=int, default=1350)
    parser.add_argument("--neighbors", type=int, help="Compare only this many succeeding positions in filename order")
    parser.add_argument("--representatives", type=Path, help="Compare only one image per verified duplicate family")
    args = parser.parse_args()
    paths = sorted(args.photos.glob("DJI_*.jpg"), key=lambda path: int(path.stem.split("_")[1]))
    if args.representatives:
        families = json.loads(args.representatives.read_text(encoding="utf-8"))["view_groups"]
        names = {family[0] for family in families}
        paths = [path for path in paths if path.name in names]
        if len(paths) != len(names):
            raise ValueError("Some duplicate-family representatives are absent from the photo folder.")
    if not paths or args.output.exists():
        raise SystemExit("Expected a populated photo folder and a new output file.")
    cv2.setNumThreads(3)
    cv2.setRNGSeed(841)
    sift = cv2.SIFT_create(nfeatures=args.features, contrastThreshold=0.025)
    with Image.open(paths[0]) as img:
        height = round(img.height * args.width / img.width)
    features = [photo_features(path, args.width, sift) for path in paths]
    print(f"Computed SIFT for {len(features)} photos", flush=True)
    records = []
    n_pairs = 0
    near_misses = []
    for a, b in combinations(range(len(paths)), 2):
        if args.neighbors and b - a > args.neighbors:
            continue
        n_pairs += 1
        match, tentative = compare(features[a], features[b], args.width, height)
        if match:
            records.append({"a": paths[a].name, "b": paths[b].name, **match})
        elif tentative >= 32:
            near_misses.append([paths[a].name, paths[b].name, tentative])
        if n_pairs % 1500 == 0:
            print(f"Pairs checked {n_pairs}, confirmed {len(records)}", flush=True)
    payload = {"width": args.width, "height": height, "features": args.features,
               "photos": [path.name for path in paths], "pairs_tested": n_pairs,
               "edges": records, "rejected_with_tentative_matches": near_misses}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Pairs checked {n_pairs}, confirmed {len(records)}", flush=True)


if __name__ == "__main__":
    main()
