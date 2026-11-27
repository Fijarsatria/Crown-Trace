"""Versioned, non-destructive review of an immutable CrownTrace result.

Visual review is not disease diagnosis or independent test ground truth.
Only assigned staff can edit the current draft; release freezes the event log.
"""
import csv
import hashlib
import io
import json
import math
from collections import Counter
from pathlib import Path
from .portal_store import Problem, now, uid, string, integer, calendar

SCHEMA = """
CREATE TABLE IF NOT EXISTS quality_edits(
 job_id TEXT NOT NULL REFERENCES jobs(id), revision INTEGER NOT NULL,
 actor_id TEXT NOT NULL REFERENCES users(id), tree_id TEXT NOT NULL,
 action TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(job_id,revision));
CREATE INDEX IF NOT EXISTS quality_tree ON quality_edits(job_id,tree_id,revision);
CREATE TABLE IF NOT EXISTS quality_releases(
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), revision INTEGER NOT NULL,
 source_sha256 TEXT NOT NULL, summary TEXT NOT NULL, released_at TEXT NOT NULL);
"""
VISUAL={"normal_visual":"Normal visual","stress_visual":"Indikasi stres visual",
        "dead_visual":"Indikasi mati","uncertain":"Tidak dapat dinilai"}
ACTIONS={"review","exclude","add","reshape","restore"}
MAX_EDITS=20000


def load_result(folder):
    report=json.loads((folder/"report.json").read_text(encoding="utf-8"))
    raw=(folder/"map.json").read_bytes()
    data=json.loads(raw)
    if len({t["id"] for t in data["trees"]})!=len(data["trees"]):
        raise Problem(409,"ID objek pada hasil tidak unik.")
    return report,data,hashlib.sha256(raw).hexdigest()


def xy(value,w,h):
    if (not isinstance(value,list) or len(value)!=2 or
        any(type(z) not in (int,float) or not math.isfinite(z) for z in value) or
        not (0<=value[0]<w and 0<=value[1]<h)):
        raise Problem(422,"Titik harus berada dalam dimensi citra.")
    return [round(float(z),3) for z in value]


def cross(a,b,c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def intersect(a,b,c,d):
    if (max(a[0],b[0])+1e-8<min(c[0],d[0]) or max(c[0],d[0])+1e-8<min(a[0],b[0]) or
        max(a[1],b[1])+1e-8<min(c[1],d[1]) or max(c[1],d[1])+1e-8<min(a[1],b[1])):return False
    return cross(a,b,c)*cross(a,b,d)<=1e-8 and cross(c,d,a)*cross(c,d,b)<=1e-8


def inside(point,ring):
    x,y=point;yes=False
    for a,b in zip(ring,ring[1:]+ring[:1]):
        if abs(cross(a,b,point))<1e-6 and min(a[0],b[0])-1e-6<=x<=max(a[0],b[0])+1e-6 and min(a[1],b[1])-1e-6<=y<=max(a[1],b[1])+1e-6:return True
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:yes=not yes
    return yes


def polygon(value,w,h,center):
    if not isinstance(value,list) or not 3<=len(value)<=100:raise Problem(422,"Kontur perlu 3–100 titik.")
    pts=[xy(v,w,h) for v in value]
    if len({tuple(p) for p in pts})!=len(pts):raise Problem(422,"Titik kontur tidak boleh berulang.")
    size=len(pts)
    for i in range(size):
        for j in range(i+1,size):
            if j==i+1 or (i==0 and j==size-1):continue
            if intersect(pts[i],pts[(i+1)%size],pts[j],pts[(j+1)%size]):
                raise Problem(422,"Kontur bersilangan; gambar ulang.")
    area=abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(pts,pts[1:]+pts[:1])))/2
    if area<9 or not inside(center,pts):raise Problem(422,"Kontur harus mencakup pusat pohon dan memiliki luas minimal 9 px².")
    return pts,round(area,3)


