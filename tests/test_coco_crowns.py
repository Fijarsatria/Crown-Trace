import json
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import torch

from crowntrace.benchmark import ranked_ap
from crowntrace.compare_crown_models import precision_recall_ap
from crowntrace.compact import distance_iou_loss
from crowntrace.evaluate_coco_crowns import LocalMask, calibrate, mask_iou
from crowntrace.inference import Detection, MirrorEnsemble
from crowntrace.prepare_roboflow_crowns import family, join_components
from crowntrace.segment import CrownMaskNet, CrownMaskRefiner, single_outline
from crowntrace.train_coco_crowns import turn_batch


def test_native_mask_offsets_affect_iou():
    pixels = np.ones((2, 2), dtype=bool)
    first = LocalMask.resized((0, 0, 2, 2), pixels)
    next_to_it = LocalMask.resized((2, 0, 4, 2), pixels)
    overlapping = LocalMask.resized((1, 0, 3, 2), pixels)
    assert mask_iou(first, first) == 1
    assert mask_iou(first, next_to_it) == 0
    assert mask_iou(first, overlapping) == 1 / 3


def test_ranked_ap_counts_a_duplicate_only_once():
    overlaps = {"one": np.array([[1., 0.], [1., 0.], [0., 1.]])}
    values = ranked_ap(overlaps, {"one": [.9, .8, .7]}, [.5, .75])
    assert values[0] == values[1]
    assert 0.82 < values[0] < 0.85


def test_validation_count_metrics_are_serializable():
    proposals = [SimpleNamespace(score=.8), SimpleNamespace(score=.2)]
    tile = SimpleNamespace(regions=[object()])
    threshold, grid = calibrate([(tile, None, proposals, 0.)])
    assert threshold == .8 and grid[threshold]["mae"] == 0
    assert json.loads(json.dumps(grid))["0.8"]["mae"] == 0


def test_family_groups_retain_transitive_similarity():
    assert family("14aa.jpg") == family("14aaa.JPG")
    assert family("2-kiri-atas.jpg") == family("2-kanan-bawah.jpg")
    assert join_components(4, [(0, 1), (2, 1)]) == [[0, 1, 2], [3]]


def test_mirror_boxes_map_back_to_native_coordinates():
    class BrightRectangleDetector:
        version = "fixture"
        labels = ("palm_crown",)

        def predict(self, image, size):
            return [Detection(image.getbbox(), self.labels[0], .80)]

    image = Image.new("L", (31, 23))
    ImageDraw.Draw(image).rectangle((3, 5, 9, 11), fill=255)
    result, = MirrorEnsemble(BrightRectangleDetector()).predict(image, 1024)
    np.testing.assert_allclose(result.box, (3, 5, 10, 12))
    assert result.label == "palm_crown" and result.score == .80


def test_refinement_starts_at_the_loaded_compact_prediction():
    base = CrownMaskNet().eval()
    refined = CrownMaskRefiner().eval()
    refined.load_state_dict(base.state_dict(), strict=False)
    pixels = torch.rand(1, 5, 64, 64)
    with torch.inference_mode():
        torch.testing.assert_close(refined(pixels), base(pixels))


def test_rotated_target_preserves_subpixel_center_and_swaps_box_dimensions():
    images = torch.zeros(1, 3, 4, 4)
    heat = torch.zeros(1, 4, 4)
    boxes = torch.zeros(1, 4, 4, 4)
    labels = torch.full((1, 4, 4), -1, dtype=torch.long)
    heat[0, 0, 1] = 1
    labels[0, 0, 1] = 0
    boxes[0, :, 0, 1] = torch.tensor([.4, .3, .7, .9])
    _, turned_heat, turned_boxes, turned_labels = turn_batch(images, heat, boxes, labels, 1, True)
    assert turned_heat[0, 2, 3] == 1 and turned_labels[0, 2, 3] == 0
    torch.testing.assert_close(turned_boxes[0, :, 2, 3], torch.tensor([.7, .6, .9, .7]))


def test_distance_iou_penalizes_misaligned_centers():
    reference = torch.tensor([[.5, .5, 0., 0.]])
    shifted = reference + torch.tensor([[.4, 0., 0., 0.]])
    assert distance_iou_loss(reference, reference) == 0
    assert distance_iou_loss(shifted, reference) > .5


def test_solid_outline_fills_holes_and_removes_other_components():
    raw = np.zeros((11, 11), dtype=bool)
    raw[2:9, 2:9] = True
    raw[4:6, 4:6] = False
    raw[0, 0] = True
    cleaned = single_outline(raw)
    assert cleaned[2:9, 2:9].all() and cleaned.sum() == 49


def test_saved_hit_ap_agrees_with_matches():
    overlaps = {"one": np.array([[1., 0.], [1., 0.], [0., 1.]])}
    scores = [.9, .8, .7]
    reference = ranked_ap(overlaps, {"one": scores}, [.5])
    result = precision_recall_ap(np.array(scores), np.array([[1], [0], [1]]),
                                 np.zeros(3, dtype=int), np.array([2.]), np.array([1]))
    np.testing.assert_allclose(result, reference)
