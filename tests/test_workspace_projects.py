from io import BytesIO
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest

from crowntrace.workspace_projects import WorkspaceProjects


def wait(service,pid,states,timeout=30):
    until=monotonic()+timeout
    while monotonic()<until:
        p=service.get(pid)
        if p['status'] in states:return p
        sleep(.01)
    raise AssertionError(service.get(pid))


def photo_result(name,ident):
    return {'id':ident,'source':name,'width':128,'height':128,'trees':2,'crowns':2,'counts':{'Healthy':2},
            'files':{'original.png':'/original','overlay.png':'/overlay','summary.json':'/summary'}}


def composition(ids):
    return {'areas':[{'id':ident,'mode':'single_image','images':1,'displayed_candidates':2,'displayed_masks':2,'files':{'summary.json':'/summary/'+ident},'objects':[{}]} for ident in ids]}


def create(service,records):
    p=service.create({'name':'Kebun uji','files':[{'name':name,'size':len(data)} for name,data in records]})
    return p


def upload_all(service,p,records):
    for f,(_,data) in zip(p['files'],records):service.upload(p['id'],f['id'],len(data),BytesIO(data))


def test_upload_resume_duplicate_and_saved_results(tmp_path):
    calls=[]
    def runner(source,name,progress):
        calls.append(name);return photo_result(name,f'{len(calls):032x}')
    service=WorkspaceProjects(tmp_path,None,photo_runner=runner,composer=composition)
    records=[('a.jpg',b'alpha'),('b.jpg',b'beta'),('duplicate.jpg',b'alpha')]
    p=create(service,records);pid=p['id'];first=p['files'][0]
    with pytest.raises(ValueError):service.upload(pid,first['id'],5,BytesIO(b'bad'))
    assert service.get(pid)['waiting']==3
    service.upload(pid,first['id'],5,BytesIO(b'alpha'))
    with pytest.raises(ValueError):service.start(pid)
    # New instance after the original browser/server is gone; first upload is retained.
    reopened=WorkspaceProjects(tmp_path,None,photo_runner=runner,composer=composition)
    assert reopened.get(pid)['uploaded']==1
    upload_all(reopened,p,records);assert reopened.get(pid)['status']=='ready'
    reopened.start(pid);done=wait(reopened,pid,{'done'})
    assert done['succeeded']==2 and done['duplicates']==1 and done['areas']==2
    assert calls==['a.jpg','b.jpg']
    again=WorkspaceProjects(tmp_path,None,photo_runner=runner,composer=composition)
    assert again.list()[0]['id']==pid and again.get(pid)['status']=='done'
    assert len(again.result(pid)['areas'])==2
    assert all('objects' not in area for area in again.result(pid)['areas'])


def test_pause_finish_current_and_resume_without_recomputing(tmp_path):
    entered=Event();release=Event();calls=[]
    def runner(source,name,progress):
        calls.append(name)
        if name=='first.jpg':entered.set();assert release.wait(5)
        return photo_result(name,f'{len(calls):032x}')
    service=WorkspaceProjects(tmp_path,None,photo_runner=runner,composer=composition)
    records=[('first.jpg',b'1'),('second.jpg',b'2')];p=create(service,records);upload_all(service,p,records)
    service.start(p['id']);assert entered.wait(3);service.pause(p['id']);release.set()
    paused=wait(service,p['id'],{'paused'});assert paused['succeeded']==1
    service.start(p['id']);assert wait(service,p['id'],{'done'})['succeeded']==2
    assert calls==['first.jpg','second.jpg']


def test_recover_interrupted_processing_preserves_completed(tmp_path):
    s=WorkspaceProjects(tmp_path,None,photo_runner=lambda *_:None,composer=composition)
    records=[('a.jpg',b'a'),('b.jpg',b'b')];p=create(s,records);upload_all(s,p,records)
    with s.lock:
        data=s._load(p['id']);data['status']='running';data['files'][0].update(status='done',result=photo_result('a.jpg','a'*32));data['files'][1]['status']='processing';s._save(data)
    calls=[]
    def runner(source,name,progress):calls.append(name);return photo_result(name,'b'*32)
    opened=WorkspaceProjects(tmp_path,None,photo_runner=runner,composer=composition)
    assert opened.get(p['id'])['status']=='paused'
    opened.start(p['id']);assert wait(opened,p['id'],{'done'})['succeeded']==2
    assert calls==['b.jpg']


def test_partial_retry_only_failed_and_geo_is_separate(tmp_path):
    attempts=[]
    def photo(source,name,progress):
        attempts.append(name)
        if name=='bad.jpg' and attempts.count(name)==1:raise ValueError('Citra rusak')
        return photo_result(name,f'{len(attempts):032x}')
    def geo(source,name,progress):
        progress('Pemetaan',1,1)
        return {'id':'g','mode':'geotiff','images':1,'displayed_candidates':5,'displayed_masks':5,'files':{'original.jpg':'/map'},'source_files':[name]}
    svc=WorkspaceProjects(tmp_path,None,photo_runner=photo,geo_runner=geo,composer=composition)
    records=[('good.jpg',b'a'),('bad.jpg',b'b'),('map.tif',b'c')];p=create(svc,records);upload_all(svc,p,records)
    svc.start(p['id']);done=wait(svc,p['id'],{'partial'});assert done['failed']==1 and done['areas']==2
    svc.start(p['id'],retry=True);done=wait(svc,p['id'],{'done'});assert done['succeeded']==3 and done['areas']==3
    assert attempts==['good.jpg','bad.jpg','bad.jpg']
