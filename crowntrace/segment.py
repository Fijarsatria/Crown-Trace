"""A compact box-prompted instance mask model trained on traced MOPAD crowns."""
import argparse
from hashlib import file_digest
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import torch
from torch import nn
from torch.nn import functional as F

from .dataset import DATA

SIZE = 64


class CrownMaskNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.detail = nn.Sequential(nn.Conv2d(5, 12, 3, padding=1), nn.ReLU(),
                                    nn.Conv2d(12, 16, 3, padding=1), nn.ReLU())
        self.context = nn.Sequential(nn.Conv2d(16, 24, 3, padding=1), nn.ReLU(),
                                     nn.Conv2d(24, 24, 3, padding=1), nn.ReLU())
        self.output = nn.Sequential(nn.Conv2d(40, 16, 3, padding=1), nn.ReLU(),
                                    nn.Conv2d(16, 1, 1))

    def features(self, image):
        detail = self.detail(image)
        context = self.context(F.avg_pool2d(detail, 2))
        return torch.cat((detail, F.interpolate(context, scale_factor=2, mode="bilinear", align_corners=False)), 1)

    def forward(self, image):
        return self.output(self.features(image))


class CrownMaskRefiner(CrownMaskNet):
    """Add wider crop context and a residual contour prediction."""

    def __init__(self):
        super().__init__()
        self.wide = nn.Sequential(nn.Conv2d(40, 24, 3, padding=1), nn.GroupNorm(4, 24), nn.SiLU(),
                                  nn.Conv2d(24, 16, 3, padding=2, dilation=2), nn.GroupNorm(4, 16), nn.SiLU())
        self.skip = nn.Sequential(nn.Conv2d(40, 12, 1), nn.SiLU())
        self.delta = nn.Sequential(nn.Conv2d(28, 12, 3, padding=1), nn.SiLU(), nn.Conv2d(12, 1, 1))
        nn.init.zeros_(self.delta[-1].weight)
        nn.init.zeros_(self.delta[-1].bias)

    def forward(self, image):
        features = self.features(image)
        wide = self.wide(F.avg_pool2d(features, 4))
        context = F.interpolate(wide, size=features.shape[-2:], mode="bilinear", align_corners=False)
        correction = self.delta(torch.cat((self.skip(features), context), dim=1))
        return self.output(features) + correction


def conv_norm(in_channels, out_channels, stride=1):
    return nn.Sequential(nn.Conv2d(in_channels, out_channels, 3, stride, 1, bias=False),
                         nn.BatchNorm2d(out_channels), nn.SiLU())


class CrownContourNet(nn.Module):
    """Predict a crown outline with high-resolution skip features and wider context."""

    def __init__(self):
        super().__init__()
        self.edge = conv_norm(5, 12)
        self.down_half = nn.Sequential(conv_norm(12, 20, 2), conv_norm(20, 20))
        self.quarter = nn.Sequential(conv_norm(20, 32, 2), conv_norm(32, 32))
        self.context = nn.Sequential(conv_norm(32, 48, 2), conv_norm(48, 48), conv_norm(48, 48))
        self.up_quarter = conv_norm(80, 32)
        self.up_half = conv_norm(52, 20)
        self.up_edge = nn.Sequential(conv_norm(32, 12), nn.Conv2d(12, 1, 1))

    @staticmethod
    def combine(coarse, fine):
        up = F.interpolate(coarse, size=fine.shape[-2:], mode="bilinear", align_corners=False)
        return torch.cat((up, fine), dim=1)

    def forward(self, image):
        edge = self.edge(image)
        half = self.down_half(edge)
        quarter = self.quarter(half)
        context = self.context(quarter)
        quarter = self.up_quarter(self.combine(context, quarter))
        half = self.up_half(self.combine(quarter, half))
        return self.up_edge(self.combine(half, edge))


class CrownPromptContourNet(CrownContourNet):
    """Contour model with a spatial box-prompt channel."""

    def __init__(self):
        super().__init__()
        self.edge = conv_norm(6, 12)


