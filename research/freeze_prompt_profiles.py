"""Freeze validation-selected contour configurations into explicit inference profiles."""
import argparse
import json
from pathlib import Path
import shutil
import torch
from crowntrace.train_coco_crowns import digest,save


def materialize(root,report,name,destination):
    config=report["validation"]["configs"][name]
    if name=="baseline":
        raise ValueError("Baseline remains available; do not duplicate as new profile.")
    run=root/name.split("_r",1)[0]
    source=json.loads((run/"training.json").read_text())
    destination.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(run/"detector.pth",destination/"detector.pth")
    checkpoint=torch.load(run/"mask.pth",map_location="cpu",weights_only=True)
    checkpoint["rotations"]=tuple(config["rotations"])
    threshold=config["threshold"]
    if isinstance(threshold,list):
        checkpoint["threshold"],checkpoint["small_threshold"],checkpoint["small_box_area"]=threshold
    else:
        checkpoint["threshold"]=threshold
        checkpoint.pop("small_threshold",None);checkpoint.pop("small_box_area",None)
    checkpoint["name"]=f"CrownPrompt-{checkpoint['size']}-{name}"
    save(destination/"mask.pth",checkpoint)
    source["detector"]["sha256"]=digest(destination/"detector.pth")
    source["mask"].update(sha256=digest(destination/"mask.pth"),threshold=threshold,rotations=config["rotations"])
    source["selection"]={"rule":report["selection"],"selected":{"name":name,"count_cutoff":.25,
                          "validation":config,"test":report.get("test",{}).get("configs",{}).get(name)}}
    (destination/"training.json").write_text(json.dumps(source,indent=2),encoding="utf-8")
    return source["mask"]["sha256"]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("root",type=Path);p.add_argument("summary",type=Path)
    a=p.parse_args()
    report=json.loads(a.summary.read_text())
    selected=report["selected"]
    fast=max((n for n in report["eligible"] if "_r1_" in n),key=lambda n:report["validation"]["configs"][n]["ap50_95"],default=None)
    if selected!="baseline":print("detail",selected,materialize(a.root,report,selected,a.root/"selected_detail"))
    if fast:print("fast",fast,materialize(a.root,report,fast,a.root/"selected_fast"))


if __name__=="__main__":main()
