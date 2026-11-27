"""Local service operations: survey comparison, notification inbox, billing summary."""
from datetime import date
import html
import json
import math
import re
from .portal_store import Problem, now, today, calendar
from .portal_quality import _context

SCHEMA="""
CREATE TABLE IF NOT EXISTS notification_reads(
 user_id TEXT PRIMARY KEY REFERENCES users(id), last_event INTEGER NOT NULL DEFAULT 0);
"""
ID=re.compile(r"^[a-f0-9]{32}$")


def inbox(store,actor,mark=False):
    role=actor["role"]
    where,args={"customer":("o.customer_id=? AND t.visibility='client'",[actor["id"]]),
                "operator":("o.assigned_to=? AND t.action NOT IN ('quote','accept_quote','decline_quote','submit_payment','approve_payment','reject_payment','upload_payment_proof')",[actor["id"]]),
                "admin":("1=1",[])}[role]
    with store.tx() if mark else store.connect() as db:
        last=db.execute("SELECT last_event FROM notification_reads WHERE user_id=?",(actor["id"],)).fetchone()
        cursor=last["last_event"] if last else 0
        rows=[dict(r) for r in db.execute(f"""SELECT t.id,t.order_id,t.label,t.created_at,o.code,o.title
                FROM timeline t JOIN orders o ON t.order_id=o.id WHERE {where} ORDER BY t.id DESC LIMIT 100""",args)]
        if mark:
            cursor=max([cursor]+[r["id"] for r in rows])
            db.execute("INSERT INTO notification_reads VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET last_event=MAX(last_event,excluded.last_event)",(actor["id"],cursor))
        unread=db.execute(f"SELECT COUNT(*) FROM timeline t JOIN orders o ON t.order_id=o.id WHERE ({where}) AND t.id>?",args+[cursor]).fetchone()[0]
        return {"unread":unread,"items":[{**r,"unread":r["id"]>cursor} for r in rows]}


def candidates(store,actor):
    where,args={"customer":("o.customer_id=?",(actor["id"],)),"operator":("o.assigned_to=?",(actor["id"],)),"admin":("1=1",())}[actor["role"]]
    out=[]
    with store.connect() as db:
        for r in db.execute(f"""SELECT o.id AS order_id,o.code,o.title,o.customer_id,o.location,p.job_id,p.version,p.released_at
               FROM publications p JOIN orders o ON o.id=p.order_id WHERE {where} ORDER BY p.released_at DESC,p.version DESC LIMIT 500""",args):
            folder=store.results/r["job_id"]
            try:rep=json.loads((folder/"report.json").read_text(encoding="utf-8"))
            except (FileNotFoundError,ValueError):continue
            if rep.get("coordinate_mode")=="geographic":
                out.append({k:r[k] for k in ("order_id","job_id","code","title","location","version","released_at")}|{"example":bool(rep.get("example"))})
    return {"surveys":out}


