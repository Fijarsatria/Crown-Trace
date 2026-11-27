from dataclasses import dataclass, replace
from hashlib import file_digest
from pathlib import Path
from random import Random
from time import perf_counter

import numpy as np
from PIL import Image

LABELS = ("Dead", "Healthy", "Small", "Yellow")
METHODS = ("full_640", "full_1024", "uniform", "random", "no_gap", "gap_only",
           "adaptive", "exhaustive")


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    label: str
    score: float
    view: int = 0

    @property
    def center(self):
        x0, y0, x1, y1 = self.box
        return (x0 + x1) / 2, (y0 + y1) / 2


def iou(first, second):
    ax, ay, ar, ab = first
    bx, by, br, bb = second
    intersection = max(0, min(ar, br) - max(ax, bx)) * max(0, min(ab, bb) - max(ay, by))
    union = (ar - ax) * (ab - ay) + (br - bx) * (bb - by) - intersection
    return intersection / union if union > 0 else 0.0


def duplicate(first, second, overlap, center_ratio):
    if iou(first.box, second.box) >= overlap:
        return True
    if first.view == second.view or center_ratio <= 0:
        return False
    a, b = first.box, second.box
    aw, ah, bw, bh = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]
    if min(aw / bw, bw / aw, ah / bh, bh / ah) < 0.45:
        return False
    x, y = first.center
    u, v = second.center
    return ((x - u) / min(aw, bw)) ** 2 + ((y - v) / min(ah, bh)) ** 2 <= center_ratio ** 2


