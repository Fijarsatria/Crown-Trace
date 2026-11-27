"""Compare paired mask AP on the same image families."""

import argparse
import json
from pathlib import Path

import numpy as np


def load(path):
    with np.load(path, allow_pickle=False) as data:
        return {name: data[name] for name in data.files}


def precision_recall_ap(scores, hits, groups, instances, multiplicity):
    ranks = np.argsort(-scores, kind="stable")
    weights = multiplicity[groups[ranks]]
    present = weights > 0
    if not present.any():
        return np.zeros(hits.shape[1])
    weights = weights[present]
    tp = (hits[ranks[present]] * weights[:, None]).cumsum(0)
    recall = tp / (instances @ multiplicity)
    precision = tp / weights.cumsum()[:, None]
    envelope = np.maximum.accumulate(precision[::-1], axis=0)[::-1]
    envelope = np.vstack((envelope, np.zeros(hits.shape[1])))
    recall_grid = np.linspace(0, 1, 101)
    return np.array([envelope[np.searchsorted(recall[:, i], recall_grid), i].mean()
                     for i in range(hits.shape[1])])


def compare(baseline, candidate, repeats, seed):
    for key in ("images", "families", "references"):
        if not np.array_equal(baseline[key], candidate[key]):
            raise ValueError(f"The models did not evaluate the same {key}.")
    names, groups = np.unique(baseline["families"], return_inverse=True)
    instances = np.bincount(groups, weights=baseline["references"], minlength=len(names))
    inputs = [(record["scores"], record["hits"], np.repeat(groups, np.diff(record["offsets"])))
              for record in (baseline, candidate)]

    def evaluate_both(weights):
        first, second = [precision_recall_ap(*row, instances, weights) for row in inputs]
        return first, second

    def readable(values):
        return {"ap50": float(values[0]), "ap75": float(values[5]), "ap50_95": float(values.mean())}

    all_groups = np.ones(len(names), dtype=int)
    first, second = evaluate_both(all_groups)
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(repeats):
        weights = np.bincount(rng.integers(len(names), size=len(names)), minlength=len(names))
        a, b = evaluate_both(weights)
        deltas.append(b - a)
    deltas = np.array(deltas)
    intervals = {metric: np.quantile(series, (.025, .975)).tolist() for metric, series in
                 (("ap50", deltas[:, 0]), ("ap75", deltas[:, 5]), ("ap50_95", deltas.mean(1)))}
    families = []
    for i, name in enumerate(names):
        weights = np.zeros(len(names), dtype=int)
        weights[i] = 1
        a, b = evaluate_both(weights)
        families.append({"family": name, "reference_instances": int(instances[i]), **readable(b - a)})
    return {"baseline": readable(first), "candidate": readable(second), "delta": readable(second - first),
            "paired_family_resample_interval_95": intervals, "by_family": families, "seed": seed,
            "resamples": repeats,
            "scope": "Exploratory paired resampling over filename families, with unknown geographic independence."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if args.repeats < 1 or args.output.exists():
        parser.error("Use positive resamples and an unused output path.")
    result = compare(load(args.baseline), load(args.candidate), args.repeats, args.seed)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key != "by_family"}, indent=2))


if __name__ == "__main__":
    main()