def box_prompt(box, window, size):
    """Mark pixel centers inside the supplied detection box, even for clipped crops."""
    x, y, r, b = window
    axis = (torch.arange(size, dtype=torch.float32) + .5) / size
    yy, xx = torch.meshgrid(y + axis * (b-y), x + axis * (r-x), indexing="ij")
    left, top, right, bottom = box
    return ((xx >= left) & (xx <= right) & (yy >= top) & (yy <= bottom)).float()[None]


def crop_box(box, width, height, padding=.13):
    x, y, right, bottom = box
    px, py = (right - x) * padding, (bottom - y) * padding
    left, top = min(width - 1, max(0, round(x - px))), min(height - 1, max(0, round(y - py)))
    return (left, top, min(width, max(left + 1, round(right + px))),
            min(height, max(top + 1, round(bottom + py))))


def rgb_tensor(image, size=SIZE):
    return torch.from_numpy(np.array(image.resize((size, size), Image.Resampling.BILINEAR),
                                     dtype=np.float32).transpose(2, 0, 1)) / 255


def position_grid(size):
    axis = (torch.arange(size, dtype=torch.float32) + 0.5) / size * 2 - 1
    yy, xx = torch.meshgrid(axis, axis, indexing="ij")
    return torch.stack((xx, yy))


POSITION = position_grid(SIZE)
xx, yy = POSITION
ELLIPSE = (xx.square() + yy.square()) < 0.64


def single_outline(binary):
    """Keep the component nearest the crop center and close its internal holes."""
    yy, xx = binary.nonzero()
    if not len(xx):
        return binary
    cy, cx = (np.asarray(binary.shape) - 1) / 2
    closest = np.argmin((yy - cy) ** 2 + (xx - cx) ** 2)
    foreground = Image.fromarray(binary.astype(np.uint8)).copy()
    ImageDraw.floodfill(foreground, (int(xx[closest]), int(yy[closest])), 2)
    enclosed = np.pad((np.asarray(foreground) == 2).astype(np.uint8), 1)
    exterior = Image.fromarray(enclosed).copy()
    ImageDraw.floodfill(exterior, (0, 0), 2)
    return np.asarray(exterior)[1:-1, 1:-1] != 2


