"""Classify field-survey labels on Ganoderma V2 with a fixed block split."""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.nn import functional as F

from crowntrace.benchmark import operating_matches
from crowntrace.segment import crop_box
from research.ganoderma_crowns import load_tiles

POSITIVE = "ganoderma_affected_palm"
NEGATIVE = "survey_negative_normal_palm"
SIZE = 48
PADDINGS = {"crown": 0., "context": .25}


def classifier():
    return nn.Sequential(nn.Conv2d(3, 16, 3, padding=1), nn.BatchNorm2d(16), nn.SiLU(), nn.MaxPool2d(2),
                         nn.Conv2d(16, 24, 3, padding=1), nn.BatchNorm2d(24), nn.SiLU(), nn.MaxPool2d(2),
                         nn.Conv2d(24, 40, 3, padding=1), nn.BatchNorm2d(40), nn.SiLU(),
                         nn.AdaptiveAvgPool2d(2), nn.Flatten(), nn.Linear(160, 1))


def image_crops(image, detections, padding):
    crops = [np.asarray(image.crop(crop_box(det.box, *image.size, padding)).resize((SIZE, SIZE),
                          Image.Resampling.BILINEAR), dtype=np.uint8) for det in detections]
    return torch.from_numpy(np.stack(crops)).permute(0, 3, 1, 2) if crops else torch.empty((0, 3, SIZE, SIZE), dtype=torch.uint8)


def prepare(tiles):
    inputs = {key: [] for key in PADDINGS}
    labels, names = [], []
    for tile in tiles:
        objects = [row[0] for row in tile.regions]
        with Image.open(tile.path) as photo:
            image = photo.convert("RGB")
            for name, padding in PADDINGS.items():
                inputs[name].append(image_crops(image, objects, padding))
        labels.extend(det.label == POSITIVE for det in objects)
        names.extend((tile.path.name, index) for index in range(len(objects)))
    return {key: torch.cat(values) for key, values in inputs.items()}, torch.tensor(labels), names


def binary_metrics(labels, probabilities, threshold):
    ref = np.asarray(labels, dtype=bool)
    prob = np.asarray(probabilities, dtype=float)
    guess = prob >= threshold
    tp, fp, fn, tn = (int(np.sum(mask)) for mask in (ref&guess, ~ref&guess, ref&~guess, ~ref&~guess))
    order = np.argsort(-prob, kind="stable")
    ends = np.flatnonzero(np.r_[prob[order][:-1] != prob[order][1:], True])
    hits = np.cumsum(ref[order])[ends]
    precision = hits / (ends+1)
    ap = float(np.sum(np.diff(np.r_[0, hits]) * precision) / max(1, ref.sum()))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "average_precision": ap,
            "precision": tp/max(1,tp+fp), "recall": tp/max(1,tp+fn),
            "specificity": tn/max(1,tn+fp), "f1": 2*tp/max(1,2*tp+fp+fn),
            "balanced_accuracy": (tp/max(1,tp+fn)+tn/max(1,tn+fp))/2,
            "accuracy": (tp+tn)/len(ref), "review_count": tp+fp, "threshold": threshold}


@torch.inference_mode()
def classify(model, features):
    model.eval()
    if not len(features):
        return np.empty(0)
    return torch.cat([model(batch.float()/255).flatten().sigmoid() for batch in features.split(128)]).numpy()


def cutoff(labels, probabilities):
    return max([binary_metrics(labels, probabilities, t) for t in np.arange(.05, 1., .05).round(2)],
               key=lambda result: (result["f1"], result["precision"], result["threshold"]))


