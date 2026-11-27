"""Offline end-to-end exercise of upload handler, real models, and area composition.

Does not open a server, access the network, or use browser telemetry.
"""
from pathlib import Path
from email.message import Message
from io import BytesIO
from urllib.parse import urlencode
import json
from time import perf_counter
from PIL import Image
import argparse

from crowntrace.dataset import ROOT
from crowntrace.lab_segmentation import SegmentationLab
from crowntrace.lab_batch_map import compose_workspace


class Handler:
    def __init__(self,body,mime):
        self.rfile=BytesIO(body);self.headers=Message();self.headers['Content-Length']=str(len(body));self.headers['Content-Type']=mime;self.connection=self
    def settimeout(self,_):pass
    def send_json(self,status,result):self.status=status;self.result=result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--large',action='store_true');args=parser.parse_args()
    start=perf_counter(); dst=ROOT/'analyses/workspace_qa';dst.mkdir(parents=True,exist_ok=True)
    lab=SegmentationLab(dst);pack=ROOT/'data/UJI_SENDIRI'
    photos=sorted((pack/'07_MOPAD_MOSAIC_BERDEKATAN').glob('*.jpg'))
    external=next((pack/'04_PALANGKA_UJI_EKSTERNAL').glob('*.png'))
    def upload(data,name,mime):
        h=Handler(data,mime);lab.post(h,urlencode({'profile':'workspace','filename':name}));assert h.status==201,h.result
        r=h.result;print(name,r['profile'],r['trees'],r['crowns'],r['elapsed_seconds'],flush=True);return r
    def make_batch(results):
        h=Handler(json.dumps({'ids':[r['id'] for r in results]}).encode(),'application/json');lab.batch(h,workspace=True);assert h.status==201,h.result;return h.result
    def concise(batch):return [{k:a.get(k) for k in ('id','images','mode','registration','raw_occurrences','displayed_candidates','displayed_masks','relative_coverage_pct','visual_counts')} for a in batch['areas']]
    if args.large:
        photos=sorted((pack/'08_MOPAD_120_FOTO').glob('*.jpg'));assert len(photos)==120
        results=[upload(f.read_bytes(),f.name,'image/jpeg') for f in photos];result=make_batch(results)
        assert len(result['areas'])==1 and result['areas'][0]['images']==120
        report={'large_batch':concise(result),'elapsed_seconds':round(perf_counter()-start,2),'note':'Real inference for 120 audited same-parent crops; not a photogrammetry or field accuracy benchmark.'}
        (dst/'audit_large.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2),flush=True);return
    known=[upload(f.read_bytes(),f.name,'image/jpeg') for f in photos]
    other=upload(external.read_bytes(),external.name,'image/png')
    result=make_batch(known+[other]);assert sorted(a['images'] for a in result['areas'])==[1,24]
    # Change the original encoding deliberately to remove the curated hash and
    # prove actual pixel-based registration, including an unrelated image.
    def recode(file,name):
        with Image.open(file) as im:
            stream=BytesIO();im.convert('RGB').resize((1024,1024)).save(stream,format='PNG');return upload(stream.getvalue(),name,'image/png')
    raw=[recode(f,f'new_{i}.png') for i,f in enumerate(photos[:6])]
    raw+=[recode(external,'other_estate.png')];visual=make_batch(raw)
    assert sorted(a['images'] for a in visual['areas'])==[1,6],[(a['images'],a['registration']) for a in visual['areas']]
    report={'known_plus_unrelated':concise(result),'unrecognised_reencoded_plus_unrelated':concise(visual),'elapsed_seconds':round(perf_counter()-start,2),'note':'Offline real-model test with known overlapping crops and a separate domain; not a benchmark of arbitrary flight photos or independent field accuracy.'}
    (dst/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