class CrownMaskPredictor:
    def __init__(self, weights, device="cpu"):
        self.device = torch.device(device)
        checkpoint = torch.load(weights, weights_only=True, map_location=self.device)
        architecture = checkpoint.get("architecture", "compact")
        architectures = {"compact": CrownMaskNet, "refined": CrownMaskRefiner, "contour": CrownContourNet, "prompt_contour": CrownPromptContourNet}
        if architecture not in architectures:
            raise ValueError(f"Unsupported crown mask architecture: {architecture}")
        self.requires_prompt = architecture == "prompt_contour"
        self.size = checkpoint.get("size", SIZE)
        self.padding = checkpoint.get("padding", .13)
        self.rotations = tuple(checkpoint.get("rotations", (0,)))
        self.solid_outline = checkpoint.get("solid_outline", False)
        if not self.rotations or any(k not in (0, 1, 2, 3) for k in self.rotations):
            raise ValueError("Mask rotations must be right-angle turns.")
        if not (64 <= self.size <= 256 and self.size % 8 == 0 and 0 <= self.padding <= .75):
            raise ValueError("Unsupported mask crop size or padding.")
        self.position = position_grid(self.size).to(self.device)
        self.model = architectures[architecture]().to(self.device, memory_format=torch.channels_last)
        self.model.load_state_dict(checkpoint["weights"])
        self.model.eval()
        self.threshold = checkpoint["threshold"]
        self.small_box_area = checkpoint.get("small_box_area")
        self.small_threshold = checkpoint.get("small_threshold")
        if (self.small_box_area is None) != (self.small_threshold is None):
            raise ValueError("Incomplete size-aware mask threshold.")
        if self.small_box_area is not None and (self.small_box_area <= 0 or not 0 < self.small_threshold < 1):
            raise ValueError("Invalid size-aware mask threshold.")
        if not 0 < self.threshold < 1:
            raise ValueError("Invalid mask threshold.")
        with Path(weights).open("rb") as handle:
            self.version = checkpoint.get("name", "CrownMask-Pilot") + "@" + file_digest(handle, "sha256").hexdigest()[:12]

    @torch.inference_mode()
    def probabilities(self, tensors, prompts=None):
        pixels = tensors.to(self.device)
        shape = pixels.shape[-2:]
        desired = (self.size, self.size)
        if shape != desired:
            pixels = F.interpolate(pixels, size=desired, mode="bilinear", align_corners=False)
        coords = self.position.expand(len(pixels), -1, -1, -1)
        inputs = [pixels, coords]
        if self.requires_prompt:
            if prompts is None:
                raise ValueError("Explicit box prompts are required for this mask model.")
            prompts = prompts.to(self.device).float()
            if prompts.shape != (len(pixels), 1, *desired):
                prompts = F.interpolate(prompts, size=desired, mode="nearest")
            inputs.append(prompts)
        values = self.model(torch.cat(inputs, dim=1).contiguous(memory_format=torch.channels_last))
        if shape != desired:
            values = F.interpolate(values, size=shape, mode="bilinear", align_corners=False)
        return values.sigmoid()[:, 0]

    @torch.inference_mode()
    def crop_masks(self, tensors, prompts=None, thresholds=None):
        if self.rotations == (0,):
            probabilities = self.probabilities(tensors, prompts) if prompts is not None else self.probabilities(tensors)
        else:
            probabilities = sum((self.probabilities(tensors.rot90(turn, (-2, -1)), prompts.rot90(turn, (-2, -1)))
                                 if prompts is not None else self.probabilities(tensors.rot90(turn, (-2, -1)))).rot90(-turn, (-2, -1))
                                for turn in self.rotations) / len(self.rotations)
        cutoff = self.threshold if thresholds is None else thresholds.to(probabilities.device)[:, None, None]
        return probabilities >= cutoff

    @torch.inference_mode()
    def masks(self, image, detections):
        crops = [crop_box(det.box, *image.size, padding=self.padding) for det in detections]
        for start in range(0, len(crops), 48):
            batch = crops[start:start + 48]
            tensors = torch.stack([rgb_tensor(image.crop(box), self.size) for box in batch])
            prompts = (torch.stack([box_prompt(det.box, window, self.size)
                                    for det, window in zip(detections[start:start+48], batch, strict=True)])
                       if self.requires_prompt else None)
            cuts = None
            if self.small_box_area is not None:
                cuts = torch.tensor([self.small_threshold if (d.box[2]-d.box[0])*(d.box[3]-d.box[1]) <= self.small_box_area
                                     else self.threshold for d in detections[start:start+48]],dtype=torch.float32)
            masks = self.crop_masks(tensors, prompts, cuts).cpu().numpy()
            if self.solid_outline:
                masks = [single_outline(mask) for mask in masks]
            yield from zip(batch, masks)


def read_labels(path, images):
    labels = json.loads(path.read_text(encoding="utf-8"))["samples"]
    groups = {}
    examples = {split: [] for split in ("train", "val", "test")}
    for row in labels:
        group, split = row["group"], row["split"]
        if groups.setdefault(group, split) != split:
            raise ValueError(f"Area yang sama ada di dua partisi: {group}")
        name = row["image"]
        if Path(name).name != name:
            raise ValueError(f"Nama gambar tidak valid: {name}")
        with Image.open(images / name) as image:
            patch = image.convert("RGB").crop(row["crop"])
        mask = Image.new("L", (SIZE, SIZE))
        ImageDraw.Draw(mask).polygon([(x * SIZE / 128, y * SIZE / 128)
                                      for x, y in row["polygon_128"]], fill=1)
        examples[split].append((rgb_tensor(patch), torch.from_numpy(np.array(mask, dtype=np.float32))))
    if not all(examples.values()):
        raise ValueError("Label train, val, dan test dibutuhkan.")
    return examples


