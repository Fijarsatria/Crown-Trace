"""Offline diagnostic using saved model outputs; never trains or tunes a holdout.

A single validation tile is a case study, not a replacement for grouped AP.
Inspection thresholds were already selected on validation in a prior run.
"""
import csv,json,math
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from collections import Counter
from statistics import mean,median
import argparse

ROOT=Path(__file__).resolve().parents[1]


def load(path):return json.loads(path.read_text(encoding="utf-8-sig"))


def decode_rle(seg):
    h,w=seg["size"];out=np.zeros(h*w,dtype=bool);start=0
    for i,length in enumerate(seg["counts"]):
        if not isinstance(length,int) or length<0 or start+length>len(out):raise ValueError("Bad RLE")
        if i%2:out[start:start+length]=True
        start+=length
    if start!=len(out):raise ValueError("Incomplete RLE")
    return out.reshape((h,w),order="F")


def region(polygons,w,h):
    img=Image.new("1",(w,h))
    for p in polygons:ImageDraw.Draw(img).polygon(list(zip(p[::2],p[1::2])),fill=1)
    return np.array(img,dtype=bool)


def iou(a,b):
    union=np.count_nonzero(a|b)
    return np.count_nonzero(a&b)/union if union else 0.


def dilate(mask,radius):
    p=np.pad(mask,radius)
    h,w=mask.shape;out=np.zeros_like(mask)
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            if dx*dx+dy*dy<=radius*radius:out|=p[radius+dy:radius+dy+h,radius+dx:radius+dx+w]
    return out


def boundary(mask):
    p=np.pad(mask,1)
    core=p[1:-1,1:-1]&p[:-2,1:-1]&p[2:,1:-1]&p[1:-1,:-2]&p[1:-1,2:]
    return mask&~core


def bf(pred,ref,radius=2):
    p,r=boundary(pred),boundary(ref)
    pr=np.count_nonzero(p&dilate(r,radius))/max(1,np.count_nonzero(p))
    re=np.count_nonzero(r&dilate(p,radius))/max(1,np.count_nonzero(r))
    return 2*pr*re/(pr+re) if pr+re else 0.


def matched(matrix,scores,cut):
    used=set();pairs=[]
    for i in sorted(range(len(scores)),key=lambda j:(-scores[j],j)):
        candidates=[j for j in range(matrix.shape[1]) if j not in used and matrix[i,j]>=cut]
        if candidates:
            j=max(candidates,key=lambda k:matrix[i,k]);used.add(j);pairs.append((i,j))
    return pairs


def case_metrics(pred,truth,scores):
    mat=np.array([[iou(p,r) for r in truth] for p in pred])
    pairs=matched(mat,scores,.5)
    strict=matched(mat,scores,.75)
    return {"predictions":len(pred),"references":len(truth),"matched_iou50":len(pairs),"matched_iou75":len(strict),
            "mean_iou_matched50":mean(float(mat[i,j]) for i,j in pairs) if pairs else None,
            "mean_boundary_f1_2px_matched50":mean(bf(pred[i],truth[j]) for i,j in pairs) if pairs else None,
            "count_bias":len(pred)-len(truth)}


def bbox_iou(a,b):
    x=max(a[0],b[0]);y=max(a[1],b[1]);r=min(a[2],b[2]);t=min(a[3],b[3])
    inter=max(0,r-x)*max(0,t-y)
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union>0 else 0.


def triage_audit():
    base=ROOT/"results/deep_20261126"
    op=load(base/"ganoderma_operating_points.json")["validation"]
    lo,hi=op["high_recall"]["threshold"],op["balanced"]["threshold"]
    records=[json.loads(s) for s in (base/"ganoderma_pipeline/predictions.jsonl").read_text().splitlines() if s.strip()]
    totals=Counter();tp=Counter();reference_count=0
    for row in records:
        refs=row["survey_positive"];reference_count+=len(refs);used=set()
        for p in sorted(row["candidates"],key=lambda p:-p["score"]):
            score=p["score"];tier="high_priority" if score>=hi else "needs_review" if score>=lo else "not_flagged"
            totals[tier]+=1
            if tier=="not_flagged":continue
            choices=[(bbox_iou(p["box"],r["box"]),j) for j,r in enumerate(refs) if j not in used and bbox_iou(p["box"],r["box"])>=.5]
            if choices:
                _,j=max(choices);used.add(j);tp[tier]+=1
    return {"scope":"Stored repeated-source test detections, thresholds frozen from validation. Not diagnosis or calibrated probability.",
            "tiles":len(records),"survey_positive_references":reference_count,"thresholds":{"high_priority":hi,"needs_review":lo},
            "tiers":{t:{"detections":totals[t],"matched_positive":tp[t] if t!="not_flagged" else None,
                         "unmatched_flagged":totals[t]-tp[t] if t!="not_flagged" else None} for t in ("high_priority","needs_review","not_flagged")},
            "positive_not_flagged_or_missed":reference_count-tp["high_priority"]-tp["needs_review"]}


def run(output):
    output.mkdir(parents=True,exist_ok=False)
    base=ROOT/"results/iteration_20261126"
    detail=load(base/"photo_detail/crowns.coco.json");fast=load(base/"photo_fast/crowns.coco.json")
    assert detail["images"]==fast["images"]
    image=detail["images"][0]
    a={x["id"]:x for x in detail["annotations"]};b={x["id"]:x for x in fast["annotations"]}
    assert set(a)==set(b) and all(abs(a[k]["score"]-b[k]["score"])<1e-6 for k in a)
    keys=sorted(a);scores=[a[k]["score"] for k in keys]
    d=[decode_rle(a[k]["segmentation"]) for k in keys];f=[decode_rle(b[k]["segmentation"]) for k in keys]
    raw=load(ROOT/"data/roboflow_crowns_grouped_v1/val/_annotations.coco.json")
    q=next(x for x in raw["images"] if x["file_name"]==image["file_name"])
    truth=[region(x["segmentation"],image["width"],image["height"]) for x in raw["annotations"] if x["image_id"]==q["id"]]
    disagreement=[]
    for key,p,g,score in zip(keys,d,f,scores):
        disagreement.append({"annotation_id":key,"detector_score":score,"detail_fast_iou":iou(p,g),
                             "detail_area_px":int(p.sum()),"fast_area_px":int(g.sum()),
                             "detail_best_ref_iou":max(iou(p,t) for t in truth),"fast_best_ref_iou":max(iou(g,t) for t in truth)})
    disagreement.sort(key=lambda r:r["detail_fast_iou"])
    with (output/"mask_review_candidates.csv").open("w",encoding="utf-8",newline="") as h:
        w=csv.DictWriter(h,fieldnames=list(disagreement[0]));w.writeheader();w.writerows(disagreement)
    alternatives={"detail":d,"fast":f,"union":[p|g for p,g in zip(d,f)],"intersection":[p&g for p,g in zip(d,f)]}
    result={"scope":"One previously used validation tile; fixed union/intersection diagnostic. No checkpoint selected or replaced.",
            "image":image["file_name"],"models":{k:case_metrics(v,truth,scores) for k,v in alternatives.items()},
            "detail_fast_iou_median":median(r["detail_fast_iou"] for r in disagreement),
            "review_priority_lowest_agreement":[r["annotation_id"] for r in disagreement[:5]],
            "inspection":triage_audit()}
    (output/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",required=True,type=Path);run(p.parse_args().output)
