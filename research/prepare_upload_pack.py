"""Build a local, demonstrator-friendly image pack from existing audited datasets."""
from collections import Counter
from pathlib import Path
import csv
import hashlib
import json
import shutil

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEST = DATA / "UJI_SENDIRI"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def pick(items, number):
    return [items[round(i*(len(items)-1)/(number-1))] for i in range(number)] if len(items) > number > 1 else items[:number]


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    rows = []

    def put(source, folder, name, source_name, split, mode, reference="", notes=""):
        target = DEST / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        with Image.open(target) as img:
            width, height = img.size
        rows.append(dict(file=str(target.relative_to(DEST)).replace("\\", "/"), source=source_name,
                         original=str(source.relative_to(DATA)).replace("\\", "/"), split=split,
                         width=width, height=height, recommended_mode=mode,
                         reference=reference, notes=notes, sha256=hashlib.sha256(target.read_bytes()).hexdigest()))

    # Preserve all 18 held-out pilot images; names include reference box counts.
    manifest = read(DATA / "pilot_manifest.json")
    tests = sorted((x for x in manifest["samples"] if x["split"] == "test"), key=lambda x: len(x["objects"]))
    for i, row in enumerate(tests, 1):
        counts = Counter(manifest["categories"][str(obj[0])] for obj in row["objects"])
        put(DATA / "pilot_images" / row["file_name"], "01_MOPAD_HITUNG_DAN_MASK",
            f"MOPAD_{i:02d}_{len(row['objects']):03d}_box.jpg", "MOPAD Site 2", "pilot test (reused)",
            "MOPAD mask pilot / Hitung foto", json.dumps(dict(counts)),
            "Anotasi kotak sumber, bukan mask; Grass dipetakan berdasarkan protokol model. Holdout pernah dievaluasi.")

    # All 20 grouped test tiles from Trees v3 with COCO mask reference counts.
    roboflow = DATA / "roboflow_crowns_grouped_v1/test"
    coco = read(roboflow / "_annotations.coco.json")
    masks = Counter(a["image_id"] for a in coco["annotations"])
    for i, row in enumerate(sorted(coco["images"], key=lambda r: masks[r["id"]]), 1):
        n = masks[row["id"]]
        put(roboflow / row["file_name"], "02_TREES_SEGMENTASI_DETAIL_FAST", f"TREES_{i:02d}_{n:03d}_mask.jpg",
            "Roboflow Trees v3 (grouped)", "test (reused)", "CrownPrompt Detail / Fast", f"{n} mask COCO",
            "Referensi mask dari anotasi dataset, bukan jumlah yang harus diprediksi secara identik.")

    gan = DATA / "external/mendeley_ganoderma_v2/Ganoderma_UAV_RGB_Dataset_v2"
    coco = read(gan / "annotations/instances_test.json")
    label = {c["id"]: c["name"] for c in coco["categories"]}
    labels = {im["id"]: Counter() for im in coco["images"]}
    for ann in coco["annotations"]:
        labels[ann["image_id"]][label[ann["category_id"]]] += 1
    pos = [im for im in coco["images"] if labels[im["id"]]["ganoderma_affected_palm"]]
    neg = [im for im in coco["images"] if not labels[im["id"]]["ganoderma_affected_palm"]]
    for kind, group in (("KANDIDAT", pos), ("NEGATIF_SURVEI", neg)):
        for i, row in enumerate(pick(sorted(group, key=lambda im: im["file_name"]), 8), 1):
            put(gan / "images/test" / row["file_name"], "03_INSPEKSI_VISUAL_GANODERMA",
                f"{kind}_{i:02d}.png", "Ganoderma UAV RGB Dataset v2", "official block test (reused)",
                "Inspeksi Balanced / High Recall", json.dumps(dict(labels[row["id"]])),
                "Label asal survei; negatif tidak membuktikan sehat. Gunakan inspeksi, bukan mask Trees v3.")

    pal = DATA / "palangka_external_review_v1/images"
    for i, source in enumerate(pick(sorted(pal.glob("PAL_*.png")), 12), 1):
        put(source, "04_PALANGKA_UJI_EKSTERNAL", f"PALANGKA_{i:02d}.png", "Palangka Raya cohort", "external, review pending",
            "Hitung foto / MOPAD atau Detail (eksplorasi)", "belum tervalidasi", "Uji domain baru; tanpa GPS dan ground truth individu tervalidasi.")
    peru = DATA / "peru_anomaly_grouped_v1/test"
    files = sorted(peru.glob("*.jpg"))
    for i, source in enumerate(pick(files, 8), 1):
        put(source, "05_PERU_UJI_DOMAIN_LAIN", f"PERU_{i:02d}.jpg", "Peru Anomaly grouped", "test (external exploration)",
            "Hitung foto / segmentasi (eksplorasi)", "tidak disamakan dengan label MOPAD", "Domain berbeda; bukan uji konfirmatif kondisi visual.")

    put(DATA / "demo_mopad_mosaic.tif", "06_PETA_GEOTIFF", "01_MOPAD_DEMO_PETA_PENUH.tif", "MOPAD registered demonstration",
        "derived demo", "Peta kebun (GeoTIFF) + mask pilot", "koordinat geografis sintetis",
        "Citra MOPAD nyata; CRS dan posisi dibuat untuk demo, bukan lokasi aktual.")
    shutil.copy2(DATA / "demo_mopad_blocks.geojson", DEST / "06_PETA_GEOTIFF/01_MOPAD_DEMO_BATAS_BLOK.geojson")
    with (DEST / "INDEX_CITRA.csv").open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(f"Prepared {len(rows)} images in {DEST}")


if __name__ == "__main__":
    main()
