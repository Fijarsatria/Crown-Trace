"""A compact, downloadable-through-source training baseline for the real-image pilot."""
import math
from hashlib import file_digest
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.nn import functional as F

from .inference import Detection, LABELS

STRIDE = 8
PILOT_SIZES = {640: 256, 1024: 416}


class CrownNet(nn.Module):
    def __init__(self, class_count=len(LABELS)):
        super().__init__()
        layers = []
        for incoming, outgoing, stride in ((3, 16, 2), (16, 24, 2), (24, 40, 2),
                                           (40, 48, 1), (48, 48, 1)):
            layers.extend((nn.Conv2d(incoming, outgoing, 3, stride, 1, bias=False),
                           nn.BatchNorm2d(outgoing), nn.SiLU()))
        self.backbone = nn.Sequential(*layers)
        self.head = nn.Conv2d(48, 1 + 4 + class_count, 1)
        with torch.no_grad():
            self.head.bias[0] = -2.19

    def forward(self, images):
        return self.head(self.backbone(images))


class MultiScaleCrownNet(CrownNet):
    """Combine early spatial detail and coarse context with the baseline head."""

    def __init__(self, class_count=len(LABELS)):
        super().__init__(class_count)
        self.detail = nn.Sequential(nn.Conv2d(24, 16, 2, stride=2, bias=False), nn.BatchNorm2d(16), nn.SiLU())
        self.context = nn.Sequential(nn.Conv2d(48, 16, 3, padding=1, bias=False), nn.BatchNorm2d(16), nn.SiLU())
        self.refine = nn.Sequential(nn.Conv2d(80, 24, 3, padding=1, bias=False), nn.BatchNorm2d(24), nn.SiLU(),
                                    nn.Conv2d(24, 1 + 4 + class_count, 1))
        nn.init.zeros_(self.refine[-1].weight)
        nn.init.zeros_(self.refine[-1].bias)

    def forward(self, images):
        features = images
        for index, layer in enumerate(self.backbone):
            features = layer(features)
            if index == 5:
                spatial = self.detail(features)
        context = self.context(F.avg_pool2d(features, 2, ceil_mode=True))
        context = F.interpolate(context, size=features.shape[-2:], mode="bilinear", align_corners=False)
        return self.head(features) + self.refine(torch.cat((features, spatial, context), dim=1))


def to_tensor(image, size):
    rgb = np.asarray(image.resize((size, size), Image.Resampling.BILINEAR), dtype=np.float32)
    return torch.from_numpy(rgb.transpose(2, 0, 1).copy()).div_(255)


def targets(rows, width, height, size, class_names=LABELS):
    grid = size // STRIDE
    heat = torch.zeros(grid, grid)
    boxes = torch.zeros(4, grid, grid)
    labels = torch.full((grid, grid), -1, dtype=torch.long)
    for d in rows:
        left, top, right, bottom = d.box
        center_x, center_y = d.center
        cx, cy = center_x * grid / width, center_y * grid / height
        x, y = int(cx), int(cy)
        if not (0 <= x < grid and 0 <= y < grid):
            continue
        w, h = (right - left) * grid / width, (bottom - top) * grid / height
        if w < 0.2 or h < 0.2:
            continue
        heat[y, x] = 1
        boxes[:, y, x] = torch.tensor([cx - x, cy - y, math.log(w), math.log(h)])
        labels[y, x] = class_names.index(d.label)
    return heat, boxes, labels


def distance_iou_loss(predicted, target):
    predicted_center, target_center = predicted[:, :2], target[:, :2]
    predicted_half = predicted[:, 2:].clamp(-3, 5).exp() / 2
    target_half = target[:, 2:].exp() / 2
    left, right = predicted_center - predicted_half, predicted_center + predicted_half
    target_left, target_right = target_center - target_half, target_center + target_half
    intersection = (torch.minimum(right, target_right) - torch.maximum(left, target_left)).clamp_min(0).prod(1)
    union = 4 * (predicted_half.prod(1) + target_half.prod(1)) - intersection
    diagonal = (torch.maximum(right, target_right) - torch.minimum(left, target_left)).square().sum(1)
    offset = (predicted_center - target_center).square().sum(1)
    return (1 - intersection / union.clamp_min(1e-6) + offset / diagonal.clamp_min(1e-6)).mean()


def loss(prediction, heat, boxes, labels, class_weights, overlap_weight=0.):
    score = prediction[:, 0].sigmoid()
    probability_true = torch.where(heat > 0, score, 1 - score)
    focal = F.binary_cross_entropy_with_logits(prediction[:, 0], heat, reduction="none")
    object_loss = (focal * (1 - probability_true).square()).sum() / heat.sum().clamp(min=1)
    positive = labels >= 0
    if not positive.any():
        return object_loss
    regression = prediction[:, 1:5].permute(0, 2, 3, 1)[positive]
    regression = torch.cat((regression[:, :2].sigmoid(), regression[:, 2:]), dim=1)
    reference = boxes.permute(0, 2, 3, 1)[positive]
    box_loss = 2 * F.smooth_l1_loss(regression, reference)
    if overlap_weight:
        box_loss = box_loss + overlap_weight * distance_iou_loss(regression, reference)
    if prediction.shape[1] == 6:
        return object_loss + box_loss
    cls_loss = F.cross_entropy(prediction[:, 5:].permute(0, 2, 3, 1)[positive],
                               labels[positive], weight=class_weights)
    return object_loss + box_loss + 0.65 * cls_loss


class CompactDetector:
    def __init__(self, weights, device="cpu"):
        weights = Path(weights)
        self.device = torch.device(device)
        checkpoint = torch.load(weights, map_location=self.device, weights_only=True)
        parameters = checkpoint.get("weights", checkpoint)
        self.labels = tuple(checkpoint.get("class_names", LABELS))
        self.input_size = checkpoint.get("input_size")
        architecture = MultiScaleCrownNet if "refine.3.weight" in parameters else CrownNet
        self.model = architecture(len(self.labels)).to(self.device)
        self.model.load_state_dict(parameters)
        self.model.eval()
        with weights.open("rb") as source:
            self.version = weights.name + "@" + file_digest(source, "sha256").hexdigest()[:12]

    @torch.inference_mode()
    def predict(self, image, size):
        net_size = self.input_size or PILOT_SIZES[size]
        value = self.model(to_tensor(image, net_size).unsqueeze(0).to(self.device))
        scores = value[:, :1].sigmoid()
        local_max = F.max_pool2d(scores, 3, stride=1, padding=1)
        locations = ((scores == local_max) & (scores >= 0.05))[0, 0].nonzero()
        if not locations.numel():
            return []
        y, x = locations.T
        selected = value[0, :, y, x].T
        centers_x = x + selected[:, 1].sigmoid()
        centers_y = y + selected[:, 2].sigmoid()
        half_w, half_h = selected[:, 3:5].clamp(-3, 5).exp().T / 2
        width, height = image.size
        scale_x, scale_y = width * STRIDE / net_size, height * STRIDE / net_size
        xyxy = torch.stack(((centers_x - half_w).mul(scale_x).clamp(0, width),
                            (centers_y - half_h).mul(scale_y).clamp(0, height),
                            (centers_x + half_w).mul(scale_x).clamp(0, width),
                            (centers_y + half_h).mul(scale_y).clamp(0, height)), dim=1)
        confidences = selected[:, 0].sigmoid()
        labels = selected[:, 5:].argmax(dim=1)
        return [Detection(tuple(box), self.labels[label], score) for box, label, score in
                zip(xyxy.cpu().tolist(), labels.cpu().tolist(), confidences.cpu().tolist())]
