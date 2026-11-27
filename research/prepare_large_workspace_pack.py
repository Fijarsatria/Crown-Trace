"""Prepare a 120-crop same-parent image set for a real local batch demo."""
import csv,json,hashlib,shutil
from pathlib import Path
from collections import Counter
from crowntrace.dataset import ROOT,manifest
from crowntrace.audit_overlap import offsets
from research.prepare_contiguous_upload_pack import footprint

def main():
    data=ROOT/'data';pack=data/'UJI_SENDIRI';index=pack/'INDEX_CITRA.csv';target=pack/'08_MOPAD_120_FOTO';target.mkdir(exist_ok=True)
    with index.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);fields=reader.fieldnames;rows=[r for r in reader if not r['file'].startswith('08_MOPAD_')]
    m=manifest(data/'mopad_site2_val_manifest.json');used={Path(r['original']).name for r in rows if r['source']=='MOPAD Site 2'}
    candidates=sorted([r for r in m['samples'] if r['group']=='52000_20000' and r['file_name'] not in used],key=lambda r:offsets(r['file_name'])[::-1])
    assert len(candidates)>=120
    chosen=[candidates[round(i*(len(candidates)-1)/119)] for i in range(120)]
    for i,r in enumerate(chosen,1):
        x,y=offsets(r['file_name']);original=data/'mopad_site2_val_images'/r['file_name'];dest=target/f'SURVEI_{i:03d}_x{x}_y{y}.jpg';shutil.copy2(original,dest)
        counts=Counter(m['categories'][str(obj[0])] for obj in r['objects'])
        rows.append(dict(file=f'{target.name}/{dest.name}',source='MOPAD Site 2',original=f'mopad_site2_val_images/{original.name}',split='test (reused; large demo)',width=1024,height=1024,recommended_mode='Ruang kerja utama otomatis',reference=json.dumps(dict(counts)),notes='Satu parent-frame dengan offset terverifikasi; bukan GPS.',sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
    with index.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    cover,area,total=footprint([offsets(r['file_name']) for r in chosen]);print(f'120 crops; relative rectangle coverage={100*cover:.2f}%; {area}/{total} pixels')

if __name__=='__main__':main()
