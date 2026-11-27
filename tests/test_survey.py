from collections import Counter
import csv
from http.server import ThreadingHTTPServer
from io import BytesIO, StringIO
import json
import threading
import time
from urllib.request import Request, urlopen

from PIL import Image
import pytest

from crowntrace.compact import CompactDetector
from crowntrace.inference import Detection
from crowntrace.server import Service, make_handler
from crowntrace.survey import DETECT_WEIGHTS, encode_objects, gdal_launcher, grid, select
from crowntrace.survey_api import SAMPLE


def test_core_grid_covers_large_raster_without_overlapping():
    windows = list(grid(2800, 2300, 1024, 0.2))
    assert len(windows) == 12
    assert windows[0][0] == "A3"
    assert windows[8][0] == "A1"
    assert windows[-1][0] == "D1"
    assert sum((x1 - x0) * (y1 - y0) for _, (x0, y0, x1, y1), _ in windows) == 2800 * 2300
    assert all(r[0] <= c[0] < c[2] <= r[2] and r[1] <= c[1] < c[3] <= r[3] for _, c, r in windows)


def test_cross_window_merge_preserves_a_neighbor():
    windows = list(grid(2050, 1024, 1024, 0.2))
    first = Detection((770, 300, 878, 404), "Healthy", 0.81, view=0)
    duplicate = Detection((777, 306, 879, 405), "Small", 0.87, view=1)
    neighbor = Detection((875, 320, 961, 420), "Healthy", 0.82, view=1)
    assert len(select([first, duplicate, neighbor], windows)) == 2
    assert neighbor in select([first, duplicate, neighbor], windows)


def test_overlap_detection_is_counted_in_its_nonoverlapping_core():
    windows = list(grid(1800, 720, 1024, 0.2))
    first, second = windows[:2]
    boundary = first[1][2]
    detection = Detection((boundary - 12, 50, boundary + 12, 100), "Yellow", 0.8, view=0)
    result, = encode_objects(None, [detection], windows, None, lambda *_: None)
    assert result["tile"] == first[0]
    assert result["core_tile"] == second[0]


def json_call(base, path, input=None, dtype="application/json"):
    data = json.dumps(input).encode() if isinstance(input, dict) else input
    request = Request(base + path, data=data, headers={"Content-Type": dtype} if data else {})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def downloadable(base, prefix, name):
    with urlopen(base + prefix + "/" + name, timeout=30) as response:
        assert "attachment" in response.headers.get("Content-Disposition", "")
        return response.read()


def demo_boundaries():
    # Survey fixture easting columns at 480000, 480140 and 480280 m, converted from UTM 50S.
    top = [(116.820215655094, -1.351659451962), (116.821474141391, -1.351659545385),
           (116.822732627775, -1.351659638152)]
    bottom = [(116.820215501943, -1.353740312154), (116.821473989313, -1.353740405722),
              (116.822732476769, -1.353740498632)]
    result = []
    for i, name in enumerate(("Barat", "Timur")):
        ring = [top[i], bottom[i], bottom[i+1], top[i+1], top[i]]
        result.append({"type": "Feature", "properties": {"name": name},
                       "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": result}


@pytest.mark.skipif(gdal_launcher() is None or not SAMPLE.is_file(), reason="QGIS dan GeoTIFF contoh diperlukan")
def test_windowed_survey_http_upload_to_exports(tmp_path):
    import torch

    torch.set_num_threads(4)
    service = Service(tmp_path, CompactDetector(DETECT_WEIGHTS), "compact", 0.40)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert b"Peta kebun" in urlopen(base + "/survei", timeout=10).read()
        health = json_call(base, "/api/surveys/health")
        assert health["ready"] and health["segmenter"]
        staged = json_call(base, "/api/surveys?filename=contoh-uji.tif",
                           SAMPLE.read_bytes(), "application/octet-stream")
        assert staged["status"] == "staged"
        path = "/api/surveys/" + staged["id"]
        job = json_call(base, path + "/start", {"blocks": demo_boundaries(), "segment": True})
        assert job["status"] == "queued"
        limit = time.monotonic() + 75
        while job["status"] in {"queued", "running"} and time.monotonic() < limit:
            time.sleep(0.08)
            job = json_call(base, path)
        assert job["status"] == "done", job
        summary = json_call(base, path + "/summary.json")
        mapped = json_call(base, path + "/map.json")
        points = json.loads(downloadable(base, path, "trees.geojson"))["features"]
        crowns = json.loads(downloadable(base, path, "crowns.geojson"))["features"]
        cells = json.loads(downloadable(base, path, "grid.geojson"))["features"]
        gaps = json.loads(downloadable(base, path, "coverage.geojson"))["features"]
        review = json.loads(downloadable(base, path, "review.geojson"))["features"]
        blocks = csv.DictReader(StringIO(downloadable(base, path, "blocks.csv").decode("utf-8-sig")))
        assert summary["tile_count"] == 12
        assert summary["raw_detections"] > summary["points"]
        assert summary["points"] == len(points) == len(mapped["trees"]) and len(points) > 0
        tallies = Counter(item["properties"]["visual_label"] for item in points)
        assert sum(summary["visual_counts"].values()) == len(points)
        assert all(summary["visual_counts"].get(label) == count for label, count in tallies.items())
        assert {row["id"]: row["visual_label"] for row in mapped["trees"]} == {
            row["properties"]["id"]: row["properties"]["visual_label"] for row in points
        }
        assert summary["crowns"] == len(crowns) and len(crowns) > 0
        assert len(set(obj["properties"]["id"] for obj in points)) == len(points)
        assert sum(row["trees"] for row in summary["grid"]) == len(points)
        by_cell = Counter(point["properties"]["core_tile"] for point in points)
        cell_by_tile = {row["tile"]: row for row in summary["grid"]}
        for item in mapped["trees"]:
            x, y = item["center"]
            left, top, right, bottom = cell_by_tile[item["core_tile"]]["core"]
            assert left <= x < right and top <= y < bottom
        for cell in cells:
            props = cell["properties"]
            assert props["trees"] == by_cell[props["tile"]]
            assert props["trees_per_covered_ha"] == cell_by_tile[props["tile"]]["trees_per_covered_ha"]
            if not props["covered_ha"]:
                assert props["trees"] == 0 and props["trees_per_covered_ha"] is None
        assert len(gaps) == summary["coverage_cells"] > 0
        assert gaps == [cell for cell in cells if cell["properties"]["missing_pixels"]]
        assert len(review) == summary["review_points"]
        assert review == [point for point in points if point["properties"]["visual_label"] in {"Yellow", "Dead"}]
        assert {block["block"] for block in summary["blocks"]} == {"Barat", "Timur"}
        block_rows = list(blocks)
        assert sum(int(row["trees"]) for row in block_rows) == len(points)
        for row in block_rows:
            assert sum(int(row[f"visual_{label.lower()}"]) for label in summary["visual_counts"]) == int(row["trees"])
        assert min(float(row["coverage_pct"]) for row in summary["blocks"]) < 99.0
        assert len(mapped["blocks"]) == 2 and all(shape["type"] == "Polygon" for shape in mapped["blocks"])
        assert downloadable(base, path, "crowntrace.gpkg").startswith(b"SQLite format 3")
        with urlopen(base + path + "/window?x0=200&y0=200&x1=800&y1=500", timeout=30) as response:
            with Image.open(BytesIO(response.read())) as image:
                assert image.size == (600, 300)
                assert image.mode == "RGBA"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
