"""Loopback-only integration acceptance test with real CrownTrace model jobs.
Run against a dedicated --demo store, never a live customer database.
No network calls outside the provided loopback address.
"""
import argparse
import secrets
import csv
from datetime import date, timedelta, datetime, timezone
from http.client import HTTPConnection
from io import BytesIO, StringIO
import json
from pathlib import Path
import time
from urllib.parse import urlencode, urlsplit
from uuid import uuid4
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TILE = ROOT / "data/roboflow_crowns_grouped_v1/val/18aa_jpg.rf.09748715e2783c5e61d6219cea189845.jpg"
INVENTORY_TILE = ROOT / "data/portal_samples/mopad_inventory_tile.png"
GEO = ROOT / "data/demo_mopad_mosaic.tif"
BLOCKS = ROOT / "data/demo_mopad_blocks.geojson"
INSPECTION = ROOT / "data/external/mendeley_ganoderma_v2/Ganoderma_UAV_RGB_Dataset_v2/images/test/P6G_T000068_test_positive_context.png"
QA = {key:True for key in ("source_checked","outputs_checked","scope_checked","review_checked")}
CHECKS = []
METRICS = []


def verified(label, info=None):
    CHECKS.append({"check": label, "status": "pass", **({"detail":info} if info is not None else {})})
    print("PASS:", label, flush=True)


class Client:
    def __init__(self, url):
        u=urlsplit(url)
        assert u.scheme=="http" and u.hostname=="127.0.0.1" and u.port and not u.username
        self.port=u.port;self.origin=f"http://127.0.0.1:{self.port}"
        self.cookie="";self.csrf=None;self.user=None

    def request(self, method, path, body=None, *, raw=None, status=200, csrf=True):
        c=HTTPConnection("127.0.0.1", self.port, timeout=180)
        headers={"Origin":self.origin,"Cookie":self.cookie,"Accept":"application/json"}
        if csrf and self.csrf: headers["X-CSRF-Token"]=self.csrf
        if raw is not None:
            payload=raw;headers["Content-Type"]="application/octet-stream"
        elif body is not None:
            payload=json.dumps(body).encode();headers["Content-Type"]="application/json"
        else: payload=None
        try:
            c.request(method,path,payload,headers)
            resp=c.getresponse();data=resp.read()
            content=resp.getheader("Content-Type","")
            decoded=json.loads(data) if "application/json" in content else data
            assert resp.status==status, f"{method} {path}: expected {status}, got {resp.status}: {str(decoded)[:350]}"
            if resp.getheader("Set-Cookie"):self.cookie=resp.getheader("Set-Cookie").split(";")[0]
            return decoded
        finally:c.close()

    def get(self,path,status=200):
        return self.request("GET",path,status=status)

    def post(self,path,doc,status=200,*,csrf=True):
        return self.request("POST",path,doc,status=status,csrf=csrf)

    def login(self,role):
        d=self.post("/api/demo-login",{"role":role})
        self.csrf=d["csrf"];self.user=d["user"];assert self.user["role"]==role
        return self

    def register(self,name):
        stamp=uuid4().hex[:12]
        d=self.post("/api/register",{"name":name,"email":f"qa-{stamp}@example.invalid","password":secrets.token_urlsafe(24),"organization":"QA lokal","role":"admin"},201)
        self.csrf=d["csrf"];self.user=d["user"];assert self.user["role"]=="customer"
        return self

    def order(self,oid,status=200):
        return self.get(f"/api/orders/{oid}",status)

    def action(self,oid,name,body=None,*,status=200,version=None,csrf=True):
        if version is None:version=self.order(oid)["version"]
        return self.post(f"/api/orders/{oid}/actions/{name}",{**(body or {}),"version":version},status,csrf=csrf)

    def upload(self,oid,kind,path,*,status=201):
        d=self.order(oid)
        query=urlencode({"kind":kind,"version":d["version"],"name":path.name})
        return self.request("POST",f"/api/orders/{oid}/files?{query}",raw=path.read_bytes(),status=status)


def quote(a,oid,day,amount=1250000):
    return a.action(oid,"quote",{"amount":amount,"scope":"Analisis sumber penelitian dan ekspor; transaksi simulasi.","estimated_days":3,"expires_on":day,"payment_instruction":"QA ONLY"})


