"""Read the compact MOPAD pilot manifest and build a local YOLO dataset on demand."""
import argparse
from collections import Counter
from hashlib import file_digest
import json
from pathlib import Path
import shutil

from PIL import Image

from .inference import Detection, LABELS

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def manifest(path=DATA / "pilot_manifest.json"):
    contents = json.loads(Path(path).read_text(encoding="utf-8"))
    samples = contents["samples"]
    groups = {}
    files = set()
    for row in samples:
        group, split, name = row["group"], row["split"], row["file_name"]
        if split not in ("train", "val", "test") or group in groups and groups[group] != split:
            raise ValueError(f"Grup lintas partisi: {group}")
        if Path(name).name != name or name in files:
            raise ValueError(f"Nama citra tidak valid atau ganda: {name}")
        groups[group] = split
        files.add(name)
    return contents


def annotations(row, categories, size):
    width, height = size
    for category, x, y, w, h in row["objects"]:
        label = categories[str(category)]
        label = "Healthy" if label == "Grass" else label
        if label not in LABELS:
            raise ValueError(f"Kategori tidak didukung: {label}")
        box = (max(0, x), max(0, y), min(width, x + w), min(height, y + h))
        if box[2] > box[0] and box[3] > box[1]:
            yield Detection(box, label, 1.0)


def download(man, images):
    images.mkdir(parents=True, exist_ok=True)
    import gdown
    for row in man["samples"]:
        target = images / row["file_name"]
        if target.is_file() and target.stat().st_size == row["source_bytes"]:
            continue
        temporary = target.with_suffix(".download")
        url = f'https://drive.google.com/uc?id={row["file_id"]}'
        output = gdown.download(url, str(temporary), quiet=True)
        if not output or not temporary.is_file() or temporary.stat().st_size != row["source_bytes"]:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"Gagal mengunduh {target.name} dengan ukuran resmi.")
        with Image.open(temporary) as image:
            image.verify()
        temporary.replace(target)


def audit(man, images):
    groups, counts, digests = {}, {}, {}
    for row in man["samples"]:
        name, split = row["file_name"], row["split"]
        path = images / name
        if not path.is_file() or path.stat().st_size != row["source_bytes"]:
            raise ValueError(f"Ukuran citra hilang atau berbeda: {name}")
        with path.open("rb") as file:
            digest = file_digest(file, "sha256").hexdigest()
        if digest in digests:
            raise ValueError(f"Citra identik: {name} dan {digests[digest]}")
        digests[digest] = name
        with Image.open(path) as image:
            if image.size != (1024, 1024):
                raise ValueError(f"Dimensi citra tidak cocok: {name}")
            image.verify()
            boxes = list(annotations(row, man["categories"], image.size))
        groups.setdefault(split, set()).add(row["group"])
        counts.setdefault(split, Counter()).update([d.label for d in boxes])
        counts[split]["images"] += 1
    return {"groups": {k: sorted(v) for k, v in groups.items()}, "counts": counts,
            "sha256": {v: k for k, v in digests.items()}}


def export_yolo(man, images, output):
    if output.exists():
        raise ValueError(f"Pilih folder keluaran baru: {output}")
    audit(man, images)
    for row in man["samples"]:
        name, split = row["file_name"], row["split"]
        image_dir, label_dir = output / "images" / split, output / "labels" / split
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        source = images / name
        with Image.open(source) as image:
            width, height = image.size
        lines = []
        for det in annotations(row, man["categories"], (width, height)):
            cx, cy = det.center
            x, y, r, b = det.box
            lines.append(f"{LABELS.index(det.label)} {cx/width:.8f} {cy/height:.8f} "
                         f"{(r-x)/width:.8f} {(b-y)/height:.8f}")
        shutil.copyfile(source, image_dir / name)
        (label_dir / Path(name).with_suffix(".txt")).write_text("\n".join(lines), encoding="utf-8")
    config = {"path": output.resolve().as_posix(), "train": "images/train",
              "val": "images/val", "test": "images/test", "names": list(LABELS)}
    # JSON syntax is accepted by YAML loaders.
    (output / "data.yaml").write_text(json.dumps(config, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, default=DATA / "pilot_images")
    parser.add_argument("--manifest", type=Path, default=DATA / "pilot_manifest.json")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--export-yolo", type=Path)
    args = parser.parse_args()
    data = manifest(args.manifest)
    if args.download:
        download(data, args.images)
    result = audit(data, args.images)
    print(json.dumps({k: v for k, v in result.items() if k != "sha256"}, indent=2))
    if args.audit:
        args.audit.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.export_yolo:
        export_yolo(data, args.images, args.export_yolo)


if __name__ == "__main__":
    main()
