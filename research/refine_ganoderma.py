"""Measure validation-selected scale, mirror ensemble, and box calibration."""

import argparse
from dataclasses import replace
import json
from pathlib import Path

import torch

from crowntrace.benchmark import summarize
from crowntrace.compact import CompactDetector
from crowntrace.inference import MirrorEnsemble
from research.ganoderma_crowns import calibrate, load_tiles, predict_records, short, single_class


def calibrated_boxes(rows, expansion):
    output=[]
    for name,actual,detections,stats in rows:
        adjusted=[]
        for item in detections:
            x,y=item.center
            a,b,r,d=item.box
            w,h=(r-a)*expansion/2,(d-b)*expansion/2
            adjusted.append(replace(item,box=(max(0,x-w),max(0,y-h),min(640,x+w),min(640,y+h))))
        output.append((name,actual,adjusted,stats))
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    parser.add_argument("checkpoint",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    torch.set_num_threads(3)
    args.output.mkdir(parents=True,exist_ok=False)
    detector=CompactDetector(args.checkpoint)
    ensemble=MirrorEnsemble(detector)
    models={"single":detector,"mirror4":ensemble}
    val=single_class(load_tiles(args.root,"validation"))
    validation=[]
    for name,model in models.items():
        for scale in (1.,1.15,1.3):
            rows=predict_records(model,val,scale)
            for expansion in (.9,1.,1.1):
                stats=calibrate(calibrated_boxes(rows,expansion))
                config={"model":name,"scale":scale,"box_expansion":expansion}
                validation.append({"config":config,"metrics":stats})
                print(config,round(stats["crown_ap50_95"],4),flush=True)
    selected=max(validation,key=lambda r:(r["metrics"]["crown_ap50_95"],r["metrics"]["operating_point"]["f1_50"]))
    config=selected["config"]
    base=next(r for r in validation if r["config"]=={"model":"single","scale":1.,"box_expansion":1.})
    test=single_class(load_tiles(args.root,"test"))
    result={"scope":"Second exploratory experiment on the same official test split",
            "validation":validation,"selected":selected,"test":{}}
    for name,entry in (("base",base),("selected",selected)):
        c=entry["config"]
        rows=predict_records(models[c["model"]],test,c["scale"])
        stats=summarize(calibrated_boxes(rows,c["box_expansion"]),entry["metrics"]["operating_threshold"],labels=())
        result["test"][name]=stats
        print("test",name,c,short(stats),flush=True)
    (args.output/"summary.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
