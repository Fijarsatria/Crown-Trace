"""Run standard YOLO11-seg or Mask R-CNN on the same frozen Trees partitions.

Optional GPU research environment only; not a dependency of CrownTrace WebGIS.
"""
import argparse
import json
from pathlib import Path
from statistics import mean
from time import perf_counter

import numpy as np
from PIL import Image
import torch

from crowntrace.benchmark import ranked_ap
from crowntrace.evaluate_coco_crowns import LocalMask,bbox_window,mask_iou,IOU_THRESHOLDS
from crowntrace.train_coco_crowns import read_split,mask_pixels,digest
from crowntrace.coco_mask_audit import image_path
from research.contour_refit import overlap_matrix


def load_reference(tile):
    with Image.open(tile.path) as source: image=source.convert("RGB")
    masks=[]
    for det,polygon in tile.regions:
        win=bbox_window(det.box,image)
        binary=np.asarray(mask_pixels(polygon,win))>0
        masks.append(LocalMask(win,binary,int(binary.sum())))
    return image,masks


def local(binary):
    row,col=np.nonzero(binary)
    if not len(row): return LocalMask((0,0,1,1),np.zeros((1,1),bool),0)
    x,y,r,b=int(col.min()),int(row.min()),int(col.max())+1,int(row.max())+1
    return LocalMask((x,y,r,b),binary[y:b,x:r],int(binary.sum()))


def evaluate(tiles,predict,threshold=None):
    overlaps,scores={},{}
    references=[]
    milliseconds=[]
    for i,tile in enumerate(tiles):
        image,masks=load_reference(tile)
        start=perf_counter(); prediction=sorted(predict(image),key=lambda v:-v[1])[:300]
        milliseconds.append((perf_counter()-start)*1000)
        for binary,_ in prediction:
            if binary.shape!=(image.height,image.width):raise ValueError("Predicted mask dimensions differ.")
        pred=[local(binary) for binary,_ in prediction]
        scores[i]=[float(score) for _,score in prediction]
        overlaps[i]=overlap_matrix(pred,masks)
        references.append(len(masks))
    ap=ranked_ap(overlaps,scores,IOU_THRESHOLDS)
    def mae(t):return mean(abs(sum(s>=t for s in scores[i])-n) for i,n in enumerate(references))
    if threshold is None:threshold=min(np.arange(.05,.96,.05).round(2).tolist(),key=lambda t:(mae(t),-t))
    return dict(images=len(tiles),references=sum(references),mask_ap50=float(ap[0]),mask_ap75=float(ap[5]),
                mask_ap50_95=float(mean(ap)),count_cutoff=threshold,count_mae=mae(threshold),
                mean_inference_ms=mean(milliseconds))


class MaskDataset(torch.utils.data.Dataset):
    def __init__(self,tiles):self.tiles=tiles
    def __len__(self):return len(self.tiles)
    def __getitem__(self,index):
        tile=self.tiles[index];image,refs=load_reference(tile)
        masks=np.zeros((len(refs),image.height,image.width),dtype=np.uint8)
        for i,r in enumerate(refs):
            x,y,b,t=r.window;masks[i,y:t,x:b]=r.pixels
        rgb=torch.from_numpy(np.array(image)).permute(2,0,1).float()/255
        target={"boxes":torch.tensor([d.box for d,_ in tile.regions],dtype=torch.float32),
                "labels":torch.ones(len(refs),dtype=torch.int64),"masks":torch.from_numpy(masks)}
        return rgb,target


def run_maskrcnn(a,train,val):
    from torchvision.models.detection import maskrcnn_resnet50_fpn,MaskRCNN_ResNet50_FPN_Weights
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
    from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor
    weights=MaskRCNN_ResNet50_FPN_Weights.DEFAULT if a.pretrained else None
    model=maskrcnn_resnet50_fpn(weights=weights,weights_backbone=None,min_size=640,max_size=640,
                               box_score_thresh=.05,box_detections_per_img=300)
    model.roi_heads.box_predictor=FastRCNNPredictor(model.roi_heads.box_predictor.cls_score.in_features,2)
    model.roi_heads.mask_predictor=MaskRCNNPredictor(model.roi_heads.mask_predictor.conv5_mask.in_channels,256,2)
    model.to(a.device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.0002)
    loader=torch.utils.data.DataLoader(MaskDataset(train),batch_size=a.batch,shuffle=True,num_workers=0,collate_fn=lambda b:tuple(zip(*b)))
    @torch.inference_mode()
    def predict(image):
        model.eval()
        x=torch.from_numpy(np.array(image)).permute(2,0,1).float().to(a.device)/255
        out=model([x])[0]
        return [(m[0].cpu().numpy()>=.5,float(s)) for m,s in zip(out["masks"],out["scores"])]
    best=-1;history=[]
    for epoch in range(a.epochs):
        model.train();losses=[]
        for images,targets in loader:
            optimizer.zero_grad(set_to_none=True)
            values=model([x.to(a.device) for x in images],[{k:v.to(a.device) for k,v in target.items()} for target in targets])
            loss=sum(values.values());loss.backward();optimizer.step();losses.append(loss.item())
        metrics=evaluate(val,predict);history.append({"epoch":epoch+1,"loss":mean(losses),**metrics})
        print(history[-1],flush=True)
        if metrics["mask_ap50_95"]>best:
            best=metrics["mask_ap50_95"];torch.save(model.state_dict(),a.output/"best.pth")
        (a.output/"history.json").write_text(json.dumps(history,indent=2))
    model.load_state_dict(torch.load(a.output/"best.pth",weights_only=True,map_location=a.device))
    return predict


