from pathlib import Path
import json
import csv

import numpy as np
from PIL import Image

from crowntrace.lab_batch_map import compose, _local_mask, identify
from crowntrace.prepare_annotation_drafts import encode_rle


def sample(root, ident, x, relative=True):
    folder=root/ident
    folder.mkdir()
    Image.new("RGB", (32,32), (30,90,40)).save(folder/"original.png")
    s={"source":ident+".png","width":32,"height":32,"placement":{"kind":"audited_relative_pixels","group":"test","x":x,"y":0} if relative else None}
    (folder/"summary.json").write_text(json.dumps(s),encoding="utf-8")
    rect=(10-x,10,16-x,16)
    mask=np.ones((6,6),dtype=bool)
    ann={"id":1,"bbox":[rect[0],rect[1],6,6],"segmentation":encode_rle(mask,rect,(32,32))}
    (folder/"crowns.coco.json").write_text(json.dumps({"annotations":[ann]}),encoding="utf-8")
    with (folder/"trees.csv").open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["id","label","score","x_px","y_px","mask_px"]);writer.writeheader();writer.writerow({"id":1,"label":"Healthy","score":.9,"x_px":13-x,"y_px":13,"mask_px":36})


def test_relative_map_deduplicates_and_exports_layers(tmp_path):
    ids=["a"*32,"b"*32]
    sample(tmp_path,ids[0],0);sample(tmp_path,ids[1],4)
    report=compose(tmp_path,ids)
    assert report["mode"]=="relative_mosaic" and report["raw_occurrences"]==2
    assert report["displayed_candidates"]==1 and report["displayed_masks"]==1
    folder=tmp_path/"batch"/report["id"]
    for file in ("original.jpg","crowns.png","points.png","coverage.png"):
        with Image.open(folder/file) as im:assert im.size==(report["width"],report["height"])
    with Image.open(folder/"crowns.png") as im:assert im.mode=="RGBA" and im.getchannel("A").getbbox() is not None


def test_independent_grid_does_not_claim_unique_count(tmp_path):
    ids=["a"*32,"b"*32]
    sample(tmp_path,ids[0],0,False);sample(tmp_path,ids[1],4,False)
    report=compose(tmp_path,ids)
    assert report["mode"]=="image_grid" and report["coordinate_mode"]=="independent_display_grid"
    assert report["raw_occurrences"]==report["displayed_candidates"]==2


def test_mask_window_decoding_agrees_with_original():
    mask=np.zeros((7,9),dtype=bool);mask[1:5,2:7]=True;mask[2,3]=False
    rle=encode_rle(mask,(3,5,12,12),(30,31))
    assert np.array_equal(np.asarray(_local_mask(rle,[3,5,9,7]))>0,mask)


def test_auto_profile_unknown_bytes_not_guessed():
    assert identify(b"not a curated image")["recommended"] is None
