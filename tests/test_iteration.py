"""Regression checks for geometry prompts and the local inspection HTTP route."""
from io import BytesIO
import json

import numpy as np
from PIL import Image
import pytest
import torch

from crowntrace.segment import box_prompt,CrownMaskPredictor
from crowntrace.inspection import InspectionService,SAMPLE
from crowntrace.server import Service,make_handler
from crowntrace.dataset import ROOT
from test_pipeline import call


def test_box_prompt_clipping_and_rotation():
    p=box_prompt((0,10,40,50),(0,0,60,60),60)[0]
    assert p.sum().item()==1600
    assert p[10,0] and p[49,39] and not p[9,0] and not p[50,0]
    assert (p.rot90(1,(-2,-1)).rot90(-1,(-2,-1))==p).all()


def test_prompt_and_legacy_models_inference():
    from crowntrace.inference import Detection
    torch.set_num_threads(2)
    crop=Image.new("RGB",(100,100))
    det=[Detection((0,5,40,50),"palm_crown",.9)]
    for path in ("results/contour_20261125/selected/mask.pth","results/iteration_20261126/prompt96/mask.pth"):
        segmenter=CrownMaskPredictor(ROOT/path)
        values=list(segmenter.masks(crop,det))
        assert len(values)==1 and values[0][1].shape==(segmenter.size,segmenter.size)


def test_inspection_http_modes_and_validation(tmp_path):
    torch.set_num_threads(2)
    handler=make_handler(Service(tmp_path))
    status,body=call(handler,"/inspeksi")
    assert status==200 and "PENYARINGAN".encode() in body
    status,body=call(handler,"/api/inspection/status")
    assert status==200 and json.loads(body)["model_ready"]
    status,client=call(handler,"/api/inspection/sample-image")
    assert status==200 and client==SAMPLE.read_bytes()
    results={}
    for mode in ("balanced","high_recall"):
        status,body=call(handler,"/api/inspection?mode="+mode,"POST",client)
        assert status==200
        data=json.loads(body);results[mode]=data
        assert data["coordinate_unit"]=="pixel" and len(data["records"])==data["detections"]
    balanced,recall=(results[name] for name in ("balanced","high_recall"))
    assert balanced["detections"]==recall["detections"]==3
    assert balanced["review_candidates"]==0 and recall["review_candidates"]==1
    assert [r["bbox_px"] for r in balanced["records"]]==[r["bbox_px"] for r in recall["records"]]
    for request in (b"invalid",):
        status,_=call(handler,"/api/inspection?mode=balanced","POST",request)
        assert status==422
    status,_=call(handler,"/api/inspection?mode=invalid","POST",client)
    assert status==422
    buf=BytesIO();Image.new("RGB",(64,64)).save(buf,format="PNG")
    status,_=call(handler,"/api/inspection","POST",buf.getvalue())
    assert status==422


def test_inventory_one_to_one_and_block_mismatch():
    from research.evaluate_inventory import match,metrics
    pred=[dict(id="p1",lon=116.0,lat=0.,block="A"),
          dict(id="p2",lon=116.000012,lat=0.,block="B"),
          dict(id="p3",lon=116.0000121,lat=0.,block="B")]
    truth=[dict(id="t1",lon=116.000006,lat=0.,block="A"),
           dict(id="t2",lon=116.000020,lat=0.,block="C")]
    pairs=match(pred,truth,2)
    result=metrics(pred,truth,pairs,2)
    assert result["tp"]==2 and result["fp"]==1 and result["fn"]==0
    assert len({j for _,j,_ in pairs})==2 and result["cross_block_matches"]>=1


def test_palangka_unreviewed_never_exports(tmp_path):
    from research.validate_palangka_review import export,sha
    import csv
    root=tmp_path/"cohort";root.mkdir();(root/"images").mkdir();(root/"labelme").mkdir()
    img=root/"images/a.png";Image.new("RGB",(16,16)).save(img)
    scope=root/"scope.json";scope.write_text(json.dumps({"images":1}))
    csv_path=root/"manifest.csv"
    row={"tile":"a.png","source_view_group":1,"tile_sha256":sha(img),
         "review_status":"pending","annotator":"","second_reviewer":""}
    def save_row():
        with csv_path.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=row.keys());w.writeheader();w.writerow(row)
    save_row()
    label=root/"labelme/a.json"
    label.write_text(json.dumps({"shapes":[],"flags":{"review_pending":True}}))
    destination=root/"accepted.json"
    assert not export(root,destination) and not destination.exists()
    row.update(review_status="empty_checked",annotator="A",second_reviewer="B");save_row()
    assert not export(root,destination)
    label.write_text(json.dumps({"shapes":[],"flags":{"review_pending":False}}))
    assert export(root,destination)
    assert len(json.loads(destination.read_text())["images"])==1
