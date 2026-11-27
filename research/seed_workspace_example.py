"""Install the completed, explicitly labelled local QA demo without overwriting user work."""
from pathlib import Path
import json,re,shutil,os
from crowntrace.dataset import ROOT


def main():
    qa=ROOT/'analyses/project_qa';target=ROOT/'analyses';report=json.loads((qa/'audit.json').read_text(encoding='utf-8'));pid=report['project']
    def ident(value):
        if not re.fullmatch(r'[a-f0-9]{32}',value):raise ValueError('ID contoh tidak valid')
        return value
    pid=ident(pid);source=qa/'workspace_projects'/pid;dest=target/'workspace_projects'/pid
    if dest.exists():print('Contoh sudah tersedia; tidak ada berkas yang ditimpa.');return
    p=json.loads((source/'project.json').read_text(encoding='utf-8'));assert p['status']=='done'
    def copy_dir(relative):
        origin=qa/relative;out=target/relative
        if out.exists():raise FileExistsError(f'Berkas contoh berbenturan: {relative}')
        out.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(origin,out)
    for f in p['files']:
        if f['status']!='done':continue
        i=ident(f['result']['id']);copy_dir(Path('surveys' if f['kind']=='geotiff' else 'segmentation')/i)
    for area in p['areas']:
        if area['mode']!='geotiff':copy_dir(Path('segmentation/batch')/ident(area['id']))
    # Copy the project only after all referenced output folders are present.
    copy_dir(Path('workspace_projects')/pid)
    p['name']='Contoh hasil — foto, mosaik & GeoTIFF';p['phase']='Hasil uji contoh tersimpan; bukan survei pelanggan';p['demo']=True
    pending=dest/'project.tmp';pending.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(pending,dest/'project.json')
    print('Contoh siap dibuka di Pekerjaan tersimpan:',pid)

if __name__=='__main__':main()
