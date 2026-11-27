"""Generate a local, prediction-blind independent annotation workbench."""
from pathlib import Path
import csv,json,hashlib
ROOT=Path(__file__).resolve().parents[1]
cohort=ROOT/"data/palangka_external_review_v1"
with (cohort/"manifest.csv").open(encoding="utf-8-sig") as h:rows=list(csv.DictReader(h))
manifest=[]
for r in rows:
    p=cohort/"images"/r["tile"]
    sha=hashlib.sha256(p.read_bytes()).hexdigest()
    if sha!=r["tile_sha256"]:raise ValueError("Source image hash changed")
    manifest.append({"tile":r["tile"],"sha256":sha,"width":int(r["width"]),"height":int(r["height"])})
html=(ROOT/"research/annotation_workbench_template.html").read_text(encoding="utf-8")
html=html.replace("__LOCKED_MANIFEST__",json.dumps(manifest,separators=(",",":"),ensure_ascii=True))
(cohort/"Review_Annotations.html").write_text(html,encoding="utf-8")
print("Workbench created with",len(manifest),"verified tiles; no labels generated.")
