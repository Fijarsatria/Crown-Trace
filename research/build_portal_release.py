"""Build a public-demo/runtime release without workspace/customer databases."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from hashlib import sha256
import json,argparse
from crowntrace.portal_engine import (PUBLIC_SAMPLE,TILE_SAMPLE,INVENTORY_TILE_SAMPLE,
    INSPECTION_SAMPLE,GEO_SAMPLE,GEO_BOUNDARY,DETECT_WEIGHTS,SEGMENT_WEIGHTS,FILES)
from crowntrace.dataset import ROOT,DATA,manifest
ROOT=ROOT.resolve()
p=argparse.ArgumentParser(description=__doc__)
p.add_argument("--output",type=Path,required=True)
args=p.parse_args()
out=args.output.resolve()
out.parent.mkdir(parents=True,exist_ok=True)
items=set()
def add(path,required=True):
    q=(ROOT/path).resolve()
    if not q.is_relative_to(ROOT): raise ValueError(f"Outside package root: {q}")
    if q.is_file():items.add(q)
    elif required:raise FileNotFoundError(q)
def directory(path,pattern="*",required=True):
    folder=ROOT/path
    if not folder.is_dir():
        if required:raise FileNotFoundError(folder)
        return
    for f in folder.rglob(pattern):
        if f.is_file() and "__pycache__" not in f.parts and not any(x.startswith(".") for x in f.relative_to(ROOT).parts):
            add(f)
for name in ("README.md","README_PAKET_PORTAL.md","MULAI_DI_SINI.md","requirements.txt","requirements-optional.txt",
    "requirements-baselines.txt","Buka_WebGIS.cmd","Buka_Riset_WebGIS.cmd","Buka_Operasional_WebGIS.cmd",
    "Atur_Admin_CrownTrace.cmd","Pasang_CrownTrace.cmd"):
    add(name)
directory("crowntrace","*.py");directory("research","*.py");directory("web");directory("design-system/crowntrace-services")
for f in (ROOT/"tests").glob("*.py"):add(f)
for f in (ROOT/"tests").glob("*.cjs"):add(f)
for fn in ("STATUS_RISET.md","PANDUAN_PORTAL_LAYANAN.md","FINALISASI_LAYANAN_WEBGIS.md",
           "PROTOKOL_VALIDASI_INVENTARIS.md","DATASET_CANDIDATES_20261126.md","RENCANA_EKSPERIMEN.md",
           "PROTOKOL_SEGMENTASI.md","ROADMAP_TIM.md","DATASET_SEGMENTASI.md","RENCANA_ITERASI_20261126.md"):
    add("research/"+fn,required=False)
for fn in ("main.tex","results.tex","references.tex","tile_review.png","README.md","CrownTrace_IEEE_Portal_20261126.pdf"):
    add("research/IEEE_CrownTrace_2026/"+fn)
add("research/CrownTrace_IEEE_Overleaf_Portal_20261126.zip")
for fn in ("data/README_DATASET.md","data/demo_geo.tif","data/pilot_manifest.json","data/portal_samples/inventory_provenance.json",
           "data/siit_detection_v1/images/test/52000_20000_1020_3919.jpg"):
    add(fn)
sample=next(r for r in manifest()["samples"] if r["split"]=="test")
add(DATA/"pilot_images"/sample["file_name"],required=False)
for fn in (TILE_SAMPLE,INVENTORY_TILE_SAMPLE,INSPECTION_SAMPLE,GEO_SAMPLE,GEO_BOUNDARY,DETECT_WEIGHTS,SEGMENT_WEIGHTS):
    add(fn)
for f in FILES:
    add(PUBLIC_SAMPLE/f,required=False)
for profile in ("selected_detail","selected_fast"):
    for name in ("training.json","detector.pth","mask.pth"):add("results/iteration_20261126/"+profile+"/"+name)
for name in ("ganoderma_pipeline/summary.json","ganoderma_detector/detector.pth",
             "ganoderma_condition/context_2026.pth","ganoderma_operating_points.json"):
    add("results/deep_20261126/"+name)
for name in ("results/expanded_benchmark/summary.json","results/v2_benchmark/summary.json",
             "results/multiscale_train/best.pth","results/contour_20261125/selected/training.json",
             "results/contour_20261125/selected/detector.pth","results/contour_20261125/selected/mask.pth",
             "results/iteration_20261126/Evaluasi_Iterasi_CrownTrace.md",
             "results/deep_20261126/Evaluasi_Penguatan_CrownTrace.md",
             "results/layers_20261126/Evaluasi_Layer_dan_Transfer_CrownTrace.md"):
    add(name,required=False)
qa="results/portal_final_20261126/"
for name in ("integration.json","store_unit.log","additional_model_checks.json","startup.json","EVALUASI_FINALISASI.md"):
    add(qa+name)
directory(qa+"run_evidence")
# Full integration artifacts are included. Adapter-only checks retain exact
# numeric/geometry exports but omit duplicate previews (their source inputs
# are already in the archive and can regenerate the previews).
for sub in ("tile_model_checks","geo_inventory_check"):
    for f in (ROOT/(qa+sub)).rglob("*"):
        if f.is_file() and f.suffix.lower() not in {".png",".jpg",".webp"}:add(f)
assert not any("portal_workspace" in f.parts or f.suffix in {".sqlite3",".part"} for f in items)
manifest_files={}
with ZipFile(out,"w",ZIP_DEFLATED,compresslevel=6) as z:
    for f in sorted(items):
        name=f.relative_to(ROOT).as_posix()
        content=f.read_bytes()
        manifest_files[name]={"bytes":len(content),"sha256":sha256(content).hexdigest()}
        z.writestr(name,content)
    z.writestr("MANIFEST_PORTAL_RELEASE.json",json.dumps({"kind":"CrownTrace runtime/demo","files":manifest_files},indent=2))
with ZipFile(out) as z:
    assert z.testzip() is None
    assert len(set(z.namelist()))==len(z.namelist())
print("Archive:",out,"files:",len(items),"bytes:",out.stat().st_size)
