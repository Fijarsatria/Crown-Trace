"""Render a fixed held-out example using the original image and saved predictions."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = ImageFont.load_default(size=23)
SMALL = ImageFont.load_default(size=17)
COLORS = {"Dead": "#ff7668", "Healthy": "#84e69a", "Small": "#7bc2fa", "Yellow": "#ffe168"}


def panel(canvas, image, offset, title, detections):
    x, y = offset
    size = 384
    preview = image.resize((size, size), Image.Resampling.LANCZOS)
    pen = ImageDraw.Draw(preview)
    for label, box in detections:
        xyxy = tuple(round(value * size / image.width) for value in box)
        pen.rectangle(xyxy, outline=COLORS[label], width=1)
    canvas.paste(preview, (x, y))
    draw = ImageDraw.Draw(canvas)
    draw.text((x, y - 29), title, font=SMALL, fill="#1c4434")


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--benchmark", type=Path, default=ROOT / "results/expanded_benchmark")
    args.add_argument("--output", type=Path, default=ROOT / "results/heldout_example.png")
    parsed = args.parse_args()
    manifest = json.loads((ROOT / "data/pilot_manifest.json").read_text(encoding="utf-8"))
    sample = next(row for row in manifest["samples"] if row["split"] == "test")
    source = ROOT / "data/pilot_images" / sample["file_name"]
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    metadata = json.loads((parsed.benchmark / "summary.json").read_text(encoding="utf-8"))
    minimum = metadata["metrics"]["test"]["full_640"]["operating_threshold"]
    annotations = []
    for class_id, x, y, w, h in sample["objects"]:
        condition = manifest["categories"][str(class_id)]
        annotations.append(("Healthy" if condition == "Grass" else condition, (x, y, x+w, y+h)))
    predictions = {}
    with (parsed.benchmark / "predictions.jsonl").open(encoding="utf-8") as file:
        for line in file:
            row = json.loads(line)
            if row["split"] == "test" and row["image"] == sample["file_name"]:
                predictions[row["method"]] = [(d["label"], d["box"]) for d in row["detections"] if d["score"] >= minimum]
    canvas = Image.new("RGB", (1232, 538), "#f4f6ef")
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 17), "CrownTrace  /  contoh dari kelompok uji", font=FONT, fill="#163d2e")
    draw.text((24, 52), f"{sample['file_name']}  ·  MOPAD Site 2  ·  Ambang {minimum:.2f}", font=SMALL, fill="#4b6255")
    for idx, (title, rows) in enumerate((("Anotasi sumber", annotations),
                                        ("Gambar penuh", predictions["full_640"]),
                                        ("Potongan adaptif K=2", predictions["adaptive"]))):
        panel(canvas, image, (24 + idx * 400, 106), f"{title}   |   {len(rows)} tajuk", rows)
    for idx, (label, color) in enumerate(zip(("Sehat", "Mati", "Kecil", "Menguning"),
                                             [COLORS[k] for k in ("Healthy", "Dead", "Small", "Yellow")])):
        x = 25 + idx * 137
        draw.ellipse((x, 508, x + 11, 519), fill=color, outline="#61746a")
        draw.text((x + 18, 504), label, font=SMALL, fill="#284639")
    canvas.save(parsed.output)


if __name__ == "__main__":
    main()
