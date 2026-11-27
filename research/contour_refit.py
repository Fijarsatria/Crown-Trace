"""Evaluate test-time mask refinement and quality-based ranking on fixed COCO splits."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
from statistics import mean
from time import perf_counter

import numpy as np
from PIL import Image
import torch

from crowntrace.benchmark import ranked_ap, accept_match
from crowntrace.compact import CompactDetector
from crowntrace.evaluate_coco_crowns import LocalMask, mask_iou, bbox_window, proposals, draw_panel, IOU_THRESHOLDS
from crowntrace.segment import CrownMaskPredictor, crop_box, rgb_tensor
from crowntrace.train_coco_crowns import read_split, mask_pixels, digest

METHODS = ("original", "native_soft", "refit", "blend", "blend_agreement")


@torch.inference_mode()
def probabilities(predictor, image, detections):
    crops = [crop_box(det.box, *image.size, predictor.padding) for det in detections]
    for start in range(0,len(crops),48):
        batch = crops[start:start+48]
        data = torch.stack([rgb_tensor(image.crop(box), predictor.size) for box in batch])
        probs = sum(predictor.probabilities(data.rot90(k,(-2,-1))).rot90(-k,(-2,-1))
                    for k in predictor.rotations) / len(predictor.rotations)
        for window,p in zip(batch, probs.numpy(), strict=True):
            yield window,p


def native_probability(item):
    window,prob=item
    x,y,r,b=window
    return window,np.asarray(Image.fromarray(prob).resize((r-x,b-y),Image.Resampling.BILINEAR))


def threshold_mask(item, cutoff):
    window,data=native_probability(item)
    data=data>=cutoff
    return LocalMask(window,data,int(data.sum()))


def original_mask(item,cutoff):
    window,prob=item
    return LocalMask.resized(window,prob>=cutoff)


def refined_box(det, mask):
    rows,cols=np.nonzero(mask.pixels)
    if not len(rows):
        return det
    x,y,_,_=mask.window
    a,b,r,d=det.box
    tight=np.array([x+cols.min(), y+rows.min(), x+cols.max()+1, y+rows.max()+1],float)
    original=np.array(det.box,float)
    size=np.array([r-a,d-b,r-a,d-b])
    shift=np.clip((tight-original)*.5,-size*.18,size*.18)
    return replace(det,box=tuple(original+shift))


def blend_masks(first,second,cutoff):
    a,p=native_probability(first)
    b,q=native_probability(second)
    window=(min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3]))
    x,y,r,d=window
    values=np.zeros((d-y,r-x),np.float32)
    for (u,v,s,t),prob in ((a,p),(b,q)):
        values[v-y:t-y,u-x:s-x]+=prob*.5
    binary=values>=cutoff
    return LocalMask(window,binary,int(binary.sum()))


def overlap_matrix(masks, reference):
    return np.asarray([[mask_iou(a,b) for b in reference] for a in masks],dtype=float).reshape(len(masks),len(reference))


def matched_count(matrix,scores,cutoff,count_cutoff):
    remaining=np.ones(matrix.shape[1],dtype=bool)
    order=np.argsort(-np.asarray(scores),kind="stable")
    return sum(accept_match(matrix[i],remaining,cutoff) for i in order if scores[i]>=count_cutoff)


def evaluate(tiles,detector,predictor,methods,output,split,selected=None):
    matrices={name:{} for name in methods}
    scores={name:{} for name in methods}
    hits={name:[] for name in methods}
    total=sum(len(tile.regions) for tile in tiles)
    oracle=[]
    for index,tile in enumerate(tiles):
        with Image.open(tile.path) as photo:
            image=photo.convert("RGB")
        known=[box for box,_ in tile.regions]
        reference=[]
        for box,polygon in tile.regions:
            window=bbox_window(box.box,image)
            pixels=np.asarray(mask_pixels(polygon,window))>0
            reference.append(LocalMask(window,pixels,int(pixels.sum())))
        truth_prompted=list(probabilities(predictor,image,known))
        oracle.extend(mask_iou(original_mask(p,predictor.threshold),t) for p,t in zip(truth_prompted,reference,strict=True))
        predictions=proposals(detector,image)
        initial=list(probabilities(predictor,image,predictions))
        original=[original_mask(a,predictor.threshold) for a in initial]
        soft=[threshold_mask(a,predictor.threshold) for a in initial]
        refitted=[refined_box(d,m) for d,m in zip(predictions,original,strict=True)]
        detail=list(probabilities(predictor,image,refitted))
        second=[threshold_mask(a,predictor.threshold) for a in detail]
        blended=[blend_masks(a,b,predictor.threshold) for a,b in zip(initial,detail,strict=True)]
        values={"original":original,"native_soft":soft,"refit":second,"blend":blended,"blend_agreement":blended}
        raw=[d.score for d in predictions]
        agreement=[float(d.score*(.4+.6*mask_iou(a,b))) for d,a,b in zip(predictions,original,second,strict=True)]
        for name in methods:
            rank=agreement if name=="blend_agreement" else raw
            matrix=overlap_matrix(values[name],reference)
            matrices[name][index]=matrix
            scores[name][index]=rank
            # Compare strict localization at the same original detector operating point.
            keep=[i for i,value in enumerate(raw) if value>=.25]
            hits[name].append({"predictions":len(keep),"matched50":matched_count(matrix[keep],[rank[i] for i in keep],.5,0),
                               "matched75":matched_count(matrix[keep],[rank[i] for i in keep],.75,0)})
        if split=="test" and index in (0,len(tiles)//2,len(tiles)-1):
            draw_panel(image,reference,[m for m,d in zip(values[selected],predictions,strict=True) if d.score>=.25],
                       tile.path.name,output/f"test_{index+1:02d}.png")
        if (index+1)%5==0:
            print(split,index+1,"/",len(tiles),flush=True)
    result={"reference":total,"oracle_mean_iou":mean(oracle),"methods":{}}
    for name in methods:
        aps=ranked_ap(matrices[name],scores[name],IOU_THRESHOLDS)
        result["methods"][name]={"ap50":aps[0],"ap75":aps[5],"ap50_95":mean(aps),
                                  **{key:sum(row[key] for row in hits[name]) for key in hits[name][0]}}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data",type=Path)
    parser.add_argument("weights",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--threads",type=int,default=2)
    args=parser.parse_args()
    torch.set_num_threads(args.threads)
    args.output.mkdir(parents=True,exist_ok=False)
    detector=CompactDetector(args.weights/"detector.pth")
    predictor=CrownMaskPredictor(args.weights/"mask.pth")
    start=perf_counter()
    validation=evaluate(read_split(args.data,"val"),detector,predictor,METHODS,args.output,"validation")
    candidates=validation["methods"]
    chosen=max((name for name in METHODS if candidates[name]["ap50"]>=candidates["original"]["ap50"]-.01),
               key=lambda name:(candidates[name]["ap50_95"],candidates[name]["ap75"]))
    print("selected",chosen,validation,flush=True)
    test=evaluate(read_split(args.data,"test"),detector,predictor,list(dict.fromkeys(["original",chosen])),args.output,"test",chosen)
    report={"scope":"exploratory grouped Roboflow test set reused in earlier work","selection":"validation mask AP50:95, AP50 within 0.01 of baseline",
            "detector_sha256":digest(args.weights/"detector.pth"),"mask_sha256":digest(args.weights/"mask.pth"),
            "validation":validation,"selected":chosen,"test":test,"seconds":perf_counter()-start}
    (args.output/"summary.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(test,indent=2),flush=True)


if __name__=="__main__":
    main()
