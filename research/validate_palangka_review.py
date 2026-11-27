"""Validate reviewed Palangka polygons and export COCO only after a two-reviewer gate."""
import argparse
import csv
import json
import hashlib
import math
from pathlib import Path

from PIL import Image,ImageDraw


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def export(root,output):
    rows=list(csv.DictReader((root/"manifest.csv").open(encoding="utf-8-sig")))
    scope=json.loads((root/"scope.json").read_text())
    if len(rows)!=scope["images"] or len({r["source_view_group"] for r in rows})!=len(rows):
        raise ValueError("Review cohort changed.")
    issues=[]; images=[];anns=[]
    for i,r in enumerate(rows,1):
        name=r["tile"]
        if Path(name).name!=name:raise ValueError("Unexpected tile name.")
        path=root/"images"/name
        if sha(path)!=r["tile_sha256"]:raise ValueError(f"Tile integrity mismatch: {name}")
        with Image.open(path) as img:
            w,h=img.size
        doc=json.loads((root/"labelme"/(Path(name).stem+".json")).read_text(encoding="utf-8-sig"))
        status=r["review_status"];reviewer=r["annotator"].strip();second=r["second_reviewer"].strip()
        # An empty annotation file is never a verified negative by itself.
        if status not in {"complete","empty_checked"} or not reviewer or not second or reviewer.casefold()==second.casefold():
            issues.append(f"{name}: requires complete or empty_checked and two distinct reviewers")
            continue
        shapes=doc["shapes"]
        if (status=="empty_checked")!= (len(shapes)==0):
            issues.append(f"{name}: review status conflicts with polygons");continue
        if doc.get("flags",{}).get("review_pending") is True:
            issues.append(f"{name}: remove review_pending only after annotation review");continue
        images.append({"id":i,"file_name":name,"width":w,"height":h,"source_group":r["source_view_group"]})
        for shape in shapes:
            if shape["label"]!="palm_crown" or shape.get("shape_type")!="polygon":
                raise ValueError(f"{name}: expect palm_crown polygon")
            points=shape["points"]
            if len(points)<3 or any(len(p)!=2 or not all(math.isfinite(v) for v in p) or not(0<=p[0]<w and 0<=p[1]<h) for p in points):
                raise ValueError(f"{name}: invalid polygon")
            mask=Image.new("1",(w,h));ImageDraw.Draw(mask).polygon([tuple(p) for p in points],fill=1)
            area=sum(mask.getdata()); x,y=zip(*points)
            if area<9 or max(x)==min(x) or max(y)==min(y):raise ValueError(f"{name}: degenerate mask")
            anns.append({"id":len(anns)+1,"image_id":i,"category_id":1,"segmentation":[[v for p in points for v in p]],
                         "bbox":[min(x),min(y),max(x)-min(x),max(y)-min(y)],"area":area,"iscrowd":0})
    if issues:
        print(json.dumps({"status":"blocked","review_issues":issues,"reviewed":len(images),"total":len(rows)},indent=2))
        return False
    doc={"info":{"description":"Palangka locked annotation cohort; source views may overlap spatially."},
         "images":images,"annotations":anns,"categories":[{"id":1,"name":"palm_crown"}]}
    with output.open("x",encoding="utf-8") as f:json.dump(doc,f,indent=2)
    print(json.dumps({"status":"exported","images":len(images),"annotations":len(anns)}))
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("root",type=Path);p.add_argument("output",type=Path)
    a=p.parse_args()
    if not export(a.root,a.output):raise SystemExit(2)


if __name__=="__main__":main()
