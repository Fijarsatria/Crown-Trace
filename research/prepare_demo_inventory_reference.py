"""Prepare a geographic QA reference from MOPAD registered pixel annotations.

Only for synthetic-coordinate software QA; never use this as a real field census.
"""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from research.evaluate_inventory import distance


def inside(x,y,ring):
    hit=False
    for (xa,ya),(xb,yb) in zip(ring,ring[1:]):
        if (ya>y)!=(yb>y) and x<(xb-xa)*(y-ya)/(yb-ya)+xa:hit=not hit
    return hit


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--map",required=True,type=Path);p.add_argument("--registered",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    trees=json.loads((a.map/"map.json").read_text())["trees"]
    X=np.array([[*r["center"],1] for r in trees]);Y=np.array([r["coordinates"] for r in trees])
    coeff=np.linalg.lstsq(X,Y,rcond=None)[0]
    errors=[distance(dict(lon=u[0],lat=u[1]),dict(lon=v[0],lat=v[1])) for u,v in zip(Y,X@coeff)]
    if max(errors)>=.1:raise ValueError("Affine demo projection cannot be reproduced reliably.")
    blocks=json.loads((a.map/"blocks.geojson").read_text())["features"]
    features=[]
    for row in csv.DictReader((a.registered/"test_points.csv").open()):
        if row["method"]!="no_overlap" or row["kind"]!="reference":continue
        lon,lat=(np.array([float(row["x"]),float(row["y"]),1])@coeff).tolist()
        names=[b["properties"]["block"] for b in blocks if inside(lon,lat,b["geometry"]["coordinates"][0])]
        features.append(dict(type="Feature",geometry=dict(type="Point",coordinates=[lon,lat]),
                              properties=dict(id="MOPAD-ref-"+row["id"],block=names[0] if len(names)==1 else "Unassigned")))
    if len(features)!=1704 or len({f["properties"]["id"] for f in features})!=1704:raise ValueError("Unexpected reference cohort")
    (a.output/"reference.geojson").write_text(json.dumps({"type":"FeatureCollection","features":features}))
    radius=json.loads((a.registered/"summary.json").read_text())["match_radius_px"]
    u=np.array([0,0,1])@coeff;v=np.array([1,0,1])@coeff
    scale=distance(dict(lon=u[0],lat=u[1]),dict(lon=v[0],lat=v[1]))
    report={"scope":"Synthetic geographic QA transform fitted from paired pixel/export positions, NOT field coordinates",
            "reference":"1704 deduplicated MOPAD annotation centers in reconstructed test pixel frame",
            "max_affine_residual_m":max(errors),"meters_per_pixel_approx":scale,"validation_match_radius_px":radius,
            "qa_tolerance_m":scale*radius}
    (a.output/"provenance.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