def compare(store,actor,body):
    if not isinstance(body,dict):raise Problem(422,"Parameter monitoring tidak valid.")
    if body.get("same_area") is not True:raise Problem(422,"Konfirmasi area, cakupan, akurasi posisi dan tanggal dari survei yang dibandingkan.")
    keys=[body.get(k) for k in ("before_order","before_job","after_order","after_job")]
    if not all(isinstance(k,str) and ID.fullmatch(k) for k in keys) or keys[1]==keys[3]:
        raise Problem(422,"Pilih dua hasil survei yang berbeda.")
    first=calendar(body.get("before_date"),"Tanggal survei awal")
    second=calendar(body.get("after_date"),"Tanggal survei terbaru")
    if not first<second<=today().isoformat():raise Problem(422,"Tanggal survei harus berurutan dan bukan masa depan.")
    try:tol=float(body.get("tolerance_m"))
    except (TypeError,ValueError):raise Problem(422,"Toleransi posisi tidak valid.")
    if not math.isfinite(tol) or not .1<=tol<=20:raise Problem(422,"Toleransi posisi harus 0,1–20 m dan ditetapkan dari ketelitian survei.")
    with store.connect() as db:
        before,rep0,_,info0,rows0=_context(store,db,actor,keys[0],keys[1])
        after,rep1,_,info1,rows1=_context(store,db,actor,keys[2],keys[3])
        if before["customer_id"]!=after["customer_id"]:raise Problem(422,"Survei harus berasal dari pelanggan yang sama.")
        if not info0["released"] or not info1["released"]:raise Problem(409,"Monitoring hanya membandingkan hasil yang telah dirilis.")
        if rep0["coordinate_mode"]!="geographic" or rep1["coordinate_mode"]!="geographic":raise Problem(422,"Monitoring memerlukan koordinat geografis.")
        example=info0["example"]
        if example!=info1["example"]:raise Problem(422,"Survei demo dan lapangan tidak boleh dicampur.")
        hashrows=[db.execute("SELECT f.sha256 FROM jobs j JOIN files f ON j.source_file_id=f.id WHERE j.id=?",(job,)).fetchone() for job in (keys[1],keys[3])]
        if not example and (not all(hashrows) or hashrows[0][0]==hashrows[1][0] or info0["source_sha256"]==info1["source_sha256"]):
            raise Problem(422,"Survei lapangan memerlukan dua sumber citra independen, bukan dua inferensi dari citra yang sama.")
    def eligible(rows):
        result=[]
        for r in rows:
            if r["excluded"] or not r.get("coordinates"):continue
            p=r["coordinates"]
            if len(p)!=2 or not all(type(z) in (float,int) and math.isfinite(z) for z in p) or not(-180<=p[0]<=180 and -85<=p[1]<=85):continue
            result.append({**r,"lon":p[0],"lat":p[1]})
        return result
    left,right=eligible(rows0),eligible(rows1)
    if not left or not right:raise Problem(422,"Kedua survei memerlukan titik geografis yang valid.")
    if len(left)+len(right)>40000:raise Problem(422,"Bandingkan per blok; batas pilot 40.000 titik gabungan.")
    # Point extent overlap is only a sanity check, not proof of equal coverage.
    def extent(rows):return (min(r["lon"] for r in rows),min(r["lat"] for r in rows),max(r["lon"] for r in rows),max(r["lat"] for r in rows))
    a,b=extent(left),extent(right)
    margin=tol/110000
    if a[2]+margin<b[0] or b[2]+margin<a[0] or a[3]+margin<b[1] or b[3]+margin<a[1]:
        raise Problem(422,"Cakupan titik tidak bertumpang tindih; periksa area/CRS.")
    from research.evaluate_inventory import match
    try:pairs=match(left,right,tol)
    except ValueError as err:raise Problem(422,str(err)) from err
    used0={i for i,j,d in pairs};used1={j for i,j,d in pairs}
    rows=[];transitions={}
    def label(r):return r.get("visual_condition") if r.get("review_state")=="reviewed" else None
    for i,j,d in pairs:
        l,r=left[i],right[j]
        c0,c1=label(l),label(r)
        comparable=bool(c0 and c1 and "uncertain" not in (c0,c1) and l.get("review_source")==r.get("review_source"))
        change="changed" if comparable and c0!=c1 else "unchanged" if comparable else "unverified"
        if comparable:
            k=c0+" → "+c1;transitions[k]=transitions.get(k,0)+1
        rows.append({"before_id":l["id"],"after_id":r["id"],"distance_m":round(d,3),"before_condition":c0,"after_condition":c1,"status":change,
                     "before_coords":l["coordinates"],"after_coords":r["coordinates"]})
    for i,r in enumerate(left):
        if i not in used0:rows.append({"before_id":r["id"],"after_id":None,"status":"unmatched_before","before_coords":r["coordinates"]})
    for j,r in enumerate(right):
        if j not in used1:rows.append({"before_id":None,"after_id":r["id"],"status":"unmatched_after","after_coords":r["coordinates"]})
    summary={"before_effective":info0["summary"]["effective_count"],"after_effective":info1["summary"]["effective_count"],
             "before_geographic":len(left),"after_geographic":len(right),"matched":len(pairs),
             "unmatched_before":len(left)-len(pairs),"unmatched_after":len(right)-len(pairs),
             "comparable_reviewed":sum(transitions.values()),"observed_changes":sum(r["status"]=="changed" for r in rows),
             "missing_coordinates_before":info0["summary"]["effective_count"]-len(left),
             "missing_coordinates_after":info1["summary"]["effective_count"]-len(right)}
    return {"example":example,"before":{"order_id":keys[0],"job_id":keys[1],"survey_date":first,"quality_revision":info0["revision"]},
            "after":{"order_id":keys[2],"job_id":keys[3],"survey_date":second,"quality_revision":info1["revision"]},
            "tolerance_m":tol,"summary":summary,"transitions":transitions,"rows":rows,
            "note":("Koordinat demonstrasi. " if example else "")+"Pencocokan posisi satu-ke-satu bersifat kandidat ID antar-survei. Titik tidak cocok bukan bukti pohon baru/mati. Perubahan kategori hanya dari review dengan jenis observasi sebanding, bukan diagnosis. Periksa cakupan, waktu dan ketelitian posisi sebelum tindakan."}


