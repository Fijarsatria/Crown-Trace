"""Evaluate saved detector and classifier checkpoints as one inspection pipeline."""

import argparse
from collections import defaultdict
from dataclasses import asdict, replace
import csv
import json
from pathlib import Path
from statistics import mean

import numpy as np
from PIL import Image, ImageDraw
import torch

from crowntrace.benchmark import mean_ap
from crowntrace.compact import CompactDetector
from research.ganoderma_crowns import load_tiles, predict_padded
from research.ganoderma_condition import (POSITIVE, PADDINGS, binary_metrics, classify, image_crops,
                                          load_classifier, pipeline_metrics)


def screen_image(image, detector, classifier, padding, cutoff, mode):
    if mode not in {"probability", "product"} or not 0 <= cutoff <= 1:
        raise ValueError("Invalid inspection scoring configuration")
    detected=[d for d in predict_padded(detector,image,1.) if d.score>=cutoff]
    probabilities=classify(classifier,image_crops(image,detected,padding))
    return [(d,float(p),float(p*d.score if mode=="product" else p))
            for d,p in zip(detected,probabilities,strict=True)]


def collect(tiles, detector, classifier, padding, cutoff, mode):
    rows=[]
    for tile in tiles:
        with Image.open(tile.path) as source:
            image=source.convert("RGB")
        positive=[replace(d,label=POSITIVE,score=s) for d,_,s in screen_image(image,detector,classifier,padding,cutoff,mode)]
        rows.append((tile.path.name,[d for d,_ in tile.regions if d.label==POSITIVE],positive))
    return rows


def inspect_tile(path, actual, predicted, threshold, destination):
    with Image.open(path) as image:
        reference=image.convert("RGB")
    detected=reference.copy()
    for photo,values,color in ((reference,actual,"#ffda68"),(detected,[d for d in predicted if d.score>=threshold],"#ff8ac4")):
        draw=ImageDraw.Draw(photo)
        for d in values:
            draw.rectangle(d.box,outline=color,width=4)
    width,height=reference.size
    panel=Image.new("RGB",(2*width,height+64),"#152e26")
    panel.paste(reference,(0,64))
    panel.paste(detected,(width,64))
    draw=ImageDraw.Draw(panel)
    draw.text((15,10),"Positif menurut survei",fill="white",font_size=20)
    draw.text((width+15,10),"Kandidat inspeksi model",fill="white",font_size=20)
    draw.text((15,37),path.name,fill="#bdcfc2",font_size=13)
    panel.save(destination)


def within_tile_audit(tiles,predictions,threshold):
    rows={(row["tile"],int(row["object_index"])):float(row["score"]) for row in predictions}
    labels,probabilities,distances=[],[],[]
    concordance=[]
    for tile in tiles:
        tile_scores=[]
        tile_labels=[]
        for index,(object_,_) in enumerate(tile.regions):
            value=rows[(tile.path.name,index)]
            label=object_.label==POSITIVE
            labels.append(label)
            probabilities.append(value)
            tile_scores.append(value)
            tile_labels.append(label)
            x,y=object_.center
            distances.append(np.hypot(x/640-.5,y/640-.5))
        y,p=np.asarray(tile_labels),np.asarray(tile_scores)
        if y.any() and (~y).any():
            delta=p[y][:,None]-p[~y][None,:]
            concordance.append(float(((delta>0)+.5*(delta==0)).mean()))
    labels,probabilities,distances=np.asarray(labels),np.asarray(probabilities),np.asarray(distances)
    subsets={}
    for lo,hi in ((0,.2),(.2,.4),(.4,1.)):
        keep=(distances>=lo)&(distances<hi)
        block=labels[keep]
        subsets[f"{lo}-{hi}"]={"positive":int(block.sum()),"negative":int((~block).sum()),
                                  "metrics":binary_metrics(block,probabilities[keep],threshold) if block.any() and (~block).any() else None}
    return {"mixed_tiles":len(concordance),"within_tile_pair_concordance_mean":mean(concordance),"centre_distance_strata":subsets}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    parser.add_argument("detector",type=Path)
    parser.add_argument("condition",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    torch.set_num_threads(4)
    args.output.mkdir(parents=True,exist_ok=False)
    detector_run=json.loads((args.detector/"summary.json").read_text())
    condition_run=json.loads((args.condition/"summary.json").read_text())
    chosen=condition_run["selected"]
    model=load_classifier(args.condition/chosen["checkpoint"])
    detector=CompactDetector(args.detector/"detector.pth")
    dc=detector_run["validation_trained"]["operating_threshold"]
    validations={}
    val=load_tiles(args.root,"validation")
    for mode in ("product","probability"):
        rows=collect(val,detector,model,PADDINGS[chosen["method"]],dc,mode)
        best=max((pipeline_metrics(rows,t) for t in np.arange(.025,.975,.025).round(3)),key=lambda m:(m["f1"],m["precision"]))
        validations[mode]={**best,"positive_ap50":mean_ap(rows,[POSITIVE],[.50])[POSITIVE]}
    mode=max(validations,key=lambda m:(validations[m]["positive_ap50"],validations[m]["f1"]))
    threshold=validations[mode]["threshold"]
    tiles=load_tiles(args.root,"test")
    test=collect(tiles,detector,model,PADDINGS[chosen["method"]],dc,mode)
    report={"scope":"End-to-end localization of positive survey labels at IoU 0.50", "selected_classifier":chosen,
            "detector":detector.version,"detector_threshold":dc,"validation":validations,"score_mode":mode,
            "test":{**pipeline_metrics(test,threshold),"positive_ap50":mean_ap(test,[POSITIVE],[.50])[POSITIVE]}}
    with (args.condition/"test_scores.csv").open(newline="",encoding="utf-8") as source:
        predictions=[r for r in csv.DictReader(source) if r["method"]==chosen["method"] and f"{r['method']}_{r['seed']}.pth"==chosen["checkpoint"]]
    report["reference_box_sampling_audit"]=within_tile_audit(tiles,predictions,chosen["validation"]["threshold"])
    candidates={row[0]:row for row in sorted(test,key=lambda row:(-len(row[1]),row[0]))[:3]}
    for tile in tiles:
        if tile.path.name in candidates:
            _,actual,predicted=candidates[tile.path.name]
            inspect_tile(tile.path,actual,predicted,threshold,args.output/f"{tile.path.stem}.png")
    with (args.output/"predictions.jsonl").open("w",encoding="utf-8") as f:
        for name,actual,predicted in test:
            f.write(json.dumps({"tile":name,"survey_positive":[asdict(d) for d in actual],"candidates":[asdict(d) for d in predicted]})+"\n")
    (args.output/"summary.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
