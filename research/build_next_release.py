"""Create a verified source/data update without any private runtime workspace."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from hashlib import sha256
import argparse,json,re
ROOT=Path(__file__).resolve().parents[1]
def build(output):
    items={}
    def add(rel):
        p=(ROOT/rel).resolve()
        if not p.is_relative_to(ROOT) or not p.is_file():raise ValueError(f"Missing/outside root: {rel}")
        name=p.relative_to(ROOT).as_posix()
        if "portal_workspace" in p.relative_to(ROOT).parts or p.suffix==".sqlite3":raise ValueError("Private data not allowed")
        items[name]=p
    for f in ("README.md","README_PAKET_LANJUTAN.md","MULAI_DI_SINI.md","DOKUMENTASI_PROYEK.md","Buka_WebGIS.cmd",
              "Buka_Demo_Lanjutan.cmd","Buka_Anotasi_Palangka.cmd","data/README_DATASET.md","results/INDEX_HASIL.md","research/INDEX_RISET.md",
              "research/PANDUAN_PORTAL_LAYANAN.md","research/STATUS_RISET.md","research/ACQUISITION_NEXT.md"):
        add(f)
    for name in ("portal_quality.py","portal_operations.py","portal_server.py","portal_store.py","portal_engine.py","inspection.py","raster_worker.py"):
        add("crowntrace/"+name)
    for name in ("portal-ops.js","portal.js","portal-map.js","portal.css","index.html"):
        add("web/portal/"+name)
    for name in ("train_prompt_contour.py","evaluate_prompt_contours.py","offline_review_diagnostics.py","drone_intake.py","prepare_intake_from_audits.py",
                 "build_annotation_workbench.py","annotation_workbench_template.html","review_blind_packs.py","portal_backup.py","seed_ops_demo.py","build_next_release.py"):
        add("research/"+name)
    for name in ("test_portal_operations.py","test_next_research_tools.py","test_portal_backup.py"):
        add("tests/"+name)
    for name in ("main.tex","results.tex","references.tex","tile_review.png","README.md","CrownTrace_IEEE_Next_20261126.pdf"):
        add("research/IEEE_CrownTrace_2026/"+name)
    add("research/CrownTrace_IEEE_Overleaf_Next_20261126.zip")
    cohort=ROOT/"data/palangka_external_review_v1"
    for name in ("README.md","Review_Annotations.html","manifest.csv","scope.json","contact_sheet.png"):add(cohort/name)
    for sub in ("images","labelme"):
        for p in (cohort/sub).glob("*"):
            if p.is_file():add(p)
    out=ROOT/"results/next_20261126"
    for name in ("EVALUASI_PENGEMBANGAN.md","CLEANUP_LOG.md","affine_qa.json","blind_cohort_status.json","field_observation_template.csv"):add(out/name)
    for sub in ("offline_diagnostics","intake_existing_audits","geo_crowns_actual","inspection_tiers_actual","qa"):
        for p in (out/sub).glob("*"):
            if p.is_file():add(p)
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest={}
    with ZipFile(output,"x",ZIP_DEFLATED,compresslevel=6) as z:
        for name,p in sorted(items.items()):
            data=p.read_bytes();manifest[name]={"bytes":len(data),"sha256":sha256(data).hexdigest()}
            z.writestr(name,data)
        z.writestr("MANIFEST_UPGRADE.json",json.dumps({"kind":"CrownTrace Next update for existing project","files":manifest},indent=2))
    with ZipFile(output) as z:
        assert z.testzip() is None
        assert len(set(z.namelist()))==len(z.namelist())
        for name,meta in manifest.items():assert sha256(z.read(name)).hexdigest()==meta["sha256"]
    print(json.dumps({"files":len(items),"bytes":output.stat().st_size,"manifest_verified":True},indent=2))
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("output",type=Path);build(p.parse_args().output)
