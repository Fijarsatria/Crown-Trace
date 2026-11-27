"""Evaluate the preselected fastest eligible one-rotation configuration on the reused holdout."""
import argparse
import json
from pathlib import Path
import torch
from crowntrace.compact import CompactDetector
from crowntrace.segment import CrownMaskPredictor
from crowntrace.train_coco_crowns import read_split
from research.evaluate_prompt_contours import benchmark


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("root",type=Path);p.add_argument("summary",type=Path)
    p.add_argument("data",type=Path);p.add_argument("output",type=Path)
    a=p.parse_args()
    report=json.loads(a.summary.read_text())
    name=max((n for n in report["eligible"] if "_r1_" in n),key=lambda n:report["validation"]["configs"][n]["ap50_95"])
    v=report["validation"]["configs"][name]
    chosen=a.root/name.split("_r",1)[0]
    model=CrownMaskPredictor(chosen/"mask.pth")
    spec=tuple(v["threshold"]) if isinstance(v["threshold"],list) else v["threshold"]
    a.output.mkdir(parents=True,exist_ok=False)
    torch.set_num_threads(6)
    test=benchmark(read_split(a.data,"test"),CompactDetector(chosen/"detector.pth"),{name:(model,tuple(v["rotations"]),spec)},a.output,"test")
    payload={"scope":"Exploratory reused holdout. Fast configuration selected only from validation eligible single-rotation candidates.",
             "selected":name,"validation":v,"test":test}
    (a.output/"summary.json").write_text(json.dumps(payload,indent=2))
    print(json.dumps(payload,indent=2))


if __name__=="__main__":main()