def fuse(detections, overlap=0.60, center_ratio=0.28, priority=None):
    """Keep high-score crowns and merge conflicting class predictions across views."""
    kept = []
    spatial = {}
    # A duplicate must share at least one grid cell with the kept box.
    order = priority or (lambda det: det.score)
    for det in sorted(detections, key=lambda d: (-order(d), d.view, d.box, d.label)):
        x, y, right, bottom = det.box
        if right <= x or bottom <= y:
            continue
        cells = [(i, j) for i in range(int(x // 128), int(right // 128) + 1)
                 for j in range(int(y // 128), int(bottom // 128) + 1)]
        neighbors = {idx for cell in cells for idx in spatial.get(cell, ())}
        if any(duplicate(det, kept[idx], overlap, center_ratio) for idx in neighbors):
            continue
        for cell in cells:
            spatial.setdefault(cell, []).append(len(kept))
        kept.append(det)
    return kept


class MirrorEnsemble:
    """Average same-crown boxes from the original and three mirrored views."""

    def __init__(self, detector):
        self.detector = detector
        self.labels = detector.labels
        self.version = detector.version + "+mirror4"

    def predict(self, image, size):
        width, height = image.size
        detections = []
        transforms = (None, Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.FLIP_TOP_BOTTOM,
                      Image.Transpose.ROTATE_180)
        for view, transform in enumerate(transforms):
            mirrored = image if transform is None else image.transpose(transform)
            for detection in fuse(self.detector.predict(mirrored, size), center_ratio=0):
                x, y, right, bottom = detection.box
                if view & 1:
                    x, right = width - right, width - x
                if view & 2:
                    y, bottom = height - bottom, height - y
                detections.append(replace(detection, box=(x, y, right, bottom), view=view))
        groups = []
        for detection in sorted(detections, key=lambda d: (-d.score, d.view, d.box)):
            matches = [(iou(detection.box, group[0].box), index) for index, group in enumerate(groups)
                       if group[0].label == detection.label and all(d.view != detection.view for d in group)]
            overlap, index = max(matches, default=(0, -1))
            if overlap >= .50:
                groups[index].append(detection)
            else:
                groups.append([detection])
        output = []
        for group in groups:
            weights = [row.score for row in group]
            total = sum(weights)
            score = total / len(group) * (.55 + .45 * len(group) / len(transforms))
            if score >= .05:
                box = tuple(sum(coord * w for coord, w in zip(axis, weights, strict=True)) / total
                            for axis in zip(*(row.box for row in group), strict=True))
                output.append(Detection(box, group[0].label, score))
        return output


def windows(width, height, span, overlap=0.20):
    def starts(length):
        end = max(0, length - span)
        step = max(1, round(span * (1 - overlap)))
        count = (end + step - 1) // step
        return [0] if count == 0 else [round(end * i / count) for i in range(count + 1)]

    return [(x, y, min(x + span, width), min(y + span, height))
            for y in starts(height) for x in starts(width)]


def spacing_gaps(detections, anchor=0.55):
    """Suggest midpoints bracketed by confident crowns at about two local spacings."""
    points = np.asarray([d.center for d in detections if d.score >= anchor][:384])
    if len(points) < 6:
        return np.empty((0, 2))
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    nearest = distances.min(axis=1)
    spacing = float(np.median(nearest))
    if spacing < 4 or np.median(np.abs(nearest - spacing)) > spacing * 0.24:
        return np.empty((0, 2))
    left, right = np.where(np.triu((distances >= 1.82 * spacing) &
                                   (distances <= 2.15 * spacing), k=1))
    gaps = []
    for a, b in zip(left, right):
        if abs(nearest[a] - spacing) > 0.28 * spacing or abs(nearest[b] - spacing) > 0.28 * spacing:
            continue
        center = (points[a] + points[b]) / 2
        if np.linalg.norm(points - center, axis=1).min() < 0.60 * spacing:
            continue
        if any(np.linalg.norm(previous - center) < 0.45 * spacing for previous in gaps):
            continue
        gaps.append(center)
    return np.asarray(gaps).reshape((-1, 2))


def tile_order(image, candidates, preliminary, method, seed):
    if method == "random":
        order = list(range(len(candidates)))
        Random(seed).shuffle(order)
        return order
    if method == "uniform":
        # Farthest-point sampling spreads the same number of passes across the image.
        centers = np.asarray([((x + r) / 2, (y + b) / 2) for x, y, r, b in candidates])
        first = int(np.argmin(np.linalg.norm(centers - np.asarray(image.size) / 2, axis=1)))
        order = [first]
        distance = np.linalg.norm(centers - centers[first], axis=1)
        while len(order) < len(candidates):
            distance[order] = -1
            next_index = int(np.argmax(distance))
            order.append(next_index)
            distance = np.minimum(distance, np.linalg.norm(centers - centers[next_index], axis=1))
        return order
    gaps = spacing_gaps(preliminary) if method != "no_gap" else []
    scores = []
    thumbnail = image.convert("L").resize((128, 128))
    width, height = image.size
    for idx, (x, y, r, b) in enumerate(candidates):
        weak = sum(1 - d.score for d in preliminary
                   if d.score < 0.55 and x <= d.center[0] < r and y <= d.center[1] < b)
        gap_count = sum(x <= gx < r and y <= gy < b for gx, gy in gaps)
        sample = np.asarray(thumbnail.crop((x * 128 // width, y * 128 // height,
                                            max(1, round(r * 128 / width)),
                                            max(1, round(b * 128 / height)))), dtype=float)
        texture = min(1.0, np.std(sample) / 45.0) if sample.size else 0.0
        score = 0.9 * np.log1p(gap_count)
        if method != "gap_only":
            score += np.log1p(weak) + 0.30 * texture
        scores.append((float(score), idx))
    return [idx for _, idx in sorted(scores, key=lambda item: (-item[0], item[1]))]


def infer(image, detector, method="adaptive", budget=2, threshold=0.25, span=512,
          seed=2026, center_ratio=0.28):
    if method not in METHODS or budget < 0 or not 0.05 <= threshold <= 1 or span < 64:
        raise ValueError("Parameter inferensi di luar rentang.")
    started = perf_counter()
    first_size = 1024 if method == "full_1024" else 640
    preliminary = fuse(detector.predict(image, first_size), center_ratio=0)
    calls = 1
    selected = []
    detections = list(preliminary)
    if not method.startswith("full_"):
        candidates = windows(*image.size, span)
        if candidates != [(0, 0, *image.size)]:
            order = (list(range(len(candidates))) if method == "exhaustive"
                     else tile_order(image, candidates, preliminary, method, seed)[:budget])
            for view, idx in enumerate(order, 1):
                x, y, r, b = candidate = candidates[idx]
                for det in detector.predict(image.crop(candidate), 640):
                    ax, ay, bx, by = det.box
                    detections.append(replace(det, box=(ax + x, ay + y, bx + x, by + y), view=view))
                selected.append(candidate)
                calls += 1
    result = fuse([d for d in detections if d.score >= threshold], center_ratio=center_ratio)
    return result, {"calls": calls, "tiles": selected,
                    "elapsed_ms": (perf_counter() - started) * 1000}


class YoloDetector:
    def __init__(self, weights, device="cpu"):
        from ultralytics import YOLO
        weights = Path(weights)
        if not weights.is_file():
            raise ValueError(f"Bobot lokal tidak ditemukan: {weights}")
        self.model = YOLO(str(weights), task="detect")
        self.names = {int(i): str(label) for i, label in self.model.names.items()}
        if len(self.names) != len(LABELS) or set(self.names.values()) != set(LABELS):
            raise ValueError(f"Bobot WebGIS harus memuat kelas {', '.join(LABELS)}.")
        self.device = device
        with weights.open("rb") as source:
            self.version = weights.name + "@" + file_digest(source, "sha256").hexdigest()[:12]

    def predict(self, image: Image.Image, size):
        result = self.model.predict(image, imgsz=size, conf=0.05, iou=0.70,
                                    max_det=3000, device=self.device, verbose=False)[0]
        return [Detection(tuple(map(float, row[:4])), self.names[int(row[5])], float(row[4]))
                for row in result.boxes.data.cpu().tolist()]
