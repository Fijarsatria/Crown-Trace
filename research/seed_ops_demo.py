"""Optional, explicitly synthetic monitoring/QC demo. Never opens operational data.

Precomputed results come from an actual inference over the demonstration
GeoTIFF; two copies intentionally share geometry and simulated review labels to
exercise the monitoring interface. No field observation is claimed.
"""
import argparse,json
from pathlib import Path
from datetime import timedelta
from hashlib import file_digest
from crowntrace.dataset import ROOT
from crowntrace.portal_store import PortalStore, QA_KEYS, today
from crowntrace.portal_server import exclusive_workspace
from crowntrace.portal_engine import GEO_SAMPLE, clone_report
from crowntrace.portal_quality import edit
from crowntrace.portal_operations import compare

EVIDENCE=ROOT/"results/next_20261126/geo_crowns_actual"
PREFIX="Kontrol QA monitoring"

def digest(p):
    with p.open("rb") as f:return file_digest(f,"sha256").hexdigest()

class CachedFixture:
    def available(self):return {"geo_crowns":True}
    def validate(self,source,service,data_plan,boundary):
        if digest(source)!=digest(GEO_SAMPLE):raise ValueError("Only fixed demo source permitted.")
        r=json.loads((EVIDENCE/"report.json").read_text(encoding="utf-8"))
        return {"format":"geotiff","coordinate_mode":"geographic","width":r["width"],"height":r["height"],"crs":r["crs"],"geotransform":r["geotransform"],
                "note":"Precomputed actual demo model output, not field observation."}

def prepare(root):
    store=PortalStore(root,demo=True)
    with exclusive_workspace(store.root):
        store.seed_demo()
        actors={}
        for role in ("customer","admin","operator"):
            key,_,actors[role]=store.demo_sign_in(role);store.sign_out(key)
        a,c,o=actors["admin"],actors["customer"],actors["operator"]
        current=[p for p in store.list_orders(a) if p["title"].startswith(PREFIX)]
        if current:
            if len(current)!=3:raise ValueError("Incomplete seed exists. Use a new demo workspace.")
            print("Demo lanjutan sudah tersedia; tidak menggandakan pesanan.");return
        cache=CachedFixture()
        def action(actor,oid,name,**body):
            return store.action(actor,oid,name,{"version":store.detail(actor,oid)["version"],**body},cache)
        def create(title,condition):
            order=store.create_order(c,{"title":PREFIX+" — "+title,"location":"SIMULASI / koordinat demo","service":"segmentation","data_plan":"geotiff","brief":"Kontrol software dari dua salinan geometri demo; bukan dua misi lapangan."})
            oid=order["id"]
            action(a,oid,"quote",amount=125000,scope="Demo only — hasil precomputed",estimated_days=2,expires_on=(today()+timedelta(days=3)).isoformat())
            action(c,oid,"accept_quote");action(c,oid,"submit_payment",amount=125000,reference="SIMULASI-ONLY")
            action(a,oid,"approve_payment",verified=True,note="Simulasi, tidak ada uang berpindah.")
            action(a,oid,"use_sample");action(a,oid,"validate_data",acknowledge=True,note="Contoh QA georeferensi sintetis.")
            action(a,oid,"schedule",assigned_to=o["id"],profile="geo_crowns",schedule_date=today().isoformat())
            _,jid=action(o,oid,"start_job");store.job_begin(jid)
            clone_report(EVIDENCE,store.results/jid,"geo_crowns");store.job_end(jid)
            if condition:
                key=json.loads((store.results/jid/"map.json").read_text())["trees"][0]["id"]
                edit(store,o,oid,jid,{"revision":0,"action":"review","tree_id":key,"visual_condition":condition,"note":"Label fixture sengaja berbeda untuk QA monitoring, bukan hasil observasi lapangan."})
                action(a,oid,"publish",checklist={k:True for k in QA_KEYS},note="Rilis kontrol QA simulasi dari geometri identik, label fixture.")
            return oid,jid
        before,j0=create("survei awal (fixture)","normal_visual")
        after,j1=create("survei terbaru (fixture)","stress_visual")
        _,_=create("draft QC interaktif",None)
        now=today();comp=compare(store,c,{"before_order":before,"before_job":j0,"after_order":after,"after_job":j1,
            "before_date":(now-timedelta(days=60)).isoformat(),"after_date":(now-timedelta(days=1)).isoformat(),"tolerance_m":.1,"same_area":True})
        assert comp["summary"]["matched"]==440 and comp["summary"]["observed_changes"]==1
        print(json.dumps({"mode":"DEMO only","orders_added":3,"monitoring_control":comp["summary"]},indent=2))
if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--store",type=Path,default=ROOT/"portal_workspace"/"demo_ops")
    a=p.parse_args();prepare(a.store)
