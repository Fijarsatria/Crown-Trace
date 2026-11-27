"""Validation-selected seed ensemble for the field-label crop experiment."""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import torch

from research.ganoderma_crowns import load_tiles
from research.ganoderma_condition import binary_metrics, classify, cutoff, load_classifier, prepare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    parser.add_argument("condition",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    torch.set_num_threads(4)
    condition=json.loads((args.condition/"summary.json").read_text())
    inputs,truth,_=prepare(load_tiles(args.root,"validation"))
    val_scores={}
    for method,runs in condition["methods"].items():
        for run in runs:
            val_scores[run["checkpoint"]]=classify(load_classifier(args.condition/run["checkpoint"]),inputs[method])
    base=condition["selected"]["checkpoint"]
    candidates={"single_selected":[base],
                "exact_box_seeds":[r["checkpoint"] for r in condition["methods"]["crown"]],
                "context_seeds":[r["checkpoint"] for r in condition["methods"]["context"]],
                "all_seeds":list(val_scores)}
    validation={name:cutoff(truth,np.mean([val_scores[k] for k in keys],axis=0)) for name,keys in candidates.items()}
    chosen=max(candidates,key=lambda name:(validation[name]["average_precision"],validation[name]["f1"]))
    cached={key:{} for key in val_scores}
    labels={}
    with (args.condition/"test_scores.csv").open(newline="",encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key=f"{row['method']}_{row['seed']}.pth"
            index=(row["tile"],int(row["object_index"]))
            cached[key][index]=float(row["score"])
            current=int(row["survey_positive"])
            if labels.setdefault(index,current)!=current:
                raise ValueError("Reference labels disagree across seeds")
    if any(set(predictions)!=set(labels) for predictions in cached.values()):
        raise ValueError("Test predictions do not cover the same reference boxes")
    indices=sorted(labels)
    test_truth=np.array([labels[key] for key in indices])
    tests={}
    for name in dict.fromkeys(["single_selected",chosen]):
        scores=np.mean([[cached[m][key] for key in indices] for m in candidates[name]],axis=0)
        tests[name]=binary_metrics(test_truth,scores,validation[name]["threshold"])
    report={"scope":"Repeated official test, reference-box RGB only. Ensemble choice and threshold selected on validation.",
            "validation":validation,"selected":chosen,"checkpoints":candidates[chosen],"test":tests}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as f:
        json.dump(report,f,indent=2)
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
