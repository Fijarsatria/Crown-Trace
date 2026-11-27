"""Count unique trees in a GeoTIFF using overlapping, windowed inference."""
import argparse
from base64 import b64encode
from bisect import bisect_right
from collections import defaultdict
from dataclasses import replace
import csv
import importlib.util
import json
from math import ceil
import os
from pathlib import Path
from struct import unpack
import subprocess
from subprocess import PIPE, Popen, TimeoutExpired
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
from PIL import Image

from .dataset import ROOT
from .inference import LABELS, fuse

SEGMENT_WEIGHTS = ROOT / "results/segment_pilot/best.pth"
DETECT_WEIGHTS = ROOT / "results/expanded_train/best.pth"


def gdal_launcher():
    specified = os.environ.get("CROWNTRACE_GDAL_PYTHON")
    if specified:
        return Path(specified)
    if importlib.util.find_spec("osgeo") and importlib.util.find_spec("shapely"):
        return Path(sys.executable)
    if os.name == "nt":
        installed = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        possible = sorted(installed.glob("QGIS */bin/python-qgis.bat"), reverse=True)
        if possible:
            return possible[0]
    return None


class GeoRaster:
    def __init__(self, path):
        runner = gdal_launcher()
        if runner is None:
            raise ValueError("Pasang QGIS Desktop untuk pemetaan, lalu buka ulang CrownTrace.")
        script = Path(__file__).with_name("raster_worker.py")
        args = [str(runner), "-u", str(script)]
        if runner.suffix.lower() in {".bat", ".cmd"}:
            # Run the quoted batch command directly via cmd rather than starting a shell on data paths.
            args = f'cmd.exe /d /s /c ""{runner}" -u "{script}""'
        self.process = Popen(args, stdin=PIPE, stdout=PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.info = self.request({"path": str(path.resolve())})
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()

    def request(self, command, binary=False):
        try:
            self.process.stdin.write(json.dumps(command).encode("ascii") + b"\n")
            self.process.stdin.flush()
            header = self.process.stdout.read(9)
        except OSError as error:
            raise ValueError("Pembaca GDAL terhenti. Periksa instalasi QGIS.") from error
        if len(header) != 9:
            raise ValueError("Pembaca GDAL tidak dapat berjalan. Periksa instalasi QGIS.")
        status, length = unpack("<BQ", header)
        data = self.process.stdout.read(length)
        if len(data) != length:
            raise ValueError("Data dari pembaca GDAL terpotong.")
        if status:
            raise ValueError(json.loads(data)["detail"])
        return data if binary else json.loads(data)

    def window(self, rect, limit=None, core=None, tile=None):
        return self.request({"method": "image", "rect": rect, "limit": limit,
                             "core": core, "tile": tile}, binary=True)


def column_label(index):
    result = ""
    while index >= 0:
        index, index_letter = divmod(index, 26)
        result = chr(65 + index_letter) + result
        index -= 1
    return result


def grid(width, height, tile_size, overlap, origin=(0, 0)):
    halo = round(tile_size * overlap / 2)
    stride = tile_size - 2 * halo
    shift_x, shift_y = (value % stride for value in origin)
    rows = ceil((height + shift_y) / stride)
    for row, y in enumerate(range(-shift_y, height, stride)):
        for col, x in enumerate(range(-shift_x, width, stride)):
            core = (max(x, 0), max(y, 0), min(x + stride, width), min(y + stride, height))
            left, top, right, bottom = core
            rect = (max(0, left - halo), max(0, top - halo),
                    min(width, right + halo), min(height, bottom + halo))
            yield f"{column_label(col)}{rows - row}", core, rect


def arrays(raw, rect):
    x, y, right, bottom = rect
    return np.frombuffer(raw, dtype=np.uint8).reshape(bottom - y, right - x, 4)


def inside(x, y, rectangle):
    left, top, right, bottom = rectangle
    return left <= x < right and top <= y < bottom


def candidates(raster, detector, threshold, windows, progress, tile_size=1024):
    objects = []
    tile_metadata = []
    for index, (tile, core, rect) in enumerate(windows):
        pixels = arrays(raster.window(rect, core=core, tile=tile), rect)
        dx, dy, right, bottom = rect
        left, top, end_x, end_y = core
        coverage = pixels[top - dy:end_y - dy, left - dx:end_x - dx, 3]
        tile_metadata.append({"tile": tile, "core": core,
                              "coverage": round(100 * np.count_nonzero(coverage) / coverage.size, 2)})
        if pixels[:, :, 3].any():
            image = Image.new("RGB", (tile_size, tile_size))
            image.paste(Image.fromarray(pixels[:, :, :3]))
            detections = detector.predict(image, 1024)
            for det in detections:
                x, y = det.center
                if det.score < threshold or not inside(x + dx, y + dy, rect):
                    continue
                a, b, c, d = det.box
                box = (max(dx, a + dx), max(dy, b + dy), min(right, c + dx), min(bottom, d + dy))
                if box[2] > box[0] and box[3] > box[1]:
                    placed = replace(det, box=box, view=index)
                    cx, cy = placed.center
                    if pixels[int(cy - dy), int(cx - dx), 3]:
                        objects.append(placed)
        progress(index + 1, len(windows))
    return objects, tile_metadata


def select(objects, windows):
    def priority(det):
        _, core, rect = windows[det.view]
        x, y = det.center
        edge = min(x - rect[0], y - rect[1], rect[2] - x, rect[3] - y)
        return det.score + 0.15 * inside(x, y, core) + 0.05 * min(max(0, edge) / 64, 1)
    return fuse(objects, overlap=0.55, center_ratio=0.24, priority=priority)


def encode_objects(raster, retained, windows, segmenter, progress):
    core_tiles = {(core[0], core[1]): tile for tile, core, _ in windows}
    columns = sorted({x for x, _ in core_tiles})
    rows_at = sorted({y for _, y in core_tiles})

    def core_tile(center):
        x, y = center
        return core_tiles[columns[bisect_right(columns, x) - 1], rows_at[bisect_right(rows_at, y) - 1]]

    def record(index, det, tile):
        center = [round(coord, 3) for coord in det.center]
        return {"id": f"CT-{index:06d}", "center": center, "score": round(det.score, 4),
                "tile": tile, "core_tile": core_tile(center),
                "visual_label": det.label if det.label in LABELS else "Unassigned"}

    grouped = defaultdict(list)
    for index, det in enumerate(sorted(retained, key=lambda d: (d.center[1], d.center[0])), 1):
        grouped[det.view].append((index, det))
    for position, (view, rows) in enumerate(sorted(grouped.items()), 1):
        tile, _, rect = windows[view]
        values = (record(index, det, tile) for index, det in rows)
        if segmenter is None:
            yield from values
        else:
            pixels = arrays(raster.window(rect), rect)
            image = Image.fromarray(pixels[:, :, :3])
            dx, dy, _, _ = rect
            local = [replace(det, box=tuple(v - (dx if i % 2 == 0 else dy)
                                            for i, v in enumerate(det.box))) for _, det in rows]
            for item, (crop, mask) in zip(values, segmenter.masks(image, local), strict=True):
                # Keep no-data and transparent pixels out of estimated crowns, even at internal gaps.
                x, y, right, bottom = crop
                height, width = mask.shape
                valid = Image.fromarray(pixels[y:bottom, x:right, 3]).resize((width, height), Image.Resampling.NEAREST)
                mask &= np.asarray(valid) != 0
                if mask.any():
                    item.update(mask=b64encode(np.packbits(mask)).decode("ascii"),
                                mask_shape=[height, width],
                                crop=[x + dx, y + dy, right + dx, bottom + dy])
                yield item
        progress(position, len(grouped))


def export_csv(path, rows):
    names = ["block", "area_ha", "covered_ha", "coverage_pct", "trees", "trees_per_covered_ha"]
    names.extend(f"visual_{label.lower()}" for label in (*LABELS, "Unassigned"))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            safe = dict(row)
            if safe["block"].lstrip().startswith(("=", "@", "+", "-")):
                safe["block"] = "'" + safe["block"]
            writer.writerow(safe)


def run_survey(input_path, output, detector, segmenter=None, blocks=None, tile_size=1024, overlap=0.2,
               threshold=0.40, progress=None):
    if not 256 <= tile_size <= 1536 or not 0 <= overlap <= 0.5 or not 0 < threshold <= 1:
        raise ValueError("Ukuran potongan, overlap, atau ambang berada di luar rentang.")
    if blocks is not None and (not isinstance(blocks, dict) or blocks.get("type") != "FeatureCollection" or
                               not isinstance(blocks.get("features"), list) or blocks.get("crs")):
        raise ValueError("Gunakan GeoJSON FeatureCollection batas blok dalam WGS84.")
    notify = progress or (lambda *args: None)
    if output.exists():
        raise FileExistsError(f"Hasil sudah ada: {output.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    start = perf_counter()
    with TemporaryDirectory(prefix="survey-", dir=output.parent) as working, GeoRaster(input_path) as raster:
        folder = Path(working) / "result"
        folder.mkdir()
        width, height = raster.info["width"], raster.info["height"]
        raster.request({"method": "blocks", "features": blocks["features"] if blocks is not None else None})
        windows = list(grid(width, height, tile_size, overlap))
        (folder / "preview.png").write_bytes(raster.window((0, 0, width, height), limit=1152))
        proposed, metadata = candidates(raster, detector, threshold, windows,
                                        lambda n, total: notify("deteksi", n, total), tile_size)
        retained = select(proposed, windows)
        object_file = Path(working) / "objects.json"
        with object_file.open("w", encoding="utf-8") as handle:
            handle.write("[")
            for index, item in enumerate(encode_objects(raster, retained, windows, segmenter,
                                                        lambda n, total: notify("mahkota", n, total))):
                if index:
                    handle.write(",")
                handle.write(json.dumps(item, separators=(",", ":")))
            handle.write("]")
        notify("ekspor", 0, 1)
        summary = raster.request({"method": "export", "destination": str(folder.resolve()),
                                  "objects": str(object_file.resolve())})
        export_csv(folder / "blocks.csv", summary["blocks"])
        cell_stats = summary.pop("cell_stats")
        for item, stats in zip(metadata, cell_stats, strict=True):
            if item["tile"] != stats["tile"]:
                raise ValueError("Urutan grid tidak cocok dengan hasil ekspor.")
            item.update(stats)
        summary.update(raster.info, tile_count=len(windows), tile_size=tile_size, overlap=overlap,
                       raw_detections=len(proposed),
                       detector=detector.version, segmenter=segmenter.version if segmenter else None,
                       threshold=threshold, elapsed_seconds=round(perf_counter()-start, 3), grid=metadata)
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        folder.replace(output)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("geotiff", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blocks", type=Path)
    parser.add_argument("--weights", type=Path, default=DETECT_WEIGHTS)
    parser.add_argument("--segment-weights", type=Path, default=SEGMENT_WEIGHTS)
    parser.add_argument("--no-segment", action="store_true")
    parser.add_argument("--tile-size", type=int, default=1024)
    parser.add_argument("--overlap", type=float, default=0.2)
    parser.add_argument("--threshold", type=float, default=0.4)
    args = parser.parse_args()
    import torch
    from .compact import CompactDetector
    from .segment import CrownMaskPredictor
    torch.set_num_threads(4)
    detector = CompactDetector(args.weights)
    segment = None if args.no_segment else CrownMaskPredictor(args.segment_weights)
    blocks = json.loads(args.blocks.read_text(encoding="utf-8-sig")) if args.blocks else None
    data = run_survey(args.geotiff, args.output, detector, segment, blocks, args.tile_size, args.overlap,
                      args.threshold, lambda mode, n, total: print(f"{mode} {n}/{total}", flush=True))
    print(json.dumps({key: value for key, value in data.items() if key != "grid"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