def fit(train, truth, val, val_truth, seed, epochs, destination):
    torch.manual_seed(seed)
    generator = torch.Generator().manual_seed(seed)
    model = classifier().to(memory_format=torch.channels_last)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.0015)
    weight = torch.tensor((~truth).sum().item() / truth.sum().item()).sqrt()
    history, best = [], -1.
    for epoch in range(1, epochs+1):
        model.train()
        for indices in torch.randperm(len(train), generator=generator).split(96):
            rgb = train[indices].float()/255
            rgb = rgb.rot90(int(torch.randint(4, (), generator=generator)), (-2, -1))
            if torch.rand((), generator=generator) < .5:
                rgb = rgb.flip(-1)
            gain = .9 + .2 * torch.rand((len(indices), 1, 1, 1), generator=generator)
            loss = F.binary_cross_entropy_with_logits(model((rgb*gain).clamp_(0,1)).flatten(),
                         truth[indices].float(), pos_weight=weight)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
        validation = cutoff(val_truth, classify(model, val))
        history.append({"epoch": epoch, **validation})
        if validation["average_precision"] > best:
            best = validation["average_precision"]
            torch.save({"weights": model.state_dict(), "seed": seed, "epoch": epoch}, destination)
        print(destination.stem, epoch, round(validation["average_precision"], 4), flush=True)
    checkpoint = torch.load(destination, weights_only=True)
    model.load_state_dict(checkpoint["weights"])
    return {"seed": seed, "selected_epoch": checkpoint["epoch"], "validation": cutoff(val_truth, classify(model, val)),
            "history": history, "checkpoint": destination.name}


def load_classifier(path):
    model = classifier().to(memory_format=torch.channels_last)
    model.load_state_dict(torch.load(path, weights_only=True)["weights"])
    return model


def pipeline_metrics(rows, threshold):
    tp=fp=fn=0
    for _, truth, detection in rows:
        a,b,c=operating_matches(truth,[d for d in detection if d.score>=threshold])
        tp,fp,fn=tp+a,fp+b,fn+c
    return {"tp":tp,"fp":fp,"fn":fn,"precision":tp/max(1,tp+fp),"recall":tp/max(1,tp+fn),
            "f1":2*tp/max(1,2*tp+fp+fn),"review_count":tp+fp,"threshold":threshold}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--seeds", type=int, nargs="+", default=(2026,2027,2028))
    parser.add_argument("--threads", type=int, default=4)
    args=parser.parse_args()
    torch.set_num_threads(args.threads)
    args.output.mkdir(parents=True,exist_ok=False)
    train, val = (prepare(load_tiles(args.root,split)) for split in ("train","validation"))
    result={"scope":"Binary prediction of survey-derived crown labels, source-defined blocks", "methods":{}}
    for name in PADDINGS:
        runs=[]
        for seed in args.seeds:
            runs.append(fit(train[0][name],train[1],val[0][name],val[1],seed,args.epochs,args.output/f"{name}_{seed}.pth"))
        result["methods"][name]=runs
    name,run=max(((n,r) for n,rs in result["methods"].items() for r in rs), key=lambda item:item[1]["validation"]["average_precision"])
    result["selected"]={"method":name,"checkpoint":run["checkpoint"],"validation":run["validation"]}
    # Test data is read only once the validation-based decisions have been made.
    test_tiles=load_tiles(args.root,"test")
    test=prepare(test_tiles)
    result["majority_baseline"]=binary_metrics(test[1],np.zeros(len(test[1])),.5)
    with (args.output/"test_scores.csv").open("w",newline="",encoding="utf-8") as file:
        writer=csv.writer(file)
        writer.writerow(["method","seed","tile","object_index","survey_positive","score"])
        for n,runs in result["methods"].items():
            for r in runs:
                values=classify(load_classifier(args.output/r["checkpoint"]),test[0][n])
                r["test"]=binary_metrics(test[1],values,r["validation"]["threshold"])
                writer.writerows((n,r["seed"],tile,index,int(label),score) for (tile,index),label,score in zip(test[2],test[1],values,strict=True))
                print("test",n,r["seed"],r["test"],flush=True)
    (args.output/"summary.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")


if __name__ == "__main__":
    main()