def block_for(point,report,data):
    hits=[]
    for block,geom in zip(report.get("blocks",[]),data.get("blocks",[])):
        kind=geom.get("type");co=geom.get("coordinates",[])
        parts=[co] if kind=="Polygon" else co if kind=="MultiPolygon" else []
        if any(p and inside(point,p[0]) and not any(inside(point,h) for h in p[1:]) for p in parts):hits.append(block["block"])
    return hits[0] if len(hits)==1 else "Unassigned"


def coordinate_transform(report,data):
    # An explicit affine from the original raster is authoritative, not a
    # fitted approximation of the old demo coordinates.
    a=report.get("geotransform")
    if report.get("coordinate_mode")!="geographic" or not isinstance(a,list) or len(a)!=6:return None
    if any(type(v) not in (float,int) or not math.isfinite(v) for v in a):return None
    try:
        from pyproj import Transformer
        tf=Transformer.from_crs(report["crs"],"EPSG:4326",always_xy=True)
        def convert(p):
            x,y=p;lon,lat=tf.transform(a[0]+x*a[1]+y*a[2],a[3]+x*a[4]+y*a[5])
            if not(-180<=lon<=180 and -85<=lat<=85):raise ValueError("invalid lon/lat")
            return [lon,lat]
        for t in data["trees"][:15]:
            if t.get("coordinates") and max(abs(x-y) for x,y in zip(convert(t["center"]),t["coordinates"]))>0.00002:
                return None
        return convert
    except (ValueError,KeyError,TypeError,RuntimeError):return None


def base_row(tree):
    return {"id":tree["id"],"center":tree["center"],"block":tree.get("block"),"coordinates":tree.get("coordinates"),
            "model_label":tree.get("visual_label"),"model_score":tree.get("score"),"review_state":"pending",
            "source":"model","geometry_origin":"model","visual_condition":None,"excluded":False}


def materialize(report,data,events,digest):
    rows={t["id"]:base_row(t) for t in data["trees"]}
    originals={k:dict(v) for k,v in rows.items()}
    changed={}
    for ev in events:
        key=ev["tree_id"];action=ev["action"];payload=json.loads(ev["payload"])
        if action=="restore":
            if key in originals:rows[key]=dict(originals[key])
            else:rows.pop(key,None)
            changed.pop(key,None)
            continue
        if action=="add":
            row={"id":key,"center":payload["center"],"block":payload.get("block"),"coordinates":payload.get("coordinates"),
                 "model_label":None,"model_score":None,"source":"manual","geometry_origin":"manual_point","excluded":False}
            rows[key]=row
        else:
            row=rows[key]
        if action=="exclude":
            row["excluded"]=True;row["review_state"]="excluded"
        else:
            row["excluded"]=False
            if action=="reshape":
                row.update(polygon_px=payload["polygon_px"],polygon_area_px=payload["polygon_area_px"],polygon_geo=payload.get("polygon_geo"),geometry_origin="manual_polygon")
            row.update(review_state="reviewed",visual_condition=payload["visual_condition"],
                       review_source=payload["review_source"],observation_date=payload.get("observation_date"),
                       evidence_ref=payload.get("evidence_ref",""))
        row.update(note=payload["note"],reviewed_at=ev["created_at"])
        changed[key]=dict(row)
    counted=[r for r in rows.values() if not r["excluded"]]
    reviewed=[r for r in counted if r["review_state"]=="reviewed"]
    summary={"model_count":len(originals),"effective_count":len(counted),"reviewed_count":len(reviewed),
             "pending_count":sum(r["review_state"]=="pending" for r in counted),
             "excluded_count":sum(r["excluded"] for r in rows.values()),"added_count":sum(r["source"]=="manual" for r in counted),
             "corrected_contours":sum(r["geometry_origin"]=="manual_polygon" for r in counted),
             "missing_geographic_coordinates":sum(not r.get("coordinates") for r in counted) if report["coordinate_mode"]=="geographic" else None,
             "visual_counts":dict(Counter(r["visual_condition"] for r in reviewed)),
             "field_observations":sum(r.get("review_source")=="field_observation" for r in reviewed),
             "by_block":dict(Counter(r.get("block") or "Unassigned" for r in counted))}
    by_id={t["id"]:t for t in data["trees"]}
    pending=[]
    edge=max(8,min(report["width"],report["height"])*.025)
    for r in counted:
        if r["review_state"]!="pending":continue
        t=by_id[r["id"]];x,y=r["center"];near=min(x,y,report["width"]-x,report["height"]-y)<edge
        score=r["model_score"] if isinstance(r["model_score"],(int,float)) else 1
        flags=[]
        if t.get("review_candidate") or t.get("review_tier") in ("needs_review","high_priority"):flags.append("Indikasi inspeksi")
        if near:flags.append("Dekat tepi citra")
        if score<.5:flags.append("Skor detektor rendah")
        pending.append((not bool(flags and flags[0]=="Indikasi inspeksi"),not near,score,r["id"],flags))
    queue=[{"id":key,"score":score,"reasons":flags or ["Review rutin"]} for _,_,score,key,flags in sorted(pending)[:30]]
    return {"revision":events[-1]["revision"] if events else 0,"summary":summary,"changes":list(changed.values()),"review_queue":queue,
            "source_sha256":digest,"coordinate_mode":report["coordinate_mode"],"example":bool(report.get("example")),
            "note":"Koreksi per citra, bukan sensus lengkap/diagnosis atau ground truth independen. Layer model asli tetap terpisah."},list(rows.values())


