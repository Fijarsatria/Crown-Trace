"""Prepare an explicitly sourced 640 px inventory demonstration from MOPAD.
This crop is not a new independent evaluation dataset.
"""
from pathlib import Path
from hashlib import sha256
from PIL import Image
import json

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/"data/siit_detection_v1/images/test/52000_20000_1020_3919.jpg"
target=ROOT/"data/portal_samples"
target.mkdir(parents=True,exist_ok=True)
bounds=(192,192,832,832)
with Image.open(source) as photo:
    assert photo.size==(1024,1024)
    photo.convert("RGB").crop(bounds).save(target/"mopad_inventory_tile.png")
manifest={
    "purpose":"Portal inventory demonstration, not an independent accuracy benchmark",
    "source":source.relative_to(ROOT).as_posix(),
    "source_sha256":sha256(source.read_bytes()).hexdigest(),
    "crop_xyxy":list(bounds),
    "size":[640,640],
    "coordinate_mode":"pixel",
    "annotation_source":"SIIT-DL corrected boxes for MOPAD Site 2",
    "output_sha256":sha256((target/"mopad_inventory_tile.png").read_bytes()).hexdigest(),
    "dataset_url":"https://github.com/rs-dl/MOPAD",
}
(target/"inventory_provenance.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
print("Inventory demonstration crop prepared:",target)
