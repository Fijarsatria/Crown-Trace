"""Select 24 nearby audited MOPAD crops for a more complete relative-mosaic demo.

Selection uses only source offsets and image coverage, never model predictions.
"""
from pathlib import Path
from collections import Counter
import csv
import hashlib
import json
import shutil

from crowntrace.audit_overlap import offsets
from crowntrace.dataset import ROOT, manifest

DATA=ROOT/"data"; PACK=DATA/"UJI_SENDIRI"; INDEX=PACK/"INDEX_CITRA.csv"


def footprint(positions):
    xs=sorted({v for x,y in positions for v in (x,x+1024)})
    area=0
    for a,b in zip(xs,xs[1:]):
        ranges=sorted((y,y+1024) for x,y in positions if x<=a and x+1024>=b)
        if not ranges:continue
        left,right=ranges[0];covered=0
        for lo,hi in ranges[1:]:
            if lo>right:covered+=right-left;left,right=lo,hi
            else:right=max(right,hi)
        area+=(b-a)*(covered+right-left)
    total=(max(x for x,y in positions)-min(x for x,y in positions)+1024)*(max(y for x,y in positions)-min(y for x,y in positions)+1024)
    return area/total,area,total


def main():
    with INDEX.open(encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f);fields=reader.fieldnames;prior=[r for r in reader if not r["file"].startswith("07_MOPAD_")]
    existing={Path(r["original"]).name for r in prior if r["file"].startswith("01_MOPAD_")}
    source=manifest(DATA/"mopad_site2_val_manifest.json")
    rows=[r for r in source["samples"] if r["group"]=="52000_20000" and r["file_name"] not in existing]
    pts=[offsets(row["file_name"]) for row in rows]
    options=[]
    for x,y in pts:
        ids=sorted(range(len(pts)),key=lambda j:(pts[j][0]-x)**2+(pts[j][1]-y)**2)[:24]
        cover,area,total=footprint([pts[j] for j in ids])
        options.append((cover,area,total,ids))
    cover,area,total,ids=max(options,key=lambda v:(v[0]>=.9,v[1] if v[0]>=.9 else v[0],v[0]))
    chosen=sorted((rows[i] for i in ids),key=lambda r:(offsets(r["file_name"])[1],offsets(r["file_name"])[0]))
    folder=PACK/"07_MOPAD_MOSAIC_BERDEKATAN";folder.mkdir(exist_ok=True)
    for i,r in enumerate(chosen,1):
        name=r["file_name"];counts=Counter(source["categories"][str(obj[0])] for obj in r["objects"])
        x,y=offsets(name);dest=folder/f"MOSAIC_{i:02d}_x{x}_y{y}.jpg";orig=DATA/"mopad_site2_val_images"/name
        shutil.copy2(orig,dest)
        prior.append(dict(file=f"{folder.name}/{dest.name}",source="MOPAD Site 2",original=f"mopad_site2_val_images/{name}",split="test (reused; adjacent demo)",width=1024,height=1024,recommended_mode="MOPAD / resolusi tinggi, relative mosaic",reference=json.dumps(dict(counts)),notes="Dipilih berdasarkan letak dan cakupan saja; koordinat relatif, bukan GPS.",sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
    with INDEX.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(prior)
    report={"group":"52000_20000","images":24,"covered_fraction_of_bounding_rectangle":cover,"covered_pixels":area,"bounding_rectangle_pixels":total,"coordinate_mode":"relative_pixels"}
    (folder/"COVERAGE.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
