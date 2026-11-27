"""One-to-one inventory evaluation for WGS84 point exports versus reviewed field points."""
import argparse
import csv
import json
import math
from collections import defaultdict,deque
from pathlib import Path
from statistics import mean,median

import numpy as np

EARTH=6371008.8


def read_points(path,field=False):
    document=json.loads(path.read_text(encoding="utf-8-sig"))
    if document.get("type")!="FeatureCollection" or document.get("crs"):
        raise ValueError("Expected RFC 7946 WGS84 FeatureCollection without custom CRS.")
    rows=[];ids=set()
    for f in document["features"]:
        if f.get("geometry",{}).get("type")!="Point":raise ValueError("Point geometry required")
        xy=f["geometry"]["coordinates"];p=f.get("properties",{})
        if len(xy)!=2 or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in xy) or not(-180<=xy[0]<=180 and -85<=xy[1]<=85):
            raise ValueError("Invalid WGS84 lon/lat")
        key=str(p.get("id","")).strip()
        if not key or key in ids:raise ValueError("Point IDs must be unique and nonempty")
        ids.add(key)
        block=str(p.get("block","")).strip() or "Unassigned"
        if field and (block=="Unassigned" or not p.get("survey_date")):
            raise ValueError("Field reference requires block and survey_date for each point")
        rows.append({"id":key,"lon":xy[0],"lat":xy[1],"block":block})
    return rows


def distance(a,b):
    p,q=math.radians(a["lat"]),math.radians(b["lat"])
    dp=q-p; dl=math.radians(b["lon"]-a["lon"])
    h=math.sin(dp/2)**2+math.cos(p)*math.cos(q)*math.sin(dl/2)**2
    return 2*EARTH*math.asin(min(1,math.sqrt(h)))


def match(pred,truth,tolerance):
    if not pred or not truth:return []
    # Compact plantations: grid candidate pruning, then exact spherical distances.
    all_points=pred+truth
    lon=[r["lon"] for r in all_points];lat=[r["lat"] for r in all_points]
    if max(lon)-min(lon)>.5 or max(lat)-min(lat)>.5:
        raise ValueError("Evaluate one local plantation area (<0.5 degree extent) per run.")
    lon0,lat0=mean(lon),mean(lat); scale=math.pi/180*EARTH
    def xy(row):return ((row["lon"]-lon0)*scale*math.cos(math.radians(lat0)),(row["lat"]-lat0)*scale)
    def cell(row):
        x,y=xy(row)
        return math.floor(x/tolerance),math.floor(y/tolerance)
    index=defaultdict(list)
    for j,row in enumerate(truth):index[cell(row)].append(j)
    adjacency=[]
    for p in pred:
        x,y=cell(p);neighbors=[]
        for dx in range(-2,3):
            for dy in range(-2,3):
                for j in index[(x+dx,y+dy)]:
                    d=distance(p,truth[j])
                    if d<=tolerance:neighbors.append((j,d))
        adjacency.append(sorted(neighbors,key=lambda v:(v[1],v[0])))
    # Maximum-cardinality bipartite matching using deterministic augmenting paths.
    # Edge order prefers nearby points; does not claim globally minimum summed distance.
    owner={}; assigned={}
    order=sorted(range(len(pred)),key=lambda i:(len(adjacency[i]),adjacency[i][0][1] if adjacency[i] else 0,pred[i]["id"]))
    for start in order:
        if not adjacency[start]:continue
        queue=deque([start]);seen_p={start};parents={};end=None
        while queue and end is None:
            i=queue.popleft()
            for j,_ in adjacency[i]:
                if j in parents:continue
                parents[j]=i
                if j not in owner:end=j;break
                nxt=owner[j]
                if nxt not in seen_p:seen_p.add(nxt);queue.append(nxt)
        if end is None:continue
        j=end
        while True:
            i=parents[j];previous=assigned.get(i)
            owner[j]=i;assigned[i]=j
            if previous is None:break
            j=previous
    return [(i,j,distance(pred[i],truth[j])) for i,j in sorted(assigned.items())]


def metrics(pred,truth,matches,tolerance):
    n=len(matches); p=n/max(1,len(pred));r=n/max(1,len(truth))
    errors=[d for _,_,d in matches]
    block_names=sorted({x["block"] for x in pred+truth})
    block_stats=[]
    for name in block_names:
        pr=sum(x["block"]==name for x in pred);gt=sum(x["block"]==name for x in truth)
        same=sum(pred[i]["block"]==truth[j]["block"]==name for i,j,_ in matches)
        block_stats.append({"block":name,"reference":gt,"prediction":pr,"count_bias":pr-gt,
                            "matched_same_block":same,"precision_same_block":same/max(1,pr),
                            "recall_same_block":same/max(1,gt),
                            "cross_block_assigned_reference":sum(truth[j]["block"]==name and pred[i]["block"]!=name for i,j,_ in matches)})
    return {"predictions":len(pred),"references":len(truth),"tp":n,"fp":len(pred)-n,"fn":len(truth)-n,
            "precision":p,"recall":r,"f1":2*p*r/max(1e-12,p+r),"count_bias":len(pred)-len(truth),
            "tolerance_m":tolerance,"median_matched_distance_m":median(errors) if errors else None,
            "p95_matched_distance_m":float(np.quantile(errors,.95)) if errors else None,
            "cross_block_matches":sum(pred[i]["block"]!=truth[j]["block"] for i,j,_ in matches),
            "block_count_mae":mean(abs(x["count_bias"]) for x in block_stats) if block_stats else None,"blocks":block_stats}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("predictions",type=Path);p.add_argument("reference",type=Path);p.add_argument("output",type=Path)
    p.add_argument("--tolerance-m",type=float,required=True);p.add_argument("--scope",choices=["demo","unverified","field"],default="unverified")
    a=p.parse_args()
    if not math.isfinite(a.tolerance_m) or a.tolerance_m<=0:p.error("Explicit positive tolerance in meters required")
    pred,truth=read_points(a.predictions),read_points(a.reference,a.scope=="field")
    pairs=match(pred,truth,a.tolerance_m);result=metrics(pred,truth,pairs,a.tolerance_m)
    result["scope"]=a.scope;result["matching"]="maximum-cardinality one-to-one, exact haversine within fixed tolerance; not minimum total distance"
    result["notes"]="Field scope is user-declared; confirm source coverage/IDs/CRS independently. No disease metric."
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    rows=[dict(prediction_id=pred[i]["id"],reference_id=truth[j]["id"],distance_m=d,
               prediction_block=pred[i]["block"],reference_block=truth[j]["block"]) for i,j,d in pairs]
    with (a.output/"matches.csv").open("w",encoding="utf-8",newline="") as f:
        keys=("prediction_id","reference_id","distance_m","prediction_block","reference_block")
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
    print(json.dumps(result,indent=2))


if __name__=="__main__":main()
