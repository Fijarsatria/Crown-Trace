"""Conservative same-scale, near-nadir image registration (not photogrammetry).

Full pixel evidence is required; visual theme, filename, and GPS proximity alone
never cause a join. Rotation/scale/perspective changes can remain unregistered.
"""
from collections import defaultdict
from dataclasses import dataclass
from math import hypot

import numpy as np
from PIL import Image, ImageFilter


@dataclass
class Frame:
    path: object
    width: int
    height: int
    fine: np.ndarray
    coarse: np.ndarray
    spectrum: np.ndarray


def _gray(image, limit):
    gray=image.convert('L')
    gray.thumbnail((limit,limit),Image.Resampling.LANCZOS)
    raw=np.asarray(gray,dtype=np.float32)
    trend=np.asarray(gray.filter(ImageFilter.GaussianBlur(1.6)),dtype=np.float32)
    return raw,raw-trend


def prepare(path):
    with Image.open(path) as image:
        width,height=image.size
        fine,high=_gray(image,384)
        coarse,hp=_gray(image,128)
    norm=hp/max(float(hp.std()),.5)
    spectrum=np.fft.rfft2(norm,s=(256,256),axes=(0,1)).astype(np.complex64)
    return Frame(path,width,height,np.stack((fine,high)),np.stack((coarse,hp)),spectrum)


def _overlap(a,b,dx,dy):
    ah,aw=a.shape[-2:];bh,bw=b.shape[-2:]
    x=max(0,dx);y=max(0,dy);r=min(aw,dx+bw);t=min(ah,dy+bh)
    if r-x<20 or t-y<20 or (r-x)*(t-y)<.25*min(aw*ah,bw*bh):return None
    return a[...,y:t,x:r],b[...,y-dy:t-dy,x-dx:r-dx]


def _corr(a,b):
    a=a-a.mean();b=b-b.mean()
    norm=float(np.sqrt(np.sum(a*a)*np.sum(b*b)))
    return float(np.sum(a*b)/norm) if norm>1e-5 else -1.


def _score(a,b,dx,dy):
    o=_overlap(a,b,dx,dy)
    if o is None:return -1.,-1.,None
    u,v=o
    if min(float(u[1].std()),float(v[1].std()))<2.:return -1.,-1.,None
    return _corr(u[1],v[1]),_corr(u[0],v[0]),o


