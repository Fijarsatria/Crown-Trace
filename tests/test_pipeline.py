import csv
from io import BytesIO, StringIO
from http.server import ThreadingHTTPServer
import json
import math
from threading import Thread
from urllib.request import urlopen

from PIL import Image, TiffImagePlugin
import pytest

from crowntrace.compact import CompactDetector
from crowntrace.dataset import DATA, ROOT, manifest
from crowntrace.inference import Detection, fuse, infer, spacing_gaps
from crowntrace.server import Service, make_handler, mock_raster
from crowntrace.spatial import read_image


class FakeConnection:
    def __init__(self, request):
        self.inbound, self.response = BytesIO(request), bytearray()

    def makefile(self, *_):
        return self.inbound

    def sendall(self, data):
        self.response.extend(data)

    def settimeout(self, _):
        pass


def call(handler, path, method="GET", body=b""):
    prefix = f"{method} {path} HTTP/1.0\r\nContent-Type: application/octet-stream\r\n"
    raw = prefix.encode() + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    connection = FakeConnection(raw)
    handler(connection, ("127.0.0.1", 0), None)
    header, result = bytes(connection.response).split(b"\r\n\r\n", 1)
    return int(header.split()[1]), result


@pytest.fixture(scope="module")
def detector():
    import torch
    torch.set_num_threads(2)
    return CompactDetector(ROOT / "results/pilot_train/best.pth")


def test_single_crown_fusion_and_close_neighbors():
    first = Detection((0, 0, 20, 20), "Healthy", 0.9)
    duplicate = Detection((2, 3, 22, 21), "Yellow", 0.8, 1)
    adjacent = Detection((18, 1, 37, 22), "Healthy", 0.7, 1)
    assert fuse([first, duplicate, adjacent]) == [first, adjacent]


def test_spacing_prior_finds_gap_and_disables_without_anchors():
    def crown(x, y):
        return Detection((x-6, y-6, x+6, y+6), "Healthy", .99)
    points = [crown(x, y) for x in (50, 100, 150, 200, 250)
              for y in (50, 100, 150) if (x, y) != (150, 100)]
    assert any((point == [150, 100]).all() for point in spacing_gaps(points))
    assert len(spacing_gaps(points[:3])) == 0


def test_crop_budget_and_offsets():
    class SpatialStub:
        def predict(self, image, size):
            return [Detection((14, 15, 40, 41), "Small", 0.84)]
    image = Image.new("RGB", (1024, 1024))
    predictions, meta = infer(image, SpatialStub(), "adaptive", budget=2)
    assert meta["calls"] == 3 and len(meta["tiles"]) == 2
    assert any(x.box[0] > 14 or x.box[1] > 15 for x in predictions)


def test_geotiff_projected_and_pixel_fallback():
    image = Image.new("RGB", (100, 100))
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[33550] = (10.0, 10.0, 0.0)
    tags[33922] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    tags[34735] = (1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, 3857)
    stream = BytesIO()
    image.save(stream, format="TIFF", tiffinfo=tags)
    _, ref, corners, warning = read_image(stream.getvalue())
    lon, lat = ref.location(10.0, 20.0)
    assert warning is None and len(corners) == 4
    assert lon == pytest.approx(math.degrees(100 / 6378137.0))
    assert lat == pytest.approx(math.degrees(math.atan(math.sinh(-200 / 6378137.0))))
    stream = BytesIO()
    image.save(stream, format="TIFF")
    _, ref, _, warning = read_image(stream.getvalue())
    assert ref is None and warning


