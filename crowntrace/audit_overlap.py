"""Screen filename grouping using alignment of pixels in predicted crop overlaps."""
import argparse
from collections import defaultdict
from functools import lru_cache
from itertools import combinations
import json
from pathlib import Path
from random import Random

import numpy as np
from PIL import Image

from .dataset import manifest


def offsets(name):
    values = Path(name).stem.split("_")
    if len(values) != 4:
        raise ValueError(f"Nama MOPAD tidak sesuai format empat koordinat: {name}")
    return int(values[2]), int(values[3])


def overlap_mad(a, b, dx, dy):
    width, height = a.shape[1], a.shape[0]
    left, right = max(0, dx), min(width, width + dx)
    top, bottom = max(0, dy), min(height, height + dy)
    if (right - left) * (bottom - top) < width * height * 0.20 or right <= left or bottom <= top:
        return None
    original = a[top:bottom:7, left:right:7]
    other = b[top-dy:bottom-dy:7, left-dx:right-dx:7]
    return float(np.abs(original.astype(np.int16) - other).mean())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = manifest(args.manifest)
    groups = defaultdict(list)
    for row in data["samples"]:
        groups[row["group"]].append(row["file_name"])

    @lru_cache(maxsize=24)
    def grayscale(name):
        with Image.open(args.images / name) as img:
            return np.asarray(img.convert("L"))

    rng = Random(2026)
    alignment = {}
    fingerprints = {}
    for group, names in sorted(groups.items()):
        pairs = []
        for a, b in combinations(sorted(names), 2):
            x0, y0 = offsets(a)
            x1, y1 = offsets(b)
            dx, dy = x1 - x0, y1 - y0
            if 45 <= max(abs(dx), abs(dy)) <= 560 and abs(dx - dy) > 35:
                pairs.append((a, b, dx, dy))
        sample = rng.sample(pairs, min(60, len(pairs)))
        normal, swapped, control = [], [], []
        for a, b, dx, dy in sample:
            arr_a, arr_b = grayscale(a), grayscale(b)
            normal.append(overlap_mad(arr_a, arr_b, dx, dy))
            swapped.append(overlap_mad(arr_a, arr_b, dy, dx))
            control.append(overlap_mad(arr_a, arr_b, dx + 45, dy - 35))
        control = [value for value in control if value is not None]
        alignment[group] = {
            "sampled_pairs": len(sample),
            "median_gray_mad_xy": float(np.median(normal)) if normal else None,
            "median_gray_mad_yx": float(np.median(swapped)) if swapped else None,
            "median_gray_mad_offset_control": float(np.median(control)) if control else None,
        }
        for name in names:
            thumb = np.asarray(Image.fromarray(grayscale(name)).resize((9, 8), Image.Resampling.LANCZOS))
            bits = (thumb[:, 1:] > thumb[:, :-1]).reshape(-1)
            digest = int.from_bytes(np.packbits(bits).tobytes(), "big")
            fingerprints[name] = group, digest
    distances = [(a, b, (ha ^ hb).bit_count())
                 for (a, (ga, ha)), (b, (gb, hb)) in combinations(fingerprints.items(), 2) if ga != gb]
    report = {
        "alignment": alignment,
        "cross_group_dhash64_pairs": len(distances),
        "cross_group_dhash64_min_distance": min((d for _, _, d in distances), default=None),
        "cross_group_dhash64_screen_le4": [(a, b, d) for a, b, d in distances if d <= 4],
        "limits": "The overlap check compares crop alignment inside each filename prefix. The full-tile "
                  "difference hash screens for near-identical views across groups. Neither verifies geographic "
                  "coordinates or excludes partial spatial overlap between groups.",
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
