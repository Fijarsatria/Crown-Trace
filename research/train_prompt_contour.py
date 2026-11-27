"""Fit an explicit-box-prompt contour head with small-crown weighted loss."""
import argparse
import json
from pathlib import Path
import shutil
from time import perf_counter

import numpy as np
from PIL import Image
import torch
from torch.nn import functional as F

from crowntrace.compact import CompactDetector
from crowntrace.evaluate_coco_crowns import proposals
from crowntrace.refine_masks import matched_crops, jitter, foreground_iou
from crowntrace.segment import CrownPromptContourNet, box_prompt, crop_box, position_grid
from crowntrace.train_coco_crowns import digest, mask_pixels, read_split, save


def patch(image, box, polygon, size, padding):
    win = crop_box(box, *image.size, padding=padding)
    rgb = np.array(image.crop(win).resize((size,size), Image.Resampling.BILINEAR)).transpose(2,0,1)
    truth = np.array(mask_pixels(polygon,win).resize((size,size), Image.Resampling.NEAREST)) > 0
    return np.concatenate((rgb, box_prompt(box,win,size).numpy().astype(np.uint8)*255)), truth


def polygon_area(points):
    p = np.asarray(points)
    return .5 * abs(float(np.sum(p[:,0]*np.roll(p[:,1],-1)-p[:,1]*np.roll(p[:,0],-1))))


def prepare(tiles, detector, rng, size, padding, train):
    exact, realistic, areas, edges = [], [], [], []
    for tile in tiles:
        with Image.open(tile.path) as source:
            image = source.convert("RGB")
        refs = [d for d,_ in tile.regions]
        boxes = [d for d in proposals(detector,image) if d.score >= .25]
        matches = matched_crops(boxes,refs,.35 if train else .50)
        for j,(d,polygon) in enumerate(tile.regions):
            if not train and j not in matches:
                continue
            row = patch(image,d.box,polygon,size,padding)
            candidate = matches[j][0] if j in matches and (not train or rng.random() < .75) else jitter(d.box,rng)
            next_row = patch(image,candidate,polygon,size,padding)
            exact.append(row)
            realistic.append(next_row if next_row[1].any() else row)
            areas.append(polygon_area(polygon))
            x,y,r,b=d.box
            edges.append(min(x,y,image.width-r,image.height-b)<=5)
    def stack(rows):
        px, masks = zip(*rows,strict=True)
        return torch.from_numpy(np.stack(px)),torch.from_numpy(np.stack(masks))
    return stack(exact),stack(realistic),torch.tensor(areas),torch.tensor(edges)


def inputs(pixels,position):
    px=pixels.float()/255
    return torch.cat((px[:,:3], position.expand(len(px),-1,-1,-1),px[:,3:]),1).contiguous(memory_format=torch.channels_last)


def weighted_loss(logits,truth,weights,iou_weight=0.,boundary_weight=2.):
    prob=logits.sigmoid()
    desc=truth[:,None]
    band=(F.max_pool2d(desc,5,1,2)+F.max_pool2d(-desc,5,1,2))[:,0]
    pixel_weights=1+boundary_weight*band
    bce=(F.binary_cross_entropy_with_logits(logits,truth,reduction="none")*pixel_weights).sum((1,2))/pixel_weights.sum((1,2))
    dice=1-(2*(prob*truth).sum((1,2))+1)/((prob+truth).sum((1,2))+1)
    intersection=(prob*truth).sum((1,2))
    union=(prob+truth-prob*truth).sum((1,2))
    soft_iou=1-(intersection+1)/(union+1)
    return ((bce+dice+iou_weight*soft_iou)*weights).sum()/weights.sum()


