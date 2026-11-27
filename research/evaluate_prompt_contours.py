"""Validation-gated comparison of contour checkpoints on the same frozen detector."""
import argparse
import json
from pathlib import Path
from statistics import mean
from time import perf_counter

import numpy as np
from PIL import Image
import torch

from crowntrace.benchmark import ranked_ap
from crowntrace.compact import CompactDetector
from crowntrace.evaluate_coco_crowns import LocalMask,bbox_window,mask_iou,proposals,draw_panel,IOU_THRESHOLDS
from crowntrace.segment import CrownMaskPredictor,box_prompt,crop_box,rgb_tensor
from crowntrace.train_coco_crowns import mask_pixels,read_split,digest
from research.audit_contour_failures import matched_reference
from research.contour_refit import overlap_matrix


@torch.inference_mode()
def probabilities(predictor,image,boxes,rotations):
    windows=[crop_box(d.box,*image.size,padding=predictor.padding) for d in boxes]
    outputs=[]
    for start in range(0,len(boxes),48):
        win=windows[start:start+48]; det=boxes[start:start+48]
        rgb=torch.stack([rgb_tensor(image.crop(w),predictor.size) for w in win])
        prompts=torch.stack([box_prompt(d.box,w,predictor.size) for d,w in zip(det,win,strict=True)]) if predictor.requires_prompt else None
        value=sum(predictor.probabilities(rgb.rot90(k,(-2,-1)),prompts.rot90(k,(-2,-1)) if prompts is not None else None)
                  .rot90(-k,(-2,-1)) for k in rotations)/len(rotations)
        outputs.extend(zip(win,value.numpy(),strict=True))
    return outputs


def box_area(det):
    x,y,r,b=det.box
    return (r-x)*(b-y)


def masks(probs,cut,boxes):
    if isinstance(cut,tuple):
        large,small,limit=cut
        cuts=[small if box_area(d)<=limit else large for d in boxes]
    else:
        cuts=[cut]*len(boxes)
    return [LocalMask.resized(window,p>=threshold) for (window,p),threshold in zip(probs,cuts,strict=True)]


