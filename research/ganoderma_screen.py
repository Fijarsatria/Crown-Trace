"""Apply the evaluated Ganoderma V2 inspection model to a matching 640px RGB tile."""

import argparse
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw
import torch

from crowntrace.compact import CompactDetector
from research.evaluate_ganoderma_pipeline import screen_image
from research.ganoderma_condition import load_classifier, PADDINGS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--research",type=Path,default=Path("results/deep_20261126"))
    parser.add_argument("--mode", choices=("balanced", "high_recall"), default="balanced",
                        help="Choose a validation-selected inspection threshold.")
    args=parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    torch.set_num_threads(4)
    config=json.loads((args.research/"ganoderma_pipeline/summary.json").read_text())
    classifier=config["selected_classifier"]
    detector=CompactDetector(args.research/"ganoderma_detector/detector.pth")
    model=load_classifier(args.research/"ganoderma_condition"/classifier["checkpoint"])
    score_mode=config["score_mode"]
    operating=json.loads((args.research/"ganoderma_operating_points.json").read_text())
    cutoff=operating["validation"][args.mode]["threshold"]
    if operating["validation"]["balanced"]["threshold"] != config["validation"][score_mode]["threshold"]:
        raise ValueError("Operating point does not match the selected pipeline.")
    with Image.open(args.image) as photo:
        if photo.size!=(640,640) or photo.format not in {"JPEG","PNG"}:
            raise ValueError("Gunakan tile RGB 640 × 640, sesuai data riset Ganoderma V2.")
        image=photo.convert("RGB")
    results=screen_image(image,detector,model,PADDINGS[classifier["method"]],config["detector_threshold"],score_mode)
    draw=ImageDraw.Draw(image)
    records=[]
    for index,(det,probability,score) in enumerate(results,1):
        review=score>=cutoff
        color="#ff9dc6" if review else "#8bf0ab"
        draw.rectangle(det.box,outline=color,width=3)
        draw.text((det.box[0]+3,det.box[1]+3),str(index),fill=color,stroke_width=1,stroke_fill="#142822",font_size=15)
        records.append({"id":index,"x_px":det.center[0],"y_px":det.center[1],"box":list(det.box),
                        "detector_score":det.score,"survey_label_score":probability,"review_score":score,
                        "review_candidate":review})
    result={"scope":"Prediksi riset pada tile RGB yang sesuai distribusi Ganoderma V2; kandidat inspeksi belum dikonfirmasi lapangan.",
            "image":args.image.name,"coordinates":"pixels","mode":args.mode,"detections":len(records),
            "review_candidates":sum(row["review_candidate"] for row in records),"review_cutoff":cutoff,
            "detector":detector.version,"classifier":classifier["checkpoint"],"records":records}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as temporary:
        stage=Path(temporary)/"complete"
        stage.mkdir()
        image.save(stage/"overlay.png")
        (stage/"summary.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
        with (stage/"trees.csv").open("w",encoding="utf-8-sig",newline="") as file:
            fields=("id","x_px","y_px","box","detector_score","survey_label_score","review_score","review_candidate")
            writer=csv.DictWriter(file,fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
        stage.rename(args.output)
    print(json.dumps({k:v for k,v in result.items() if k!="records"},indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
