"""Research-only RGB tile inspection; deliberately separate from geospatial surveys."""
from io import BytesIO
from collections import Counter
import json
from pathlib import Path
from threading import Lock

from PIL import Image, UnidentifiedImageError
from .dataset import ROOT

RESEARCH = ROOT / "results/deep_20261126"
SAMPLE = ROOT / "data/external/mendeley_ganoderma_v2/Ganoderma_UAV_RGB_Dataset_v2/images/test/P6G_T000068_test_positive_context.png"
MAX_BYTES = 5 * 1024 * 1024
MODES = {"balanced", "high_recall"}
SCOPE = ("Model riset Ganoderma V2 pada tile RGB 640 x 640 yang sebanding dengan data latih. "
         "Kandidat inspeksi belum dikonfirmasi di lapangan; tidak ditandai bukan berarti sehat. "
         "Koordinat piksel, bukan GPS; bukan estimasi prevalensi atau hasil sensus.")


class InspectionService:
    def __init__(self, research=RESEARCH):
        self.research=Path(research)
        self.lock=Lock()
        self.models=None

    def available(self):
        needed=("ganoderma_pipeline/summary.json","ganoderma_detector/detector.pth",
                "ganoderma_condition/context_2026.pth","ganoderma_operating_points.json")
        return all((self.research/name).is_file() for name in needed)

    def _load(self):
        from crowntrace.compact import CompactDetector
        from research.ganoderma_condition import PADDINGS, load_classifier
        config=json.loads((self.research/"ganoderma_pipeline/summary.json").read_text())
        chosen=config["selected_classifier"]
        operating=json.loads((self.research/"ganoderma_operating_points.json").read_text())
        if operating["validation"]["balanced"]["threshold"] != config["validation"][config["score_mode"]]["threshold"]:
            raise ValueError("Konfigurasi ambang tidak selaras dengan pipeline yang diuji.")
        self.models=(CompactDetector(self.research/"ganoderma_detector/detector.pth"),
                     load_classifier(self.research/"ganoderma_condition"/chosen["checkpoint"]),
                     config, operating, PADDINGS[chosen["method"]])

    def analyze(self,data,filename,mode):
        if mode not in MODES:
            raise ValueError("Mode inspeksi tidak dikenal.")
        if not 0 < len(data) <= MAX_BYTES:
            raise ValueError("Batas berkas 5 MiB.")
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG","PNG"} or image.size != (640,640):
                    raise ValueError("Gunakan tile RGB JPEG/PNG berukuran tepat 640 x 640.")
                pixels=image.convert("RGB")
        except (UnidentifiedImageError,Image.DecompressionBombError,OSError) as error:
            raise ValueError("Berkas bukan citra JPEG/PNG yang valid.") from error
        if not self.available():
            raise FileNotFoundError("Bobot inspeksi belum tersedia.")
        from research.evaluate_ganoderma_pipeline import screen_image
        with self.lock:
            if self.models is None:
                self._load()
            detector, classifier, config, op, padding=self.models
            cutoff=op["validation"][mode]["threshold"]
            low=op["validation"]["high_recall"]["threshold"]
            high=op["validation"]["balanced"]["threshold"]
            if not 0<=low<=high<=1:raise ValueError("Ambang prioritas tidak valid.")
            predictions=screen_image(pixels,detector,classifier,padding,config["detector_threshold"],config["score_mode"])
        records=[{"id":i,"bbox_px":list(map(float,d.box)),"center_px":list(map(float,d.center)),
                  "detector_score":float(d.score),"survey_label_score":float(p),"review_score":float(score),
                  "review_candidate":score>=cutoff,
                  "review_tier":"high_priority" if score>=high else "needs_review" if score>=low else "not_flagged"}
                 for i,(d,p,score) in enumerate(predictions,1)]
        return {"scope":SCOPE,"coordinate_unit":"pixel","width":640,"height":640,
                "image":filename,"mode":mode,"review_cutoff":cutoff,
                "detector":detector.version,"classifier":config["selected_classifier"]["checkpoint"],
                "detections":len(records),"review_candidates":sum(r["review_candidate"] for r in records),
                "records":records,"review_tiers":dict(Counter(r["review_tier"] for r in records)),
                "triage_thresholds":{"high_priority":high,"needs_review":low}}
