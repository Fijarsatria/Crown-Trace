"""Offline photo-intake audit; prepares a manifest, not a fabricated orthomosaic."""
import argparse,csv,json,hashlib
from pathlib import Path
from collections import Counter
from statistics import median
import numpy as np
from PIL import Image,ImageOps

def digest(path):
    with path.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()

def rational(v):
    try:return float(v)
    except (TypeError,ValueError,ZeroDivisionError):return None

def gps_coord(value,ref):
    if not isinstance(value,tuple) or len(value)!=3:return None
    parts=[rational(x) for x in value]
    if any(v is None for v in parts):return None
    a,b,c=parts;val=a+b/60+c/3600
    return -val if str(ref).upper() in ("S","W") else val

def examine(file):
    with Image.open(file) as p:
        exif=p.getexif()
        try:gps=exif.get_ifd(34853)
        except (KeyError,TypeError,ValueError,AttributeError):gps={}
        lat=gps_coord(gps.get(2),gps.get(1));lon=gps_coord(gps.get(4),gps.get(3))
        if not (lat is not None and lon is not None and -90<=lat<=90 and -180<=lon<=180):lat=lon=None
        # Standardized thumbnail allows relative comparisons; no universal blur threshold.
        rgb=ImageOps.exif_transpose(p).convert("RGB")
        w,h=rgb.size;gray=rgb.convert("L").resize((640,max(3,round(640*h/w))),Image.Resampling.LANCZOS)
        arr=np.asarray(gray,dtype=np.float32)
        lap=arr[:-2,1:-1]+arr[2:,1:-1]+arr[1:-1,:-2]+arr[1:-1,2:]-4*arr[1:-1,1:-1]
        return {"file":file.name,"sha256":digest(file),"width":w,"height":h,
                "gps_lat":lat,"gps_lon":lon,"gps_alt_m":rational(gps.get(6)),
                "capture_time_exif":str(exif.get(36867) or exif.get(306) or ""),
                "camera_make":str(exif.get(271) or ""),"camera_model":str(exif.get(272) or ""),
                "relative_focus":round(float(np.var(lap)),3),
                "dark_fraction":round(float(np.mean(arr<=5)),5),"bright_fraction":round(float(np.mean(arr>=250)),5)}

def components(vertices,edges):
    adj={v:set() for v in vertices}
    for a,b in edges:
        if a in adj and b in adj:adj[a].add(b);adj[b].add(a)
    seen=set();groups=[]
    for x in sorted(adj):
        if x in seen:continue
        current=[];queue=[x];seen.add(x)
        while queue:
            y=queue.pop();current.append(y)
            for z in sorted(adj[y]):
                if z not in seen:seen.add(z);queue.append(z)
        groups.append(sorted(current))
    return sorted(groups,key=lambda g:(-len(g),g))

def audit(source,output,group_audit=None,overlap_audit=None):
    files=sorted(f for f in source.iterdir() if f.is_file() and f.suffix.lower() in (".jpg",".jpeg",".png",".tif",".tiff"))
    if not files:raise ValueError("Tidak ada citra.")
    rows=[];errors=[]
    for f in files:
        try:rows.append(examine(f))
        except (OSError,ValueError,Image.DecompressionBombError) as exc:errors.append({"file":f.name,"error":str(exc)})
    if not rows:raise ValueError("Tidak ada citra yang berhasil dibaca.")
    lookup={r["file"]:r for r in rows};names=set(lookup)
    if group_audit:
        groups=json.loads(group_audit.read_text(encoding="utf-8-sig"))["view_groups"]
        flat=[x for g in groups for x in g]
        if set(flat)!=names or len(flat)!=len(names):raise ValueError("Audit grup tidak sesuai dengan kumpulan file yang dibaca.")
        method="Imported previously verified near-duplicate view groups"
    else:
        by={}
        for r in rows:by.setdefault(r["sha256"],[]).append(r["file"])
        groups=list(by.values());method="Exact file SHA-256 only; visually augmented variants still need review"
    overlap=json.loads(overlap_audit.read_text(encoding="utf-8-sig")) if overlap_audit else None
    preferred=set(overlap["photos"]) if overlap else set()
    selected=[]
    for i,g in enumerate(groups,1):
        matches=preferred.intersection(g)
        if overlap and len(matches)!=1:raise ValueError("Overlap audit must identify one representative per validated group.")
        # Preserve exact orientation/name used for prior feature matching if available.
        best=next(iter(matches)) if matches else max(g,key=lambda n:(lookup[n]["width"]*lookup[n]["height"],lookup[n]["relative_focus"],n))
        selected.append(best)
        for n in g:lookup[n].update(view_group=i,representative=n==best)
    graph=components(selected,[(e["a"],e["b"]) for e in overlap["edges"]]) if overlap else None
    selected_rows=[lookup[n] for n in selected]
    focus_median=median(x["relative_focus"] for x in selected_rows)
    for r in rows:r["relative_focus_low"]=r["relative_focus"]<focus_median*.5
    summary={"photos_total":len(files),"readable":len(rows),"errors":errors,"groups":len(groups),"group_method":method,
             "exact_duplicate_files":len(rows)-len({r["sha256"] for r in rows}),
             "representatives":len(selected),"gps_representatives":sum(r["gps_lat"] is not None for r in selected_rows),
             "datetime_representatives":sum(bool(r["capture_time_exif"]) for r in selected_rows),
             "median_relative_focus":focus_median,"low_relative_focus_representatives":sum(r["relative_focus_low"] for r in selected_rows),
             "overlap_component_sizes":[len(g) for g in graph] if graph else None,
             "geographic_status":"NOT VERIFIED — need mission/GCP/RTK metadata and orthorectified result; GPS alone is not accuracy proof",
             "full_mosaic_status":"NOT VERIFIED — disconnected relative views" if graph and len(graph)>1 else "REQUIRES photogrammetry/coverage/geometry validation",
             "note":"Focus is a relative screening metric, not a universal blur gate. Exact/near-duplicate removal does not establish spatial independence."}
    output.mkdir(parents=True,exist_ok=False)
    for filename,data in (("photo_audit.csv",rows),("staging_manifest.csv",selected_rows)):
        with (output/filename).open("w",encoding="utf-8-sig",newline="") as h:
            w=csv.DictWriter(h,fieldnames=list(rows[0]));w.writeheader();w.writerows(data)
    (output/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    (output/"ground_control_template.csv").write_text("checkpoint_id,role,longitude,latitude,elevation_m,crs,measured_accuracy_m,survey_date,source_reference\n",encoding="utf-8")
    (output/"mission_template.csv").write_text("mission_id,site_id,block_id,acquisition_date,drone,sensor,flight_height_m,gsd_cm,front_overlap_pct,side_overlap_pct,crs,source_approval,notes\n",encoding="utf-8")
    print(json.dumps(summary,indent=2,ensure_ascii=False))

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("source",type=Path);p.add_argument("output",type=Path)
    p.add_argument("--groups",type=Path);p.add_argument("--overlap",type=Path)
    a=p.parse_args();audit(a.source,a.output,a.groups,a.overlap)