def benchmark(tiles,detector,configs,output,split):
    records={name:dict(overlaps={},hits50=[],hits75=[],oracle=[],small_hit75=[],small_oracle=[],ms=[]) for name in configs}
    area=[]
    prepared=[]
    for tile in tiles:
        with Image.open(tile.path) as photo:image=photo.convert("RGB")
        boxes=[d for d,_ in tile.regions]
        refs=[]
        for d,poly in tile.regions:
            win=bbox_window(d.box,image); p=np.array(mask_pixels(poly,win))>0
            refs.append(LocalMask(win,p,int(p.sum())))
        area.extend(r.area for r in refs)
        prepared.append((tile,image,boxes,refs))
    small_limit=float(np.quantile(area,.25));scores={}
    for i,(tile,image,boxes,refs) in enumerate(prepared):
        pred=proposals(detector,image);scores[i]=[d.score for d in pred]
        keep=np.array([d.score>=.25 for d in pred],dtype=bool)
        small=[j for j,r in enumerate(refs) if r.area<=small_limit]
        cache={}
        for name,(model,rotations,threshold) in configs.items():
            key=(id(model),rotations)
            if key not in cache:
                start=perf_counter()
                cache[key]=(probabilities(model,image,pred,rotations),probabilities(model,image,boxes,rotations),perf_counter()-start)
            raw,oracle,seconds=cache[key]
            guessed=masks(raw,threshold,pred)
            mat=overlap_matrix(guessed,refs)
            records[name]["overlaps"][i]=mat
            score_keep=np.array(scores[i])[keep]
            hits50=matched_reference(mat[keep],score_keep,.5)
            hits75=matched_reference(mat[keep],score_keep,.75)
            oracle_iou=[mask_iou(a,b) for a,b in zip(masks(oracle,threshold,boxes),refs,strict=True)]
            rec=records[name];rec["hits50"].append(len(hits50));rec["hits75"].append(len(hits75));rec["oracle"].extend(oracle_iou)
            rec["small_hit75"].extend(j in hits75 for j in small);rec["small_oracle"].extend(oracle_iou[j] for j in small);rec["ms"].append(seconds*1000)
            if split=="test" and i in (0,len(tiles)//2,len(tiles)-1):
                draw_panel(image,refs,[g for g,k in zip(guessed,keep,strict=True) if k],tile.path.name,output/f"{name}_{i:02d}.png")
        if (i+1)%5==0: print(split,i+1,"/",len(tiles),flush=True)
    result={}
    for name,rec in records.items():
        aps=ranked_ap(rec["overlaps"],scores,IOU_THRESHOLDS)
        result[name]=dict(ap50=float(aps[0]),ap75=float(aps[5]),ap50_95=float(mean(aps)),
                          matched_mask50=sum(rec["hits50"]),matched_mask75=sum(rec["hits75"]),
                          strict_recall75=sum(rec["hits75"])/len(area),small_recall75=float(mean(rec["small_hit75"])),
                          oracle_iou=float(mean(rec["oracle"])),small_oracle_iou=float(mean(rec["small_oracle"])),
                          inference_ms_mean=mean(rec["ms"]),small_count=len(rec["small_hit75"]),
                          threshold=configs[name][2],rotations=configs[name][1])
    return dict(n=len(area),area_quartile_limit=small_limit,configs=result)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data",type=Path,required=True);p.add_argument("--baseline",type=Path,required=True)
    p.add_argument("--candidates",nargs="+",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True); p.add_argument("--test",action="store_true")
    p.add_argument("--threads",type=int,default=6)
    a=p.parse_args();torch.set_num_threads(a.threads)
    a.output.mkdir(parents=True,exist_ok=False)
    detector=CompactDetector(a.baseline/"detector.pth")
    base=CrownMaskPredictor(a.baseline/"mask.pth")
    base_cut=(base.threshold,base.small_threshold,base.small_box_area) if base.small_box_area is not None else base.threshold
    config={"baseline":(base,base.rotations,base_cut)}
    detectorhash=digest(a.baseline/"detector.pth")
    train_areas=[box_area(d) for tile in read_split(a.data,"train") for d,_ in tile.regions]
    limits=[float(np.quantile(train_areas,q)) for q in (.25,.50)]
    for path in a.candidates:
        if digest(path/"detector.pth")!=detectorhash:raise ValueError("Detector must be frozen")
        model=CrownMaskPredictor(path/"mask.pth")
        for rot in ((0,),(0,1,2,3)):
            for cut in sorted(set([.35,.40,.45,.50,model.threshold])):
                config[f"{path.name}_r{len(rot)}_t{cut:.2f}"]=(model,rot,cut)
            pairs=((.35,.45),(.40,.50)) if len(rot)==4 else ((.35,.45),)
            for j,limit in enumerate(limits if len(rot)==4 else limits[:1]):
                for large,small in pairs:
                    spec=(large,small,limit)
                    config[f"{path.name}_r{len(rot)}_q{j+1}_L{large:.2f}_S{small:.2f}"]=(model,rot,spec)
    val=benchmark(read_split(a.data,"val"),detector,config,a.output,"val")
    base_metrics=val["configs"]["baseline"]
    eligible=[name for name,m in val["configs"].items() if m["ap75"]>=base_metrics["ap75"]-.02
              and m["small_recall75"]>=base_metrics["small_recall75"] and m["ap50_95"]>=base_metrics["ap50_95"]]
    best=max(eligible,key=lambda name:(val["configs"][name]["ap50_95"],val["configs"][name]["small_recall75"],val["configs"][name]["ap50"]))
    unconstrained=max(val["configs"],key=lambda name:val["configs"][name]["ap50_95"])
    info={"scope":"Grouped validation selection. Test, if requested, is a reused exploratory holdout, not an independent estate.",
          "selection":"Maximum AP50:95 among validation candidates with AP75 within 0.02 of baseline, small recall no worse and mean AP no worse. Size-aware cuts based on training box area quartile or median.",
          "unconstrained_best":unconstrained,"eligible":eligible,
          "train_box_area_limits":limits,
          "detector_sha256":detectorhash,"validation":val,"selected":best}
    (a.output/"summary.json").write_text(json.dumps(info,indent=2),encoding="utf-8")
    print("selected",best,val["configs"][best],flush=True)
    if a.test:
        info["test"]=benchmark(read_split(a.data,"test"),detector,{name:config[name] for name in dict.fromkeys(["baseline",best])},a.output,"test")
        (a.output/"summary.json").write_text(json.dumps(info,indent=2),encoding="utf-8")
        print("test",info["test"],flush=True)


if __name__=="__main__":
    main()
