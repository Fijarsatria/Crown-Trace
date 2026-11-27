"""Prepare a photo staging list from the previously completed Palangka audits.
This does not re-read the raw imagery or re-run feature matching.
"""
import argparse,csv,json,hashlib
from pathlib import Path
from research.drone_intake import components

ROOT=Path(__file__).resolve().parents[1]
def run(output):
    base=ROOT/"results/mapping_20261126"
    group_path=base/"palangka_duplicate_audit.json";overlap_path=base/"palangka_pairs_unique.json"
    d=json.loads(group_path.read_text(encoding="utf-8-sig"));o=json.loads(overlap_path.read_text(encoding="utf-8-sig"))
    groups=d["view_groups"];reps=set(o["photos"]);edges=[(e["a"],e["b"]) for e in o["edges"]]
    assert len(groups)==30 and sum(map(len,groups))==150 and all(len(reps.intersection(g))==1 for g in groups)
    comps=components(reps,edges)
    assigned={name:i+1 for i,g in enumerate(comps) for name in g}
    output.mkdir(parents=True,exist_ok=False)
    with (output/"staging_manifest.csv").open("w",encoding="utf-8-sig",newline="") as h:
        w=csv.DictWriter(h,fieldnames=["view_group","selected_source","other_versions","overlap_component","component_size","status"])
        w.writeheader()
        for i,g in enumerate(groups,1):
            name=next(iter(reps.intersection(g)));j=assigned[name]
            w.writerow({"view_group":i,"selected_source":name,"other_versions":";".join(x for x in g if x!=name),
                        "overlap_component":j,"component_size":len(comps[j-1]),"status":"relative_only"})
    summary={"scope":"Reuse of completed prior duplicate and overlap audits; no fresh pixel quality/EXIF measurement.",
             "photos_in_prior_audit":150,"unique_view_groups":30,"selected_representatives":len(reps),
             "verified_overlap_edges":len(edges),"overlap_component_sizes":[len(g) for g in comps],
             "largest_connected_component":len(comps[0]),"isolated_views":sum(len(g)==1 for g in comps),
             "geographic_status":"No verified GCP/GPS/orthorectified full-area imagery; not ready for site-wide geographic inventory.",
             "source_hashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (group_path,overlap_path)}}
    (output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("output",type=Path);run(p.parse_args().output)
