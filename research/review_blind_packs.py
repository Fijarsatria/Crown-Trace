"""Compare two independently authored annotation packs before adjudication.
Does not write final COCO or modify the locked cohort.
"""
import argparse,csv,hashlib,json,math
from pathlib import Path
from statistics import mean
from PIL import Image,ImageDraw
import numpy as np

def edge_cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

def collision(a,b,c,d):
    if max(a[0],b[0])<min(c[0],d[0]) or max(c[0],d[0])<min(a[0],b[0]) or max(a[1],b[1])<min(c[1],d[1]) or max(c[1],d[1])<min(a[1],b[1]):return False
    return edge_cross(a,b,c)*edge_cross(a,b,d)<=0 and edge_cross(c,d,a)*edge_cross(c,d,b)<=0

def read_manifest(root):
    with (root/"manifest.csv").open(encoding="utf-8-sig") as h:rows=list(csv.DictReader(h))
    for r in rows:
        file=root/"images"/r["tile"]
        if hashlib.sha256(file.read_bytes()).hexdigest()!=r["tile_sha256"]:raise ValueError(f"Hash mismatch: {r['tile']}")
    return rows

def status(root):
    rows=read_manifest(root)
    return {"images":len(rows),"verified_hashes":len(rows),"reviewed_manifest":sum(r["review_status"] in ("complete","empty_checked") for r in rows),
            "pending":sum(r["review_status"]=="pending" for r in rows),"ground_truth_ready":False,
            "note":"Manifest readiness only. Independent reviewer packs and adjudication are still required."}

def mask(p,w,h):
    if not isinstance(p,list) or not 3<=len(p)<=200 or any(not isinstance(x,list) or len(x)!=2 or not all(type(v) in (int,float) and math.isfinite(v) for v in x) or not(0<=x[0]<w and 0<=x[1]<h) for x in p):
        raise ValueError("Invalid polygon bounds")
    if len({tuple(x) for x in p})!=len(p):raise ValueError("Repeated polygon vertex")
    n=len(p)
    for i in range(n):
        for j in range(i+1,n):
            if j==i+1 or (i==0 and j==n-1):continue
            if collision(p[i],p[(i+1)%n],p[j],p[(j+1)%n]):raise ValueError("Self-intersecting polygon")
    area=abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(p,p[1:]+p[:1])))/2
    if area<9:raise ValueError("Degenerate polygon")
    img=Image.new("1",(w,h));ImageDraw.Draw(img).polygon([tuple(x) for x in p],fill=1)
    return np.asarray(img,dtype=bool)

def load_pack(path,rows):
    d=json.loads(path.read_text(encoding="utf-8-sig"))
    if d.get("schema")!="crowntrace-blind-v1" or not isinstance(d.get("reviewer"),str) or len(d["reviewer"].strip())<2 or d.get("round") not in ("A","B"):raise ValueError("Invalid reviewer metadata")
    tiles=d.get("tiles")
    if not isinstance(tiles,list) or len(tiles)!=len(rows):raise ValueError("Pack must contain every locked tile")
    for t,r in zip(tiles,rows):
        if t.get("tile")!=r["tile"] or t.get("sha256")!=r["tile_sha256"] or (t.get("width"),t.get("height"))!=(int(r["width"]),int(r["height"])):raise ValueError("Wrong source identity/dimensions")
        polygons=t.get("polygons");state=t.get("status")
        if not isinstance(polygons,list) or state not in ("complete","empty_checked") or (state=="empty_checked")!= (not polygons):raise ValueError(f"{t['tile']}: incomplete or contradictory review")
        for p in polygons:mask(p,t["width"],t["height"])
    return d

def matching(a,b):
    scores=[]
    for i,p in enumerate(a):
        for j,q in enumerate(b):
            union=np.count_nonzero(p|q)
            iou=float(np.count_nonzero(p&q)/union) if union else 0.
            if iou>=.5:scores.append((iou,i,j))
    # Largest IoU first, deterministic one-to-one review matching.
    used_a=set();used_b=set();matches=[]
    for score,i,j in sorted(scores,reverse=True):
        if i not in used_a and j not in used_b:matches.append((i,j,score));used_a.add(i);used_b.add(j)
    return matches

def compare(root,first,second,output):
    rows=read_manifest(root);a,b=load_pack(first,rows),load_pack(second,rows)
    if a["round"]==b["round"] or a["reviewer"].strip().casefold()==b["reviewer"].strip().casefold():raise ValueError("Two distinct reviewers and rounds required")
    result=[];all_iou=[]
    for x,y in zip(a["tiles"],b["tiles"]):
        p=[mask(z,x["width"],x["height"]) for z in x["polygons"]];q=[mask(z,y["width"],y["height"]) for z in y["polygons"]]
        pairs=matching(p,q);all_iou.extend(s for i,j,s in pairs)
        n75=sum(s>=.75 for i,j,s in pairs)
        result.append({"tile":x["tile"],"a_count":len(p),"b_count":len(q),"matched50":len(pairs),"matched75":n75,
                       "unmatched_a":len(p)-len(pairs),"unmatched_b":len(q)-len(pairs),
                       "needs_adjudication":len(p)!=len(q) or n75!=len(p) or n75!=len(q),
                       "pairs":[{"a":i+1,"b":j+1,"iou":s} for i,j,s in pairs]})
    summary={"status":"requires_adjudication","reviewer_a":a["reviewer"],"reviewer_b":b["reviewer"],"tiles":len(rows),
             "tiles_flagged":sum(r["needs_adjudication"] for r in result),"mean_matched_iou":mean(all_iou) if all_iou else None,
             "pairs50":len(all_iou),"pairs75":sum(s>=.75 for s in all_iou),
             "note":"No automatic ground truth. Review unmatched/boundary disagreements, record consensus, verify spatial independence, then use existing COCO gate.","by_tile":result}
    with output.open("x",encoding="utf-8") as h:json.dump(summary,h,indent=2)
    return summary

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("cohort",type=Path);p.add_argument("--first",type=Path);p.add_argument("--second",type=Path);p.add_argument("--output",type=Path)
    a=p.parse_args()
    if a.first or a.second:
        if not(a.first and a.second and a.output):p.error("Both packs and output required")
        result=compare(a.cohort,a.first,a.second,a.output)
    else:
        result=status(a.cohort)
        if a.output:
            with a.output.open("x",encoding="utf-8") as h:json.dump(result,h,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!="by_tile"},indent=2))