def verify_yolo_export(data,export_root):
    findings={}
    for split in ("train","val","test"):
        source=json.loads((data/split/"_annotations.coco.json").read_text(encoding="utf-8-sig"))
        images={r["id"]:r for r in source["images"]}
        expected={i:[] for i in images}
        for annotation in source["annotations"]:
            row=images[annotation["image_id"]]
            ring,=annotation["segmentation"]
            normalized=[v/(row["width"] if i%2==0 else row["height"]) for i,v in enumerate(ring)]
            expected[row["id"]].append("0 "+" ".join(f"{v:.8f}" for v in normalized))
        names=set()
        for i,row in images.items():
            original=image_path(data/split,row["file_name"])
            name=f"{i}_{original.name}"
            names.add(name)
            new=export_root/"images"/split/name
            label=export_root/"labels"/split/Path(name).with_suffix(".txt")
            if digest(new)!=digest(original) or label.read_text().splitlines()!=expected[i]:
                raise ValueError(f"YOLO export differs from COCO: {split}/{name}")
        if {f.name for f in (export_root/"images"/split).iterdir() if f.is_file()} != names:
            raise ValueError("Unexpected extra image in YOLO split")
        if {f.name for f in (export_root/"labels"/split).iterdir() if f.is_file()} != {Path(name).with_suffix(".txt").name for name in names}:
            raise ValueError("Unexpected extra label in YOLO split")
        findings[split]={"images":len(names),"objects":sum(map(len,expected.values())),
                         "source_sha256":digest(data/split/"_annotations.coco.json")}
    return findings


def run_yolo(a):
    from ultralytics import YOLO
    if a.yolo_data is None:raise ValueError("--yolo-data required")
    verified=verify_yolo_export(a.data,a.yolo_data.parent)
    (a.output/"yolo_export_verification.json").write_text(json.dumps(verified,indent=2))
    config=a.output/"verified_data.yaml"
    config.write_text(json.dumps({"path":a.yolo_data.parent.resolve().as_posix(),"train":"images/train",
                                 "val":"images/val","test":"images/test","names":["palm_crown"]},indent=2))
    model=YOLO("yolo11n-seg.pt" if a.pretrained else "yolo11n-seg.yaml")
    model.train(data=str(config.resolve()),epochs=a.epochs,imgsz=640,batch=a.batch,
                device=a.device,seed=a.seed,project=str(a.output.resolve()),name="training",workers=0,
                plots=False,verbose=False)
    model=YOLO(str(a.output/"training/weights/best.pt"))
    def predict(image):
        result=model.predict(np.asarray(image)[:,:,::-1].copy(),imgsz=640,conf=.05,max_det=300,
                             retina_masks=True,device=a.device,verbose=False)[0]
        if result.masks is None:return []
        return [(m.cpu().numpy()>.5,float(s)) for m,s in zip(result.masks.data,result.boxes.conf)]
    return predict


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend",choices=["yolo","maskrcnn"],required=True)
    p.add_argument("--data",type=Path,default=Path("data/roboflow_crowns_grouped_v1"))
    p.add_argument("--yolo-data",type=Path)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--epochs",type=int,default=30)
    p.add_argument("--batch",type=int,default=2)
    p.add_argument("--device",default="cuda:0")
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--pretrained",action="store_true")
    p.add_argument("--test",action="store_true")
    a=p.parse_args()
    if a.epochs<1 or a.batch<1 or a.output.exists():p.error("Use a new directory and positive budget")
    torch.manual_seed(a.seed);np.random.seed(a.seed)
    train,val=(read_split(a.data,s) for s in ("train","val"))
    if {t.group for t in train}&{t.group for t in val}:raise ValueError("Source group leakage")
    a.output.mkdir(parents=True,exist_ok=False)
    predict=run_yolo(a) if a.backend=="yolo" else run_maskrcnn(a,train,val)
    report={"backend":a.backend,"seed":a.seed,"epochs":a.epochs,"pretrained":a.pretrained,
            "partitions":{s:digest(a.data/s/"_annotations.coco.json") for s in ("train","val","test")},
            "validation":evaluate(val,predict),"scope":"Grouped Trees source; optional test is reused."}
    if a.test:report["test"]=evaluate(read_split(a.data,"test"),predict,report["validation"]["count_cutoff"])
    (a.output/"summary.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