@torch.inference_mode()
def calibration(model, rows, areas, limit, position):
    px,truth=rows
    p=torch.cat([model(inputs(part,position)).sigmoid()[:,0] for part in px.split(48)])
    cutoffs=torch.arange(.5,.951,.05)
    results=[]
    for threshold in (.25,.30,.35,.40,.45,.50,.55,.60,.65):
        iou=foreground_iou(p>=threshold,truth)
        strict=(iou[:,None]>=cutoffs).float().mean(1)
        small=areas<=limit
        results.append(dict(threshold=threshold,strict=float(strict.mean()),
                            small_strict=float(strict[small].mean()),mean_iou=float(iou.mean()),
                            small_iou=float(iou[small].mean()),
                            selection=float(.75*strict.mean()+.25*strict[small].mean())))
    return max(results,key=lambda x:x["selection"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data",type=Path,required=True)
    parser.add_argument("--base",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--epochs",type=int,default=6)
    parser.add_argument("--size",type=int,default=96)
    parser.add_argument("--seed",type=int,default=2027)
    parser.add_argument("--threads",type=int,default=6)
    parser.add_argument("--lr",type=float,default=.0008)
    parser.add_argument("--padding",type=float,default=.26)
    parser.add_argument("--small-weight",type=float,default=1.0)
    parser.add_argument("--iou-weight",type=float,default=0.)
    parser.add_argument("--boundary-weight",type=float,default=2.)
    a=parser.parse_args()
    if a.output.exists() or a.size%8 or not 64<=a.size<=256 or a.epochs<1 or a.threads<1 or not 0<=a.iou_weight<=5 or not 0<=a.boundary_weight<=10:
        parser.error("Use a fresh directory and valid size/epochs/threads.")
    torch.set_num_threads(a.threads); torch.manual_seed(a.seed)
    rng=np.random.default_rng(a.seed); gen=torch.Generator().manual_seed(a.seed)
    source=json.loads((a.base/"training.json").read_text())
    hashes={s:digest(a.data/s/"_annotations.coco.json") for s in ("train","val","test")}
    if hashes!=source["partition_sha256"] or any(digest(a.base/source[k]["checkpoint"])!=source[k]["sha256"] for k in ("detector","mask")):
        raise ValueError("Baseline data/checkpoint hash mismatch")
    train,val=[read_split(a.data,s) for s in ("train","val")]
    if {t.group for t in train}&{t.group for t in val}: raise ValueError("Split contamination")
    detector=CompactDetector(a.base/source["detector"]["checkpoint"])
    model=CrownPromptContourNet().to(memory_format=torch.channels_last)
    weights=torch.load(a.base/source["mask"]["checkpoint"],weights_only=True,map_location="cpu")["weights"]
    if weights["edge.0.weight"].shape[1]==5:
        weights["edge.0.weight"]=torch.cat((weights["edge.0.weight"],torch.zeros_like(weights["edge.0.weight"][:,:1])),1)
    model.load_state_dict(weights)
    a.output.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(a.base/source["detector"]["checkpoint"],a.output/"detector.pth")
    exact,real,areas,edge=prepare(train,detector,rng,a.size,a.padding,True)
    _,validation,val_areas,_=prepare(val,detector,rng,a.size,a.padding,False)
    small=float(torch.quantile(areas,.25))
    val_small=float(torch.quantile(val_areas,.25))
    loss_weight=1+a.small_weight*(areas<=small).float()+.35*edge.float()
    print("Prepared",len(areas),len(val_areas),"train_small_area_limit",small,flush=True)
    position=position_grid(a.size); optimizer=torch.optim.AdamW(model.parameters(),lr=a.lr)
    schedule=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,a.epochs,eta_min=a.lr*.1)
    best=None;history=[]
    for epoch in range(1,a.epochs+1):
        start=perf_counter(); model.train(); n=0;total=0.
        for batch in torch.randperm(len(areas),generator=gen).split(32):
            mix=torch.rand(len(batch),generator=gen)<.70
            px=torch.where(mix[:,None,None,None],real[0][batch],exact[0][batch])
            truth=torch.where(mix[:,None,None],real[1][batch],exact[1][batch])
            k=int(torch.randint(4,(),generator=gen))
            px,truth=px.rot90(k,(-2,-1)),truth.rot90(k,(-2,-1))
            if torch.rand((),generator=gen)<.5: px,truth=px.flip(-1),truth.flip(-1)
            feat=inputs(px,position)
            feat[:,:3]=(feat[:,:3]*(.8+.4*torch.rand((len(batch),3,1,1),generator=gen))).clamp(0,1)
            optimizer.zero_grad(set_to_none=True)
            value=weighted_loss(model(feat)[:,0],truth.float(),loss_weight[batch],a.iou_weight,a.boundary_weight)
            value.backward(); optimizer.step(); total+=value.item()*len(batch); n+=len(batch)
        schedule.step(); model.eval()
        result={**calibration(model,validation,val_areas,val_small,position),"epoch":epoch,"loss":total/n,"seconds":perf_counter()-start}
        history.append(result)
        if best is None or result["selection"]>best["selection"]:
            best=result
            save(a.output/"mask.pth",dict(architecture="prompt_contour",weights=model.state_dict(),threshold=best["threshold"],
                                          size=a.size,padding=a.padding,name=f"CrownPrompt-{a.size}",rotations=(0,)))
        report=dict(partition_sha256=hashes,seed=a.seed,detector=source["detector"],
                    selection="0.75 mean validation crop strict IoU success + 0.25 small-quartile strict success",
                    train_crops=len(areas),validation_matched_crops=len(val_areas),train_small_area_limit=small,
                    validation_small_area_limit=val_small,small_weight=a.small_weight,iou_weight=a.iou_weight,boundary_weight=a.boundary_weight,
                    mask=dict(checkpoint="mask.pth",sha256=digest(a.output/"mask.pth"),selected_epoch=best["epoch"],
                              history=history,size=a.size,padding=a.padding,threshold=best["threshold"]))
        (a.output/"training.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps(result),flush=True)


if __name__=="__main__":
    main()
