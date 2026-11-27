"""Prepare a locked, unannotated Palangka RGB review cohort; do not generate pseudo-GT."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image,ImageDraw


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw",type=Path,required=True)
    parser.add_argument("--audit",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    a=parser.parse_args()
    groups=json.loads(a.audit.read_text())["view_groups"]
    if len(groups)!=30 or len(set(f for group in groups for f in group))!=150:
        raise ValueError("Unexpected Palangka duplicate audit")
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/"images").mkdir(); (a.output/"labelme").mkdir()
    rng=np.random.default_rng(20261126)
    manifest=[]
    for i,group in enumerate(groups,1):
        # Prefer the first acquisition-like name, not an enhanced variant.
        original=min(group,key=lambda f:int(Path(f).stem.split("_")[-1]))
        path=a.raw/original
        with Image.open(path) as original_image:
            image=original_image.convert("RGB")
        w,h=image.size
        x=int(rng.integers(0,w-640+1)); y=int(rng.integers(0,h-640+1))
        name=f"PAL_{i:03d}.png"
        destination=a.output/"images"/name
        image.crop((x,y,x+640,y+640)).save(destination)
        row=dict(tile=name,source=original,source_view_group=i,source_sha256=sha(path),
                 tile_sha256=sha(destination),x_px=x,y_px=y,width=640,height=640,
                 review_status="pending",annotator="",second_reviewer="",scope="locked_external_candidate")
        manifest.append(row)
        labelme={"version":"5.0.1","flags":{"review_pending":True},"shapes":[],"imagePath":"../images/"+name,
                 "imageData":None,"imageHeight":640,"imageWidth":640}
        (a.output/"labelme"/(Path(name).stem+".json")).write_text(json.dumps(labelme,indent=2),encoding="utf-8")
    with (a.output/"manifest.csv").open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=manifest[0].keys());writer.writeheader();writer.writerows(manifest)
    (a.output/"scope.json").write_text(json.dumps({"source_doi":"10.17632/xj4rs77c4j.1","license":"CC BY 4.0",
                                  "images":len(manifest),"reviewed":0,"annotations":0,
                                  "spatial_overlap_between_source_groups":"unknown",
                                  "selection_seed":20261126,
                                  "selection":"one deterministic random 640px crop per duplicate-view family; no prediction-based selection",
                                  "status":"reserved unreviewed; not a train/validation/test benchmark"},indent=2))
    contact=Image.new("RGB",(1200,6*208),"#f7faf5")
    draw=ImageDraw.Draw(contact)
    for i,row in enumerate(manifest):
        thumb=Image.open(a.output/"images"/row["tile"]);thumb.thumbnail((198,198))
        x=(i%6)*200;y=(i//6)*208
        contact.paste(thumb,(x,y));draw.text((x+4,y+198),row["tile"],fill="#14241b",font_size=9)
    contact.crop((0,0,1200,5*208)).save(a.output/"contact_sheet.png")
    print(json.dumps({"tiles":len(manifest),"reviewed":0,"status":"reserved for annotation; no ground truth"},indent=2))


if __name__=="__main__":
    main()