def match(a,b):
    """Return verified pixel translation of b into a; otherwise no edge."""
    # Same original image resolution: avoid inventing a physical scale.
    if a.width!=b.width or a.height!=b.height:return None
    z=a.spectrum*np.conj(b.spectrum)
    phase=np.fft.irfft2(z/np.maximum(np.abs(z),1e-8),s=(256,256),axes=(0,1))
    # Check several phase peaks, with a minimum separation.
    peaks=[];work=phase.copy()
    for _ in range(4):
        y,x=np.unravel_index(np.argmax(work),work.shape)
        peaks.append((int(x if x<128 else x-256),int(y if y<128 else y-256)))
        for dy in range(-4,5):
            for dx in range(-4,5):work[(y+dy)%256,(x+dx)%256]=-np.inf
    coarse=[]
    for dx,dy in peaks:
        for ox in (-1,0,1):
            for oy in (-1,0,1):
                h,l,_=_score(a.coarse,b.coarse,dx+ox,dy+oy)
                coarse.append((h+.4*l,dx+ox,dy+oy))
    score,x,y=max(coarse)
    if score<.36:return None
    # Repetitive rows can create several plausible translations. Do not join if
    # a well-separated alternative has practically the same evidence.
    alternatives=[s for s,dx,dy in coarse if hypot(dx-x,dy-y)>7]
    if alternatives and max(alternatives)>max(.66,score-.055):return None
    fx=a.fine.shape[-1]/a.coarse.shape[-1];fy=a.fine.shape[-2]/a.coarse.shape[-2]
    x0,y0=round(x*fx),round(y*fy)
    best=(-9,None,None,None,None)
    for dx in range(x0-4,x0+5):
        for dy in range(y0-4,y0+5):
            h,l,o=_score(a.fine,b.fine,dx,dy)
            if h+.4*l>best[0]:best=(h+.4*l,h,l,(dx,dy),o)
    _,h,l,delta,o=best
    if o is None or h<.62 or l<.76:return None
    u,v=o;hh,ww=u.shape[-2:]
    regions=[]
    for yy in (0,hh//2):
        for xx in (0,ww//2):
            e=u[:,yy:yy+hh//2,xx:xx+ww//2];f=v[:,yy:yy+hh//2,xx:xx+ww//2]
            if min(e[1].std(),f[1].std())>=2:regions.append(_corr(e[1],f[1]))
    if len(regions)<3 or min(regions)<.40 or np.median(regions)<.59:return None
    dx,dy=delta
    px=round(dx*a.width/a.fine.shape[-1]);py=round(dy*a.height/a.fine.shape[-2])
    return px,py,float(h*.7+l*.3)


def registered_groups(items):
    """Return disconnected components with verified relative translations.

    Known positions are exact audited parent-frame offsets. For unknown photos,
    compare visual content; stronger pair evidence is prioritized. Existing
    component placement is checked whenever another candidate edge constrains it.
    """
    known=defaultdict(list);unknown=[]
    for folder,s in items:
        p=s.get('placement')
        if p and p.get('kind')=='audited_relative_pixels':known[p['group']].append((folder,s,p['x'],p['y']))
        else:unknown.append((folder,s))
    out=[]
    # No overlap required to keep exact known positions in the same verified parent frame.
    for name,members in known.items():out.append(('audited',members))
    if not unknown:return out
    frames=[prepare(folder/'original.png') for folder,_ in unknown]
    n=len(frames);edges=[]
    # Compare every compatible pair for small sets. For large sets, keep nearby
    # capture-order candidates plus globally retrieved perceptual candidates.
    for i,a in enumerate(frames):
        if n<=60:others=range(i+1,n)
        else:
            # Global visual frequency signature is only a retrieval hint, never proof.
            av=np.abs(a.spectrum[:12,:12]);av=av/max(float(np.linalg.norm(av)),1e-8)
            ranked=[]
            for j in range(i+1,n):
                b=frames[j]
                if (a.width,a.height)!=(b.width,b.height):continue
                bv=np.abs(b.spectrum[:12,:12]);bv=bv/max(float(np.linalg.norm(bv)),1e-8)
                ranked.append((float(np.linalg.norm(av-bv)),j))
            others=sorted(set(range(i+1,min(n,i+13)))|{j for _,j in sorted(ranked)[:12]})
        for j in others:
            m=match(a,frames[j])
            if m:edges.append((m[2],i,j,m[0],m[1]))
    # Weighted graph with translations; reject links disagreeing with independent
    # pair constraints between two established components.
    components={i:{i:(0,0)} for i in range(n)}; owner=list(range(n)); constraints={}
    for score,i,j,dx,dy in edges:constraints[(i,j)]=(dx,dy);constraints[(j,i)]=(-dx,-dy)
    for score,i,j,dx,dy in sorted(edges,reverse=True):
        o,p=owner[i],owner[j]
        if o==p:continue
        aa,bb=components[o],components[p];ix,iy=aa[i];jx,jy=bb[j];tx,ty=ix+dx-jx,iy+dy-jy
        consistent=True
        for u,(ux,uy) in aa.items():
            for v,(vx,vy) in bb.items():
                relative=constraints.get((u,v))
                if relative and hypot(vx+tx-ux-relative[0],vy+ty-uy-relative[1])>max(8.,.012*min(frames[u].width,frames[u].height)):
                    consistent=False;break
            if not consistent:break
        if not consistent:continue
        for v,(vx,vy) in bb.items():aa[v]=(vx+tx,vy+ty);owner[v]=o
        del components[p]
    # Attach an unrecognised component to an audited frame only when a visual
    # match proves its position and no other audited frame gives conflicting evidence.
    anchors=[(index,[(*m,prepare(m[0]/'original.png')) for m in members]) for index,(_,members) in enumerate(out)]
    for group in components.values():
        members=[(*unknown[i],x,y) for i,(x,y) in group.items()]
        candidates=[]
        for i,(x,y) in list(group.items())[:8]:
            frame=frames[i];signature=np.abs(frame.spectrum[:12,:12]);signature=signature/max(float(np.linalg.norm(signature)),1e-8)
            for group_index,refs in anchors:
                ranked=[]
                for j,(folder,s,ax,ay,ref) in enumerate(refs):
                    if (ref.width,ref.height)!=(frame.width,frame.height):continue
                    v=np.abs(ref.spectrum[:12,:12]);v=v/max(float(np.linalg.norm(v)),1e-8)
                    ranked.append((float(np.linalg.norm(signature-v)),j))
                for _,j in sorted(ranked)[:8]:
                    folder,s,ax,ay,ref=refs[j];m=match(ref,frame)
                    if m:candidates.append((m[2],group_index,ax+m[0]-x,ay+m[1]-y))
        if candidates and len({c[1] for c in candidates})==1:
            best=max(candidates);_,idx,dx,dy=best
            if all(hypot(a-dx,b-dy)<=max(12.,.02*min(frames[i].width,frames[i].height)) for _,_,a,b in candidates):
                out[idx]=('hybrid',out[idx][1]+[(folder,s,x+dx,y+dy) for folder,s,x,y in members]);continue
        out.append(('visual' if len(members)>1 else 'single',members))
    return out
