import json
from pathlib import Path
from tempfile import TemporaryDirectory
import csv

import numpy as np
from PIL import Image, ImageFilter

from crowntrace.relative_registration import prepare, match, registered_groups
from crowntrace.lab_batch_map import compose_workspace
from crowntrace.prepare_annotation_drafts import encode_rle


def make_result(root, name, image, offset=None, group=None):
    folder=root/name;folder.mkdir()
    image.save(folder/'original.png')
    w,h=image.size
    metadata={'width':w,'height':h,'source':name+'.png','source_sha256':name,
              'placement':{'kind':'audited_relative_pixels','group':group,'x':offset[0],'y':offset[1]} if group else None}
    (folder/'summary.json').write_text(json.dumps(metadata),encoding='utf-8')
    mask=np.ones((8,8),dtype=bool)
    (folder/'crowns.coco.json').write_text(json.dumps({'annotations':[{'id':1,'bbox':[40,40,8,8],'segmentation':encode_rle(mask,(40,40,48,48),(w,h))}]}),encoding='utf-8')
    with (folder/'trees.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.writer(f);writer.writerow(['id','label','score','x_px','y_px','mask_px']);writer.writerow([1,'Healthy',.8,44,44,64])
    return folder,metadata


def texture(seed):
    rng=np.random.default_rng(seed)
    a=Image.fromarray(rng.integers(0,255,(660,660,3),dtype=np.uint8),'RGB')
    return a.filter(ImageFilter.GaussianBlur(1.3))


def test_real_pixel_overlap_and_unrelated_are_separate(tmp_path):
    base=texture(21); unrelated=texture(27)
    ids=['a'*32,'b'*32,'c'*32]
    records=[make_result(tmp_path,ids[0],base.crop((0,0,512,512))),make_result(tmp_path,ids[1],base.crop((100,80,612,592))),make_result(tmp_path,ids[2],unrelated.crop((0,0,512,512)))]
    a,b,c=[prepare(folder/'original.png') for folder,_ in records]
    m=match(a,b);assert m is not None and abs(m[0]-100)<=3 and abs(m[1]-80)<=3
    assert match(a,c) is None
    result=compose_workspace(tmp_path,ids)
    assert sorted(a['images'] for a in result['areas'])==[1,2]
    assert {a['mode'] for a in result['areas']}=={'single_image','relative_mosaic'}
    assert all((tmp_path/'batch'/a['id']/'overlay.jpg').is_file() for a in result['areas'])


def test_multiple_known_frames_never_join(tmp_path):
    base=texture(8).crop((0,0,128,128))
    a=make_result(tmp_path,'a'*32,base,(0,0),'first')
    b=make_result(tmp_path,'b'*32,base,(10,0),'second')
    assert len(registered_groups([a,b]))==2


def test_unknown_crop_can_join_verified_frame_without_forced_join(tmp_path):
    base=texture(31);other=texture(33)
    anchored=make_result(tmp_path,'a'*32,base.crop((0,0,512,512)),(500,900),'audited')
    unknown=make_result(tmp_path,'b'*32,base.crop((100,80,612,592)))
    unrelated=make_result(tmp_path,'c'*32,other.crop((0,0,512,512)))
    groups=registered_groups([anchored,unknown,unrelated])
    assert sorted(len(members) for _,members in groups)==[1,2]
    kind,members=next((k,m) for k,m in groups if len(m)==2)
    assert kind=='hybrid'
    _,_,x,y=next(m for m in members if m[0].name=='b'*32)
    assert abs(x-600)<=3 and abs(y-980)<=3


def test_hundred_input_capacity_and_exact_duplicate(tmp_path):
    # Geometry-only performance test; not a trained-model test on 100 new photos.
    im=Image.new('RGB',(128,128),'#436343');ids=[]
    for i in range(101):
        name=f'{i:032x}';ids.append(name);folder,s=make_result(tmp_path,name,im,(i%10*12,i//10*12),'a')
        if i==100:
            s['source_sha256']=ids[0];(folder/'summary.json').write_text(json.dumps(s),encoding='utf-8')
    out=compose_workspace(tmp_path,ids)
    assert out['images']==101 and out['unique_images']==100 and out['duplicate_images']==1
    assert len(out['areas'])==1 and out['areas'][0]['images']==100