def _context(store,db,actor,oid,jid):
    o=store._role_order(db,actor,oid)
    job=db.execute("SELECT * FROM jobs WHERE id=? AND order_id=? AND status='succeeded'",(jid,oid)).fetchone()
    pub=db.execute("SELECT version FROM publications WHERE job_id=? AND order_id=?",(jid,oid)).fetchone()
    if not job or (actor["role"]=="customer" and not pub):raise Problem(404,"Hasil belum tersedia.")
    folder=store.results/jid
    report,data,digest=load_result(folder)
    can_edit=not pub and actor["role"] in ("admin","operator") and o["status"]=="quality_review" and o["current_job_id"]==jid
    events=[dict(r) for r in db.execute("SELECT * FROM quality_edits WHERE job_id=? ORDER BY revision",(jid,))]
    frozen=db.execute("SELECT * FROM quality_releases WHERE job_id=?",(jid,)).fetchone()
    if frozen and (frozen["source_sha256"]!=digest or frozen["revision"]!=(events[-1]["revision"] if events else 0)):
        raise Problem(409,"Integritas rilis koreksi tidak sesuai.")
    info,rows=materialize(report,data,events,digest)
    info.update(can_edit=bool(can_edit),released=bool(pub),publication_version=pub["version"] if pub else None)
    return o,report,data,info,rows


def review_state(store,actor,oid,jid,*,include_rows=False):
    with store.connect() as db:
        _,_,_,info,rows=_context(store,db,actor,oid,jid)
        if include_rows:info["rows"]=rows
        return info