def create(c,service,plan):
    o=c.post("/api/orders",{"title":f"QA portal {service}/{plan} {uuid4().hex[:6]}","location":"Dataset publik penelitian","service":service,"data_plan":plan,"brief":"Uji alur layanan; bukan pelanggan atau transaksi nyata."},201)
    assert o["status"]=="submitted"
    return o["id"]


def pay(c,a,oid):
    conf=c.action(oid,"submit_payment",{"amount":1250000,"reference":"DEMO-QA-"+uuid4().hex[:8]})
    assert conf["status"]=="payment_review" and not conf["paid"]
    d=a.action(oid,"approve_payment",{"verified":True,"note":"Simulasi verifikasi; tidak melakukan pembayaran nyata."})
    assert d["status"]=="paid" and d["paid"]


def prepare(a,o,oid,day,profile,source,boundary=None,*,client=None):
    client=client or a
    uploaded=client.upload(oid,"source",source)
    assert uploaded["source_file_id"] and not uploaded["data_review"]
    if boundary:client.upload(oid,"boundary",boundary)
    validated=a.action(oid,"validate_data",{"acknowledge":True,"note":"Sumber penelitian disetujui untuk QA; ukuran/CRS/cakupan diperiksa."})
    assert validated["data_review"]["format"]==("geotiff" if profile.startswith("geo_") else "tile")
    assigned=a.action(oid,"schedule",{"assigned_to":o.user["id"],"profile":profile,"schedule_date":day,"note":"Tugas uji lokal."})
    assert assigned["status"]=="scheduled" and assigned["assigned_to"]==o.user["id"]
    return assigned


def run_job(a,o,c,oid,profile,wait):
    start=time.monotonic()
    d=o.action(oid,"start_job");assert d["status"]=="processing";job=d["current_job_id"]
    result_path=f"/api/orders/{oid}/results/{job}"
    c.get(result_path,404)
    print(f"Running {profile}: {job}",flush=True)
    while time.monotonic()-start<wait:
        d=a.order(oid)
        if d["status"]=="quality_review":break
        if d["status"]=="processing_failed":raise RuntimeError(d["job"].get("error") or "Model job failed")
        time.sleep(2)
    else:raise TimeoutError(f"{profile} did not finish in {wait}s")
    assert d["job"]["status"]=="succeeded"
    c.get(result_path,404)
    c.get(result_path+"/files/map.json",404)
    result=o.get(result_path)
    assert result["publication"] is None
    r=result["report"]
    assert r["profile"]==profile and r["example"] and r["trees"]>=0
    files={f["name"]:f["bytes"] for f in r["files"]}
    assert {"map.json","preview.png","summary.json"}<=files.keys()
    mapdata=o.get(result_path+"/files/map.json")
    assert len(mapdata["trees"])==r["trees"]
    with Image.open(BytesIO(o.get(result_path+"/files/preview.png"))) as im:
        im.verify()
    if r["kind"]=="tile" and profile in {"fast","detail"}:
        coco=o.get(result_path+"/files/crowns.coco.json")
        assert len(coco["annotations"])==r["crowns"] and r["crowns"]<=r["trees"]
        assert "mask-layer.png" in files
        assert len(list(csv.DictReader(StringIO(o.get(result_path+"/files/trees.csv").decode("utf-8-sig")))))==r["trees"]
        assert r["area_ha"] is None and r["coordinate_mode"]=="pixel"
    if r["kind"]=="geographic":
        assert r["crs"] and len(r["blocks"]) and r["covered_ha"]>0
        assert sum(b["trees"] for b in r["blocks"])<=r["trees"]
        assert {"trees.geojson","blocks.geojson","grid.geojson","coverage.geojson","crowntrace.gpkg"}<=files.keys()
        if profile=="geo_crowns":assert "crowns.geojson" in files and r["crowns"]>0
    if r["kind"]=="inspection":
        assert r["review_candidates"]<=r["trees"] and r["coordinate_mode"]=="pixel"
        assert "inspection.json" in files
    o.action(oid,"publish",{"checklist":QA,"note":"Operator tidak boleh menerbitkan hasil."},status=409)
    p=a.action(oid,"publish",{"checklist":{"outputs_checked":True},"note":"Checklist tidak lengkap."},status=422)
    published=a.action(oid,"publish",{"checklist":QA,"note":"QA perangkat lunak, memakai data publik penelitian; bukan sensus/diagnosis lapangan."})
    assert published["status"]=="ready"
    customer=c.get(result_path)
    assert customer["publication"]["job_id"]==job and customer["report"]["trees"]==r["trees"]
    assert len(c.get(result_path+"/files/preview.png"))==files["preview.png"]
    metric={"profile":profile,"trees":r["trees"],"crowns":r["crowns"],"review_candidates":r["review_candidates"],"coordinate_mode":r["coordinate_mode"],"coverage_pct":r["coverage_pct"],"blocks":len(r["blocks"]),"files":sorted(files),"wall_seconds":round(time.monotonic()-start,2),"publication_version":customer["publication"]["version"]}
    METRICS.append(metric)
    verified(f"Model {profile}: run nyata, berkas konsisten, draft terkunci, QA & rilis",metric)
    return job


