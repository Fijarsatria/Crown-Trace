"""Combine completed local lab analyses; only verified MOPAD crops have shared positions."""
from pathlib import Path
from math import ceil, sqrt
from collections import Counter
from uuid import uuid4
import csv
import hashlib
import json
import re

from PIL import Image, ImageDraw
import numpy as np

from .dataset import ROOT
from .audit_overlap import offsets
from .inference import Detection, fuse

PACK_INDEX = ROOT / "data/UJI_SENDIRI/INDEX_CITRA.csv"
AUDIT = ROOT / "results/overlap_screen.json"
BATCH_FILES = {"original.jpg": "image/jpeg", "thumbnail.jpg": "image/jpeg", "crowns.png": "image/png", "points.png": "image/png",
               "coverage.png": "image/png", "overlay.jpg": "image/jpeg", "summary.json": "application/json"}


def identify(data):
    """Identify a curated original by its exact file bytes, not its display name."""
    digest = hashlib.sha256(data).hexdigest()
    result = {"sha256": digest, "recommended": None, "placement": None, "reference_count": None}
    if not PACK_INDEX.is_file():
        return result
    with PACK_INDEX.open(encoding="utf-8-sig", newline="") as file:
        row = next((row for row in csv.DictReader(file) if row["sha256"] == digest), None)
    if row is None:
        return result
    if row["file"].startswith(("01_MOPAD_", "07_MOPAD_", "08_MOPAD_")):
        result["recommended"] = "mopad"
        result["reference_count"] = sum(json.loads(row["reference"]).values())
        if not AUDIT.is_file():
            return result
        source = Path(row["original"]).name
        group = "_".join(Path(source).stem.split("_")[:2])
        proof = json.loads(AUDIT.read_text(encoding="utf-8"))["alignment"].get(group, {})
        if proof.get("sampled_pairs") and proof["median_gray_mad_xy"] < .20 * proof["median_gray_mad_offset_control"]:
            x, y = offsets(source)
            result["placement"] = {"group": group, "x": x, "y": y, "kind": "audited_relative_pixels"}
    elif row["file"].startswith("02_TREES_"):
        result["recommended"] = "roboflow_prompt"
        result["reference_count"] = int(row["reference"].split()[0])
    return result