def edit(store,actor,oid,jid,body):
    if not isinstance(body,dict):raise Problem(422,"Data koreksi tidak valid.")
    with store.tx() as db:
        order,report,data,info,rows=_context(store,db,actor,oid,jid)
        if not info["can_edit"]:raise Problem(409,"Koreksi hanya dapat dibuat tim pada draft aktif.")
        if type(body.get("revision")) is not int or body["revision"]!=info["revision"]:
            raise Problem(409,"Koreksi berubah. Muat ulang sebelum menyimpan.")
        if info["revision"]>=MAX_EDITS:raise Problem(409,"Batas perubahan tercapai; hubungi admin.")
        action=body.get("action");key=body.get("tree_id")
        if action not in ACTIONS:raise Problem(422,"Jenis koreksi tidak valid.")
        existing={r["id"]:r for r in rows};w,h=report["width"],report["height"]
        payload={"note":string(body.get("note"),"Alasan koreksi",3,500)}
        if action=="add":
            key="QC-"+uid()[:16];center=xy(body.get("center"),w,h)
            if any(not r["excluded"] and math.dist(center,r["center"])<2 for r in rows):
                raise Problem(422,"Pusat terlalu dekat dengan pohon yang sudah ada.")
            transform=coordinate_transform(report,data)
            payload.update(center=center,block=block_for(center,report,data),coordinates=transform(center) if transform else None)
        else:
            if not isinstance(key,str) or key not in existing:raise Problem(404,"Objek tidak ditemukan.")
            center=existing[key]["center"]
        if action in ("review","add","reshape"):
            condition=body.get("visual_condition")
            if condition not in VISUAL:raise Problem(422,"Pilih kategori visual.")
            source=body.get("review_source","image_review")
            if source not in ("image_review","field_observation"):raise Problem(422,"Sumber observasi tidak valid.")
            payload.update(visual_condition=condition,review_source=source)
            if source=="field_observation":
                observed=calendar(body.get("observation_date"),"Tanggal observasi")
                from .portal_store import today
                if observed>today().isoformat():raise Problem(422,"Tanggal observasi tidak boleh di masa depan.")
                payload.update(observation_date=observed,evidence_ref=string(body.get("evidence_ref"),"Referensi catatan lapangan",3,200))
            if action=="reshape":
                poly,area=polygon(body.get("polygon_px"),w,h,center)
                payload.update(polygon_px=poly,polygon_area_px=area)
                transform=coordinate_transform(report,data)
                if transform:payload["polygon_geo"]=[transform(p) for p in poly]
        revision=info["revision"]+1
        db.execute("INSERT INTO quality_edits VALUES (?,?,?,?,?,?,?)",(jid,revision,actor["id"],key,action,json.dumps(payload,ensure_ascii=False,allow_nan=False),now()))
        store._update(db,oid)
        if revision==1:store._event(db,oid,actor["id"],"quality_edit","Koreksi objek dimulai","Tim meninjau hasil model.",visibility="staff")
        return _context(store,db,actor,oid,jid)[3]


def freeze(store,db,jid):
    # Called within the same publication transaction. No edits may follow.
    folder=store.results/jid
    report,data,digest=load_result(folder)
    events=[dict(r) for r in db.execute("SELECT * FROM quality_edits WHERE job_id=? ORDER BY revision",(jid,))]
    info,_=materialize(report,data,events,digest)
    db.execute("INSERT INTO quality_releases VALUES (?,?,?,?,?)",(jid,info["revision"],digest,json.dumps(info["summary"]),now()))


def export_csv(info):
    out=io.StringIO(newline="")
    cols=["id","source","excluded","review_state","model_label","model_score","visual_condition","review_source","observation_date","x_px","y_px","longitude","latitude","block","geometry_origin","polygon_area_px","note","evidence_ref"]
    writer=csv.DictWriter(out,fieldnames=cols);writer.writeheader()
    def safe(v):
        return "'"+v if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@")) else v
    for r in info["rows"]:
        record={k:r.get(k) for k in cols}
        record["x_px"],record["y_px"]=r["center"]
        record["longitude"],record["latitude"]=r.get("coordinates") or (None,None)
        writer.writerow({k:safe(v) for k,v in record.items()})
    return out.getvalue().encode("utf-8-sig")


def export_geojson(info):
    if info["coordinate_mode"]!="geographic":raise Problem(422,"GeoJSON hanya tersedia untuk hasil geografis.")
    features=[];polygon_missing=0
    for row in info["rows"]:
        if row["excluded"]:continue
        props={k:row.get(k) for k in ("id","source","review_state","visual_condition","review_source","geometry_origin","block")}
        if row.get("coordinates"):
            features.append({"type":"Feature","properties":{**props,"layer":"reviewed_inventory_point"},"geometry":{"type":"Point","coordinates":row["coordinates"]}})
        if row.get("polygon_px"):
            if row.get("polygon_geo"):
                ring=row["polygon_geo"]+[row["polygon_geo"][0]]
                features.append({"type":"Feature","properties":{**props,"layer":"manual_crown_correction"},"geometry":{"type":"Polygon","coordinates":[ring]}})
            else:polygon_missing+=1
    return {"type":"FeatureCollection","features":features,"crowntrace_review":{"revision":info["revision"],"example":info["example"],
            "summary":info["summary"],"manual_polygons_without_geo":polygon_missing,
            "note":"Corrected points and manually drawn geographic polygons only. Original model crowns are exported separately. Missing coordinates are not fabricated."}}