def main(args):
    public=Client(args.url);boot=public.get("/api/bootstrap")
    assert boot["demo"] is True and boot["user"] is None
    day=(date.fromisoformat(boot["today"])+timedelta(days=2)).isoformat()
    for page in ("/","/masuk","/daftar","/contoh","/app","/app/new","/app/orders","/portal.css","/portal.js","/portal-map.js"):
        assert len(public.get(page))>100
    public.get("/api/orders",401)
    sample=public.get("/api/example/report");assert sample["trees"]==1698 and sample["example"]
    verified("Halaman, aset, contoh penelitian dan login gate tersedia")
    c=Client(args.url).login("customer");a=Client(args.url).login("admin");o=Client(args.url).login("operator")
    stranger=Client(args.url).register("Pelanggan uji terpisah")
    assert not stranger.get("/api/orders")["orders"]
    stranger.get("/api/staff",403);o.get("/api/staff",403)
    verified("Registrasi hanya pelanggan; tim/admin menggunakan akun khusus")
    oid=create(c,"segmentation","tile")
    stranger.order(oid,404);o.order(oid,404)
    c.action(oid,"quote",{"amount":1,"scope":"Invalid","estimated_days":3,"expires_on":day},status=409)
    c.action(oid,"start_job",status=409)
    a.action(oid,"quote",{"amount":1,"scope":"Invalid date","estimated_days":3,"expires_on":"2000-01-01"},status=422)
    verified("Pemisahan order dan pembatasan tindakan sesuai tahap/peran")
    d=quote(a,oid,day);old_version=d["version"]
    d=c.action(oid,"decline_quote",{"reason":"Tinjau ulang lingkup."});assert d["status"]=="submitted"
    d=quote(a,oid,day);assert d["quote"]["version"]==2 and len(d["quote_history"])==2
    c.action(oid,"accept_quote",status=409,version=old_version)
    c.action(oid,"accept_quote",status=403,csrf=False)
    d=c.action(oid,"accept_quote");assert d["status"]=="accepted" and not d["paid"]
    verified("Penawaran ditolak, versi ulang, stale version ditolak, CSRF dan deal")
    c.action(oid,"submit_payment",{"amount":123,"reference":"DEMO-SALAH"},status=422)
    proof=c.upload(oid,"payment_proof",TILE)
    proof_id=next(f["id"] for f in proof["files"] if f["kind"]=="payment_proof")
    d=c.action(oid,"submit_payment",{"amount":1250000,"reference":"DEMO-QA-REJECT","proof_file_id":proof_id})
    assert not d["paid"] and d["status"]=="payment_review"
    a.action(oid,"approve_payment",{"verified":False,"note":"Belum diperiksa."},status=422)
    d=a.action(oid,"reject_payment",{"note":"Referensi tidak sesuai."});assert d["status"]=="accepted" and not d["paid"]
    pay(c,a,oid)
    verified("Unggah bukti, nominal cocok, penolakan dan verifikasi manual")
    # Unsupported format, schedule before validation, mismatch and invalidation.
    c.upload(oid,"source",BLOCKS,status=422)
    a.action(oid,"schedule",{"assigned_to":o.user["id"],"profile":"fast","schedule_date":day},status=409)
    d=prepare(a,o,oid,day,"fast",TILE,client=c)
    op=o.order(oid);assert "payment" not in op and "quote" not in op and "amount" not in op
    o.get(f"/api/orders/{oid}/uploads/{proof_id}",404)
    stranger.get(f"/api/orders/{oid}/uploads/{d['source_file_id']}",404)
    c.upload(oid,"source",TILE)
    a.action(oid,"start_job",status=409)
    d=a.action(oid,"validate_data",{"acknowledge":True,"note":"Validasi ulang setelah pergantian versi sumber."})
    a.action(oid,"schedule",{"assigned_to":o.user["id"],"profile":"geo_crowns","schedule_date":day},status=422)
    a.action(oid,"schedule",{"assigned_to":o.user["id"],"profile":"fast","schedule_date":day})
    c.action(oid,"message",{"body":"QA: minta hasil dalam COCO dan CSV."})
    assert any(m["body"].startswith("QA:") for m in o.order(oid)["messages"])
    verified("Validasi sumber, perubahan data membatalkan validasi, penugasan & pesan privat")
    first=run_job(a,o,c,oid,"fast",args.wait)
    c.action(oid,"request_revision",{"note":"QA: ulangi dengan profil Detail."})
    assert c.get(f"/api/orders/{oid}/results/{first}")["publication"]["version"]==1
    a.action(oid,"schedule",{"assigned_to":o.user["id"],"profile":"detail","schedule_date":day})
    second=run_job(a,o,c,oid,"detail",args.wait)
    d=c.order(oid);assert second!=first and len(d["publications"])==2
    assert c.get(f"/api/orders/{oid}/results/{first}")["publication"]["version"]==1
    stranger.get(f"/api/orders/{oid}/results/{second}",404)
    stranger.get(f"/api/orders/{oid}/results/{second}/files/trees.csv",404)
    d=c.action(oid,"complete");assert d["status"]=="completed"
    c.get(f"/api/orders/{oid}/results/{second}")
    verified("Revisi, dua versi immutable, hasil privat dan penerimaan pelanggan")
    # Check other service families with real jobs, if enabled.
    if args.geo:
        geo=create(c,"segmentation","geotiff")
        quote(a,geo,day);c.action(geo,"accept_quote");pay(c,a,geo)
        prepare(a,o,geo,day,"geo_crowns",GEO,BLOCKS)
        run_job(a,o,c,geo,"geo_crowns",args.wait)
    if args.inspection:
        insp=create(c,"inspection","tile")
        quote(a,insp,day);c.action(insp,"accept_quote");pay(c,a,insp)
        prepare(a,o,insp,day,"balanced",INSPECTION)
        run_job(a,o,c,insp,"balanced",args.wait)

    if args.all_profiles:
        for service,plan,profile,source,boundary in (
            ("inventory","tile","tile_inventory",INVENTORY_TILE,None),
            ("inventory","geotiff","geo_inventory",GEO,BLOCKS),
            ("inspection","tile","high_recall",INSPECTION,None),
        ):
            extra=create(c,service,plan)
            quote(a,extra,day);c.action(extra,"accept_quote");pay(c,a,extra)
            prepare(a,o,extra,day,profile,source,boundary)
            run_job(a,o,c,extra,profile,args.wait)
    cancelled=create(c,"inventory","tile")
    d=c.action(cancelled,"cancel",{"reason":"QA pembatalan sebelum pembayaran."});assert d["status"]=="cancelled"
    c.action(cancelled,"accept_quote",status=409)
    c.post("/api/logout",{});c.get("/api/orders",401)
    verified("Pembatalan pre-payment dan logout")
    print("All selected acceptance checks passed.",flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8791")
    p.add_argument("--geo",action="store_true")
    p.add_argument("--inspection",action="store_true")
    p.add_argument("--all-profiles",action="store_true")
    p.add_argument("--wait",type=int,default=360)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    note={"kind":"Software integration QA on demo data; not a model accuracy benchmark.","started":datetime.now(timezone.utc).isoformat(),"checks":CHECKS,"model_runs":METRICS,"success":False}
    try:
        main(args)
        note["success"]=True
    except Exception as error:
        note["error"]=str(error)
        raise
    finally:
        note["finished"]=datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(note,ensure_ascii=False,indent=2),encoding="utf-8")
