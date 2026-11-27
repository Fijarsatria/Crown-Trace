"""Train the compact CPU pilot or an Ultralytics baseline on a prepared data.yaml."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import random
from time import perf_counter

from PIL import Image, ImageEnhance
import torch
from torch.utils.data import DataLoader, Dataset

from .compact import CrownNet, MultiScaleCrownNet, loss, targets, to_tensor
from .dataset import DATA, annotations, audit, manifest
from .inference import Detection, LABELS


class PilotDataset(Dataset):
    def __init__(self, data, split, images, size, augment):
        self.size, self.augment = size, augment
        self.rows = []
        for row in data["samples"]:
            if row["split"] != split:
                continue
            path = images / row["file_name"]
            # Store compressed JPEGs in memory, while opening the pixels only when needed.
            image_bytes = path.read_bytes()
            self.rows.append((image_bytes, list(annotations(row, data["categories"], (1024, 1024)))))

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, idx):
        image_bytes, boxes = self.rows[idx]
        with Image.open(BytesIO(image_bytes)) as original:
            image = original.convert("RGB")
        width, height = image.size
        if self.augment:
            side = random.choice([512, 640, 768, 1024, 1024])
            x = random.randrange(width - side + 1)
            y = random.randrange(height - side + 1)
            image = image.crop((x, y, x + side, y + side))
            width, height = image.size
            mirror = random.random() < 0.5
            flip = random.random() < 0.5
            if mirror:
                image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if flip:
                image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            image = ImageEnhance.Brightness(image).enhance(random.uniform(0.80, 1.20))
            cropped = []
            for d in boxes:
                cx, cy = d.center
                if not (x <= cx < x + side and y <= cy < y + side):
                    continue
                left, top, right, bottom = d.box
                left, right = max(0, left - x), min(side, right - x)
                top, bottom = max(0, top - y), min(side, bottom - y)
                if mirror:
                    left, right = side - right, side - left
                if flip:
                    top, bottom = side - bottom, side - top
                cropped.append(Detection((left, top, right, bottom), d.label, 1))
            boxes = cropped
        return to_tensor(image, self.size), *targets(boxes, width, height, self.size)


def train_compact(args):
    data = manifest(args.manifest)
    audited = audit(data, args.images)
    torch.set_num_threads(args.threads)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device(args.device)
    counts = audited["counts"]["train"]
    weights = torch.tensor([max(1, counts[label]) ** -0.50 for label in LABELS], device=device)
    weights /= weights.mean()
    architecture = MultiScaleCrownNet if args.architecture == "multiscale" else CrownNet
    model = architecture().to(device)
    if args.warm_start:
        initial = torch.load(args.warm_start, map_location=device, weights_only=True)
        model_keys = set(model.state_dict())
        new_keys = {key for key in model_keys if key.startswith(("detail.", "context.", "refine."))}
        if set(initial) not in (model_keys, model_keys - new_keys):
            raise ValueError("Checkpoint warm start tidak cocok dengan arsitektur.")
        model.load_state_dict(initial, strict=set(initial) == model_keys)
    args.output.mkdir(parents=True, exist_ok=False)
    train = DataLoader(PilotDataset(data, "train", args.images, args.size, True),
                       batch_size=args.batch, shuffle=True)
    val_size = args.val_size or args.size
    val = DataLoader(PilotDataset(data, "val", args.images, val_size, False), batch_size=args.batch)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.epochs, eta_min=args.lr * 0.075)
    best_loss = float("inf")
    history = []
    for epoch in range(1, args.epochs + 1):
        clock = perf_counter()
        train_sum = val_sum = 0.0
        model.train()
        for batch in train:
            x, heat, box, y = [tensor.to(device) for tensor in batch]
            optimizer.zero_grad(set_to_none=True)
            value = loss(model(x), heat, box, y, weights)
            value.backward()
            optimizer.step()
            train_sum += value.item() * len(x)
        scheduler.step()
        model.eval()
        with torch.inference_mode():
            for batch in val:
                x, heat, box, y = [tensor.to(device) for tensor in batch]
                val_sum += loss(model(x), heat, box, y, weights).item() * len(x)
        val_loss = val_sum / len(val.dataset)
        if val_loss < best_loss:
            torch.save(model.state_dict(), args.output / "best.pth")
            best_loss = val_loss
        record = {"epoch": epoch, "train_loss": train_sum / len(train.dataset),
                  "val_loss": val_loss, "seconds": perf_counter() - clock}
        history.append(record)
        print(json.dumps(record), flush=True)
    torch.save(model.state_dict(), args.output / "last.pth")
    config = {"seed": args.seed, "device": args.device, "size": args.size, "val_size": val_size,
              "architecture": args.architecture, "warm_start": str(args.warm_start) if args.warm_start else None,
              "learning_rate": args.lr, "batch": args.batch,
              "threads": args.threads, "parameters": sum(v.numel() for v in model.parameters()),
              "torch": torch.__version__, "best_epoch": min(history, key=lambda row: row["val_loss"])["epoch"]}
    (args.output / "history.json").write_text(json.dumps({"config": config, "epochs": history}, indent=2),
                                              encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("compact", "yolo"), default="compact")
    parser.add_argument("--manifest", type=Path, default=DATA / "pilot_manifest.json")
    parser.add_argument("--images", type=Path, default=DATA / "pilot_images")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-yaml", type=Path)
    parser.add_argument("--init", default="yolov8n.pt")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--architecture", choices=("baseline", "multiscale"), default="baseline")
    parser.add_argument("--warm-start", type=Path)
    parser.add_argument("--val-size", type=int)
    parser.add_argument("--lr", type=float, default=0.002)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if args.epochs < 1 or args.batch < 1 or args.size < 64 or args.size % 32 or args.lr <= 0:
        parser.error("Epoch, batch, dan laju belajar harus positif, dan ukuran kelipatan 32 dengan minimum 64.")
    if args.val_size is not None and (args.val_size < 64 or args.val_size % 32):
        parser.error("Ukuran validasi kelipatan 32 dengan minimum 64.")
    if args.backend == "compact":
        train_compact(args)
    else:
        if not args.data_yaml or not args.data_yaml.is_file():
            parser.error("--data-yaml diperlukan untuk YOLO.")
        if args.output.exists():
            parser.error("--output harus menunjuk ke folder baru.")
        from ultralytics import YOLO
        YOLO(args.init, task="detect").train(data=str(args.data_yaml.resolve()), imgsz=args.size,
                                            epochs=args.epochs, batch=args.batch, seed=args.seed,
                                            device=args.device, workers=0, deterministic=True,
                                            project=str(args.output.resolve().parent), name=args.output.name)


if __name__ == "__main__":
    main()
