"""Offline real model + durable project exercise, no HTTP listener."""
from pathlib import Path
from time import perf_counter,sleep
import json
import argparse
from crowntrace.dataset import ROOT
from crowntrace.lab_segmentation import SegmentationLab
from crowntrace.workspace_projects import WorkspaceProjects
from crowntrace.survey_api import SurveyService
from crowntrace.server import Service
from crowntrace.compact import CompactDetector
from crowntrace.survey import DETECT_WEIGHTS

def wait(store,pid):
    while True:
        p=store.get(pid)
        print(p['status'],p['processed'],'/',p['total'],p['phase'],flush=True)
        if p['status'] in {'done','partial','paused'}:return p
        sleep(4)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--large',action='store_true');args=parser.parse_args()
    started=perf_counter();root=ROOT/'analyses/project_qa';root.mkdir(parents=True,exist_ok=True)
    model=CompactDetector(DETECT_WEIGHTS);service=Service(root,model,'compact',.40);surveys=SurveyService(service);lab=SegmentationLab(root)
    db=WorkspaceProjects(root,lab,surveys);pack=ROOT/'data/UJI_SENDIRI'
    paths=sorted((pack/('08_MOPAD_120_FOTO' if args.large else '07_MOPAD_MOSAIC_BERDEKATAN')).glob('*.jpg'))
    if not args.large:
        paths=paths[:6]+[next((pack/'04_PALANGKA_UJI_EKSTERNAL').glob('*.png')),next((pack/'06_PETA_GEOTIFF').glob('02_*.tif'))]
        paths.append(paths[0]) # duplicate of an actual byte-identical original
    p=db.create({'name':'Audit batch besar' if args.large else 'Audit foto + GeoTIFF + duplikat','files':[{'name':f.name,'size':f.stat().st_size} for f in paths]})
    uid=p['id']
    for f,slot in zip(paths,p['files']):
        with f.open('rb') as source:db.upload(uid,slot['id'],f.stat().st_size,source)
    # Prove that an entirely new project-store instance can start a saved upload.
    reopened=WorkspaceProjects(root,lab,surveys);assert reopened.get(uid)['uploaded']==len(paths)
    reopened.start(uid);done=wait(reopened,uid);result=reopened.result(uid)
    assert done['status']=='done',done
    if not args.large:assert done['duplicates']==1 and sorted(a['mode'] for a in result['areas'])==['geotiff','relative_mosaic','single_image']
    else:assert done['succeeded']==120 and len(result['areas'])==1
    reloaded=WorkspaceProjects(root,lab,surveys);assert reloaded.get(uid)['status']=='done' and len(reloaded.result(uid)['areas'])==len(result['areas'])
    report={'project':uid,'status':done['status'],'total':done['total'],'succeeded':done['succeeded'],'duplicates':done['duplicates'],'areas':[{k:a.get(k) for k in ('mode','images','displayed_candidates','displayed_masks','relative_coverage_pct','details_url')} for a in result['areas']],'elapsed_seconds':round(perf_counter()-started,2)}
    (root/('audit_large.json' if args.large else 'audit.json')).write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2),flush=True)
    surveys.pool.shutdown(wait=True)

if __name__=='__main__':main()