def _local_mask(rle, bbox):
    """Decode only the bounding window of an uncompressed COCO RLE."""
    h, w = rle["size"]
    x, y, bw, bh = map(int, bbox)
    out = np.zeros((bh, bw), dtype=np.uint8)
    pos = 0
    for i, count in enumerate(rle["counts"]):
        stop = pos + count
        if i % 2 and count:
            first, last = max(x, pos // h), min(x+bw-1, (stop-1)//h)
            for col in range(first, last+1):
                lo, hi = max(y, pos-col*h), min(y+bh, stop-col*h)
                if hi > lo:
                    out[lo-y:hi-y, col-x] = 255
        pos = stop
    if pos != h*w:
        raise ValueError("Mask COCO tidak konsisten.")
    return Image.fromarray(out)


def coverage_fraction(rects, width, height):
    edges=sorted({edge for x,y,w,h in rects for edge in (x,x+w)})
    area=0
    for a,b in zip(edges,edges[1:]):
        intervals=sorted((y,y+h) for x,y,w,h in rects if x<=a and x+w>=b)
        if not intervals:continue
        lo,hi=intervals[0];covered=0
        for start,end in intervals[1:]:
            if start>hi:covered+=hi-lo;lo,hi=start,end
            else:hi=max(hi,end)
        area+=(b-a)*(covered+hi-lo)
    return area/(width*height)


def compose(root, ids, *, group=None):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 300 or any(not isinstance(i, str) or not re.fullmatch(r"[a-f0-9]{32}", i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Pilih 1–300 hasil analisis yang berbeda.")
    items = []
    for ident in ids:
        folder = root / ident
        if not (folder / "summary.json").is_file():
            raise ValueError("Salah satu hasil tidak ditemukan.")
        s = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        items.append((folder, s))
    placements = [s.get("placement") for _, s in items]
    registered = all(p and p.get("kind") == "audited_relative_pixels" for p in placements) and len({p["group"] for p in placements}) == 1
    group_kind = group[0] if group else None
    if group:
        members=group[1]
        x0=min(x for _,_,x,y in members);y0=min(y for _,_,x,y in members)
        rects=[(x-x0,y-y0,s['width'],s['height']) for _,s,x,y in members]
        registered=len(members)>1
    elif registered:
        x0, y0 = min(p["x"] for p in placements), min(p["y"] for p in placements)
        rects = [(p["x"]-x0, p["y"]-y0, s["width"], s["height"]) for p, (_,s) in zip(placements, items)]
    else:
        # Independent photos: a clearly labelled display grid, not invented geographic placement.
        cols = ceil(sqrt(len(items)))
        side = min(700, max(max(s["width"],s["height"]) for _,s in items))
        rects=[]
        for i,(_,s) in enumerate(items):
            ratio=side/max(s["width"],s["height"])
            rects.append(((i%cols)*(side+20), (i//cols)*(side+30), round(s["width"]*ratio), round(s["height"]*ratio)))
    width=max(x+w for x,y,w,h in rects); height=max(y+h for x,y,w,h in rects)
    scale=min(1, (1800 if len(items)==1 else 3200)/max(width,height)); size=(max(1,ceil(width*scale)),max(1,ceil(height*scale)))
    def scaled(x,y): return (round(x*scale),round(y*scale))
    base=Image.new("RGB", size, (24,33,28)); coverage=Image.new("RGBA",size); cover_draw=ImageDraw.Draw(coverage)
    crown=Image.new("RGBA",size); points=Image.new("RGBA",size); point_draw=ImageDraw.Draw(points)
    candidates=[]; entries={}; tiles=[]
    for view,((folder,s),(x,y,w,h)) in enumerate(zip(items,rects),1):
        left,top=scaled(x,y); right,bottom=scaled(x+w,y+h)
        with Image.open(folder/"original.png") as im:
            base.paste(im.convert("RGB").resize((max(1,right-left),max(1,bottom-top)),Image.Resampling.LANCZOS),(left,top))
        cover_draw.rectangle((left,top,max(left,right-1),max(top,bottom-1)),outline=(255,211,100,190),width=2)
        cover_draw.text((left+5,top+4),f"{view:02d}",fill=(255,246,210,255),stroke_width=1,stroke_fill=(15,20,15,255))
        tiles.append({"file":s["source"],"id":folder.name,"rect":[left,top,right,bottom]})
        coco=json.loads((folder/"crowns.coco.json").read_text(encoding="utf-8"))
        ann={a["id"]:a for a in coco["annotations"]}
        with (folder/"trees.csv").open(encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                a=ann.get(int(row["id"])); cx=float(row["x_px"]);cy=float(row["y_px"])
                bx,by,bw,bh=a["bbox"] if a else (cx-2,cy-2,4,4)
                sx,sy=w/s["width"],h/s["height"]
                det=Detection((x+bx*sx,y+by*sy,x+(bx+bw)*sx,y+(by+bh)*sy),row["label"],float(row["score"]),view)
                # Use registered candidates across tiles, preserving the supporting mask for each.
                candidates.append(det);entries[id(det)]=(a,x,y,sx,sy,cx,cy,row["label"])
    selected=fuse(candidates,overlap=.60,center_ratio=.28) if registered else candidates
    from .photo import COLORS
    masks=0; counts=Counter(); records=[]
    for det in selected:
        a,x,y,sx,sy,cx,cy,label=entries[id(det)]
        rgb=COLORS.get(label,(116,235,197)); px,py=scaled(x+cx*sx,y+cy*sy)
        counts[label]+=1;records.append({"id":f"CT-{len(records)+1:05d}","label":label,"center":[px,py],"confidence":round(det.score,4),"mask":bool(a)})
        point_draw.ellipse((px-2,py-2,px+2,py+2),fill=(*rgb,255),outline=(15,30,20,255))
        if not a:continue
        masks+=1;bx,by,bw,bh=a["bbox"]
        l,t=scaled(x+bx*sx,y+by*sy);r,b=scaled(x+(bx+bw)*sx,y+(by+bh)*sy)
        if r<=l or b<=t:continue
        mask=_local_mask(a["segmentation"],a["bbox"]).resize((r-l,b-t),Image.Resampling.NEAREST)
        rgba=Image.new("RGBA",mask.size,(*rgb,0));rgba.putalpha(mask.point(lambda v: int(v*.62)))
        crown.alpha_composite(rgba,(l,t))
    token=uuid4().hex;dest=root/"batch"/token;dest.mkdir(parents=True)
    base.save(dest/"original.jpg",quality=90);crown.save(dest/"crowns.png");points.save(dest/"points.png");coverage.save(dest/"coverage.png")
    combined=Image.alpha_composite(Image.alpha_composite(base.convert('RGBA'),crown),points)
    combined.convert('RGB').save(dest/'overlay.jpg',quality=91)
    thumb=combined.convert('RGB');thumb.thumbnail((300,300));thumb.save(dest/'thumbnail.jpg',quality=83)
    summary={"id":token,"mode":"relative_mosaic" if registered else "image_grid","images":len(items),"width":size[0],"height":size[1],
             "raw_occurrences":len(candidates),"displayed_candidates":len(selected),"displayed_masks":masks,
             "visual_counts":dict(counts),"objects":records,"registration":group_kind,
             "relative_coverage_pct":round(100*coverage_fraction(rects,width,height),2) if registered else None,
             "coordinate_mode":"relative_pixels" if registered else "independent_display_grid", "tiles":tiles,
             "note":("Posisi relatif dari offset MOPAD yang diaudit; area gelap adalah celah tanpa citra. Kandidat gabungan menggunakan deduplikasi bbox mask, bukan sensus unik tervalidasi atau GPS." if registered else "Panel citra independen; penempatan grid hanya untuk inspeksi visual. Jumlah antar foto tidak dianggap populasi kebun unik."),
             "files":{name:f"/api/segmentation/batch/{token}/{name}" for name in BATCH_FILES}}
    if group_kind in {'visual','hybrid'}:
        summary['note']="Foto disambung berdasarkan overlap visual yang lolos pemeriksaan translasi. Mosaik relatif perkiraan, bukan orthomosaic/GPS; deduplikasi belum divalidasi lapangan."
    elif group_kind=='single' or (group_kind and len(items)==1):
        summary.update(mode='single_image',coordinate_mode='pixel',note="Foto ditampilkan sendiri karena tidak ada kecocokan posisi yang cukup dengan foto lain. Jumlah adalah prediksi model.")
    if group and any(s.get('recommended_profile') is None for _,s in items):
        summary['note']+=' Citra baru belum tervalidasi untuk model kondisi visual; warna/kategori dapat keliru.'
    (dest/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


def compose_workspace(root, ids):
    """Produce distinct maps, not a fabricated montage for unrelated estates."""
    if not isinstance(ids,list) or not 1<=len(ids)<=300 or any(not isinstance(i,str) or not re.fullmatch(r'[a-f0-9]{32}',i) for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('Pilih 1–300 hasil yang berbeda.')
    items=[]; seen=set(); duplicates=0
    for ident in ids:
        folder=root/ident
        s=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
        digest=s.get('source_sha256') or ident
        if digest in seen:duplicates+=1;continue
        seen.add(digest);items.append((folder,s))
    from .relative_registration import registered_groups
    groups=registered_groups(items)
    groups.sort(key=lambda g:(-len(g[1]),g[1][0][1]['source']))
    areas=[]
    for number,(kind,members) in enumerate(groups,1):
        area=compose(root,[folder.name for folder,_,_,_ in members],group=(kind,members))
        area.update(name=f'Area {number}',source_files=[s['source'] for _,s,_,_ in members])
        areas.append(area)
    return {'images':len(ids),'unique_images':len(items),'duplicate_images':duplicates,'areas':areas,
            'note':'Area terpisah tidak dijumlahkan sebagai sensus kebun unik. Sambungan relatif hanya untuk citra yang mempunyai bukti kecocokan.'}
