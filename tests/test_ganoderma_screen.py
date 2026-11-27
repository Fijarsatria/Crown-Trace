"""Regression tests for research-only survey-label screening."""

import numpy as np
from PIL import Image
import pytest

from crowntrace.inference import Detection
from research import evaluate_ganoderma_pipeline as pipeline
from research.ganoderma_condition import POSITIVE, pipeline_metrics


def test_screen_filters_detector_then_scores(monkeypatch):
    good = Detection((5, 5, 25, 25), "palm_crown", .5)
    low = Detection((30, 30, 50, 50), "palm_crown", .2)
    monkeypatch.setattr(pipeline, "predict_padded", lambda *args: [good, low])

    def fake_classifier(model, crops):
        assert tuple(crops.shape) == (1, 3, 48, 48)
        return np.array([.8])

    monkeypatch.setattr(pipeline, "classify", fake_classifier)
    image = Image.new("RGB", (640, 640))
    assert pipeline.screen_image(image, None, None, .25, .35, "probability") == [(good, .8, .8)]
    assert pipeline.screen_image(image, None, None, .25, .35, "product") == [(good, .8, .4)]
    with pytest.raises(ValueError):
        pipeline.screen_image(image, None, None, .25, .35, "unknown")


def test_screen_empty_detections(monkeypatch):
    monkeypatch.setattr(pipeline, "predict_padded", lambda *args: [])
    monkeypatch.setattr(pipeline, "classify", lambda model, crops: np.empty(0))
    assert pipeline.screen_image(Image.new("RGB", (640, 640)), None, None, .25, .35, "probability") == []


def test_inspection_thresholds_count_matches_and_duplicates():
    truth = [Detection((0, 0, 20, 20), POSITIVE, 1.), Detection((40, 40, 60, 60), POSITIVE, 1.)]
    pred = [Detection((0, 0, 20, 20), POSITIVE, .9),
            Detection((0, 0, 20, 20), POSITIVE, .7),
            Detection((40, 40, 60, 60), POSITIVE, .1)]
    records = [("tile", truth, pred)]
    first = pipeline_metrics(records, .625)
    second = pipeline_metrics(records, .025)
    assert (first["tp"], first["fp"], first["fn"]) == (1, 1, 1)
    assert (second["tp"], second["fp"], second["fn"]) == (2, 1, 0)
