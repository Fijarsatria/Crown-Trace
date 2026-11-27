"""Additional offline model checks, single tile inputs; no HTTP or remote services.
Results verify adapter output/consistency, not accuracy.
"""
from pathlib import Path
import json
from crowntrace.portal_engine import MappingEngine, INVENTORY_TILE_SAMPLE, INSPECTION_SAMPLE

root=Path("results/portal_final_20261126")
out=root/"tile_model_checks"
engine=MappingEngine()
runs=[]
for profile,source in (("tile_inventory",INVENTORY_TILE_SAMPLE),("high_recall",INSPECTION_SAMPLE)):
    destination=out/profile
    if destination.exists():
        raise SystemExit(f"Output already exists: {destination}; use a new directory")
    r=engine.run(profile,source,None,destination,lambda *args:None,example=True)
    m=json.loads((destination/"map.json").read_text(encoding="utf-8"))
    assert len(m["trees"])==r["trees"] and r["coordinate_mode"]=="pixel"
    assert r["area_ha"] is None
    assert "boxes" in r["layers"]
    runs.append({"profile":profile,"trees":r["trees"],"crowns":r["crowns"],"review_candidates":r["review_candidates"],"source":source.name,"kind":"offline adapter check","files":[f["name"] for f in r["files"]]})
    print(profile,r["trees"],r["crowns"],r["review_candidates"],flush=True)
(root/"additional_model_checks.json").write_text(json.dumps({"runs":runs,"success":True,"note":"Local adapter checks, not independent ground truth."},indent=2)+"\n",encoding="utf-8")