def augment(image, mask, generator):
    rotation = int(torch.randint(4, (), generator=generator))
    image, mask = image.rot90(rotation, (-2, -1)), mask.rot90(rotation, (-2, -1))
    if torch.rand((), generator=generator) < 0.5:
        image, mask = image.flip(-1), mask.flip(-1)
    offsets = (torch.rand(4, generator=generator) - 0.5) * 0.23
    transform = torch.tensor([[[1 + offsets[0], 0, offsets[2]],
                               [0, 1 + offsets[1], offsets[3]]]])
    grid = F.affine_grid(transform, (1, 4, SIZE, SIZE), align_corners=False)
    patch = F.grid_sample(torch.cat((image, mask[None]))[None], grid, padding_mode="border",
                          align_corners=False)[0]
    gain = 0.78 + torch.rand((3, 1, 1), generator=generator) * 0.44
    return torch.cat(((patch[:3] * gain).clamp(0, 1), POSITION)), (patch[3] >= 0.5).float()


def stack(rows):
    return (torch.stack([torch.cat((rgb, POSITION)) for rgb, _ in rows]),
            torch.stack([mask for _, mask in rows]))


def iou_score(predicted, truth):
    overlap = (predicted & truth).sum((-2, -1))
    union = (predicted | truth).sum((-2, -1))
    return round((overlap / union.clamp_min(1)).mean().item(), 4)


def train(labels, images, output, epochs):
    torch.set_num_threads(4)
    torch.manual_seed(27)
    generator = torch.Generator().manual_seed(27)
    examples = read_labels(labels, images)
    model = CrownMaskNet()
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.002)
    validation, valid_masks = stack(examples["val"])
    thresholds = (0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65)
    best = (-1., 0.5, None, 0)
    for epoch in range(1, epochs + 1):
        model.train()
        for _ in range(4):
            rows = [augment(rgb, mask, generator) for rgb, mask in examples["train"]]
            inputs = torch.stack([rgb for rgb, _ in rows])
            masks = torch.stack([mask for _, mask in rows])
            optimizer.zero_grad()
            logits = model(inputs)[:, 0]
            loss = F.binary_cross_entropy_with_logits(logits, masks)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            predictions = model(validation).sigmoid()[:, 0]
            score, threshold = max((iou_score(predictions >= cut, valid_masks.bool()), cut)
                                   for cut in thresholds)
        if score > best[0]:
            best = (score, threshold, {k: v.clone() for k, v in model.state_dict().items()}, epoch)
        if epoch == 1 or epoch % 20 == 0:
            print(f"Epoch {epoch}/{epochs} · IoU validasi {score:.3f}", flush=True)
    valid_iou, threshold, weights, epoch = best
    model.load_state_dict(weights)
    test, test_masks = stack(examples["test"])
    with torch.inference_mode():
        predicted = model(test).sigmoid()[:, 0] >= threshold
    report = {"reference": "MOPAD Site 2, pilot image-traced crown outlines, approximate",
              "scope": "Mask IoU on labeled reference crops; excludes detector and map-position errors.",
              "samples": {k: len(v) for k, v in examples.items()}, "epoch": epoch, "threshold": threshold,
              "validation_mean_iou": valid_iou, "test_mean_iou": iou_score(predicted, test_masks.bool()),
              "ellipse_test_mean_iou": iou_score(ELLIPSE.expand_as(predicted), test_masks.bool())}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    torch.save({"weights": weights, "threshold": threshold}, temporary)
    temporary.replace(output)
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DATA / "crown_masks_mopad.json")
    parser.add_argument("--images", type=Path, default=DATA / "mopad_site2_val_images")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=80)
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("Jumlah epoch harus positif.")
    train(args.labels, args.images, args.output, args.epochs)


if __name__ == "__main__":
    main()
