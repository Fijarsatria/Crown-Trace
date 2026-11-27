"""Compare balanced and high-recall inspection thresholds fixed on validation."""

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from crowntrace.compact import CompactDetector
from research.evaluate_ganoderma_pipeline import collect
from research.ganoderma_condition import PADDINGS, load_classifier, pipeline_metrics
from research.ganoderma_crowns import load_tiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    torch.set_num_threads(4)
    config = json.loads((args.results / "ganoderma_pipeline/summary.json").read_text())
    classifier = config["selected_classifier"]
    model = load_classifier(args.results / "ganoderma_condition" / classifier["checkpoint"])
    detector = CompactDetector(args.results / "ganoderma_detector/detector.pth")
    mode = config["score_mode"]

    def predictions(split):
        return collect(load_tiles(args.root, split), detector, model, PADDINGS[classifier["method"]],
                       config["detector_threshold"], mode)

    validation = predictions("validation")
    candidates = [pipeline_metrics(validation, t) for t in np.arange(.025, .975, .025).round(3)]
    eligible = [row for row in candidates if row["recall"] >= .9]
    if eligible:
        high_recall = max(eligible, key=lambda row: (row["precision"], row["recall"]))
    else:
        high_recall = max(candidates, key=lambda row: (row["recall"], row["precision"]))
    chosen = {"balanced": config["validation"][mode], "high_recall": high_recall}
    test = predictions("test")
    result = {"scope": "Repeated source test. Thresholds chosen on validation, high-recall target 0.90.",
              "validation": chosen, "target_met_on_validation": bool(eligible),
              "test": {name: pipeline_metrics(test, row["threshold"]) for name, row in chosen.items()}}
    with (args.results / "ganoderma_operating_points.json").open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