def test_real_weights_photo_request_and_exports(tmp_path, detector):
    handler = make_handler(Service(tmp_path, detector, "compact"))
    assert json.loads(call(handler, "/api/health")[1])["model_ready"]
    assert call(handler, "/")[0] == 200
    status, demo = call(handler, "/api/demo")
    assert status == 200 and json.loads(demo)["source"] == "demo"
    demo_features = json.loads(call(handler, "/api/analyses/demo/exports/geojson")[1])["features"]
    assert len(demo_features) == json.loads(demo)["counts"]["total"]
    longitude, latitude = demo_features[0]["geometry"]["coordinates"]
    assert longitude == pytest.approx(116.829063) and latitude == pytest.approx(-1.266226)
    sample = next(row for row in manifest()["samples"] if row["split"] == "test")
    raw = (DATA / "pilot_images" / sample["file_name"]).read_bytes()
    status, response = call(handler, "/api/analyses?filename=source.jpg", "POST", raw)
    result = json.loads(response)
    assert status == 201 and result["coordinate_mode"] == "pixel" and result["source"] == "model"
    assert result["counts"]["total"] == len(result["detections"]) > 0
    assert result["threshold"] == 0.35
    endpoint = "/api/analyses/" + result["analysis_id"]
    assert call(handler, endpoint + "/image")[0] == 200
    exported = list(csv.DictReader(StringIO(call(handler, endpoint + "/exports/csv")[1].decode("utf-8-sig"))))
    assert len(exported) == result["counts"]["total"] and not exported[0]["longitude"]
    assert call(handler, endpoint + "/exports/geojson")[0] == 422
    assert call(handler, "/api/analyses", "POST", b"invalid")[0] == 422
    data, _ = mock_raster()
    status, response = call(handler, "/api/analyses?filename=geo.tif", "POST", data)
    result = json.loads(response)
    assert status == 201 and result["coordinate_mode"] == "geographic"
    assert call(handler, f"/api/analyses/{result['analysis_id']}/exports/geojson")[0] == 200


def test_http_loopback_serves_web(tmp_path):
    with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Service(tmp_path))) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = "http://127.0.0.1:" + str(server.server_port)
            with urlopen(address, timeout=5) as response:
                assert "Hitung pohon sawit" in response.read().decode()
            with urlopen(address + "/api/health", timeout=5) as response:
                assert json.load(response)["model_ready"] is False
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_webgis_example_agrees_with_expanded_benchmark(tmp_path):
    results = ROOT / "results/expanded_benchmark"
    if not (results / "summary.json").exists():
        pytest.skip("Expanded benchmark has not been run.")
    metrics = json.loads((results / "summary.json").read_text(encoding="utf-8"))["metrics"]
    threshold = metrics["val"]["full_640"]["operating_threshold"]
    model = CompactDetector(ROOT / "results/expanded_train/best.pth")
    handler = make_handler(Service(tmp_path, model, "compact", threshold))
    status, raw = call(handler, "/api/sample?method=full_640", "POST")
    sample = json.loads(raw)
    with (results / "per_image.csv").open(encoding="utf-8") as source:
        reference = next(row for row in csv.DictReader(source)
                         if row["split"] == "test" and row["method"] == "full_640"
                         and row["image"] == sample["source_image"]["name"])
    assert status == 201
    assert sample["counts"]["total"] == int(reference["pred"])
    assert sample["threshold"] == threshold


def test_multiscale_initializes_as_baseline_and_reloads(tmp_path):
    import torch
    from crowntrace.compact import CrownNet, MultiScaleCrownNet

    baseline = CrownNet().eval()
    model = MultiScaleCrownNet().eval()
    model.load_state_dict(baseline.state_dict(), strict=False)
    source = torch.zeros(1, 3, 64, 64)
    with torch.inference_mode():
        torch.testing.assert_close(model(source), baseline(source), rtol=0, atol=0)
    weights = tmp_path / "candidate.pth"
    torch.save(model.state_dict(), weights)
    detector = CompactDetector(weights)
    assert isinstance(detector.model, MultiScaleCrownNet)


def test_v2_ensemble_merges_shared_boxes_and_preserves_distinct_crown():
    from crowntrace.benchmark_v2 import combine

    main = [Detection((0, 0, 20, 20), "Healthy", 0.8)]
    other = [Detection((2, 2, 22, 22), "Healthy", 0.8), Detection((40, 40, 60, 60), "Small", 0.7)]
    matched, distinct = combine(main, other)
    assert matched.box == pytest.approx((1, 1, 21, 21))
    assert distinct.score == pytest.approx(0.63)