def billing(store,actor,oid):
    if actor["role"]=="operator":raise Problem(403,"Tagihan hanya tersedia untuk pelanggan dan admin.")
    with store.connect() as db:
        o=store._role_order(db,actor,oid)
        q=db.execute("SELECT * FROM quotes WHERE id=? AND decision='accepted'",(o["quote_id"],)).fetchone()
        if not q:raise Problem(409,"Penawaran belum disetujui.")
        payment=db.execute("SELECT * FROM payments WHERE id=?",(o["payment_id"],)).fetchone()
        cust=db.execute("SELECT name,organization FROM users WHERE id=?",(o["customer_id"],)).fetchone()
        settled=bool(o["paid_at"] and payment and payment["status"]=="approved")
        return {"document_id":o["code"]+"-Q"+str(q["version"]),"title":o["title"],"customer":cust["name"],"organization":cust["organization"],
                "scope":q["scope"],"amount":q["amount"],"status":"Terverifikasi manual" if settled else "Belum terverifikasi",
                "quote_accepted_at":q["decided_at"],"paid_at":o["paid_at"] if settled else None,
                "reference":payment["reference"] if settled else None,"demo":store.demo or bool(o["example"]),
                "note":"Ringkasan tagihan dan status internal; bukan faktur pajak atau bukti transfer bank."}


def billing_html(info):
    def e(v):return html.escape(str(v if v is not None else "—"))
    def row(label,v):return f"<tr><th>{e(label)}</th><td>{e(v)}</td></tr>"
    fields=[("ID dokumen",info["document_id"]),("Proyek",info["title"]),("Pelanggan",info["customer"]),("Organisasi",info["organization"]),
            ("Lingkup",info["scope"]),("Nominal","Rp "+f"{info['amount']:,}".replace(",",".")),("Status",info["status"]),
            ("Disetujui",info["quote_accepted_at"]),("Verifikasi",info["paid_at"]),("Referensi",info["reference"])]
    return ("<!doctype html><html lang='id'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Ringkasan tagihan CrownTrace</title><style>body{font:15px system-ui;color:#172e28;max-width:720px;margin:60px auto;padding:24px}"
            "h1{font-size:26px}table{border-collapse:collapse;width:100%}th,td{padding:12px 6px;border-bottom:1px solid #d9e3df;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{width:34%;font-weight:500}small{color:#5b726b}"
            ".demo{padding:12px;background:#fff2c8}</style><h1>CrownTrace. · Ringkasan tagihan</h1>"
            +("<p class='demo'>DEMO / SIMULASI — BUKAN TRANSAKSI NYATA</p>" if info["demo"] else "")
            +"<table>"+"".join(row(*x) for x in fields)+"</table><p><small>"+e(info["note"])+"</small></p></html>").encode("utf-8")
