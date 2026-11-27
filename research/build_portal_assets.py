"""Create real imagery preview assets from the existing public research dataset."""
from pathlib import Path
from PIL import Image,ImageDraw
import json

root=Path("results/layers_20261126/mopad_layers")
dest=Path("web/portal/assets")
with Image.open(root/"preview.png") as original:
    image=original.convert("RGB")
summary=json.loads((root/"summary.json").read_text())
maps=json.loads((root/"map.json").read_text())
sx=image.width/summary["width"];sy=image.height/summary["height"]
bounds=(int(image.width*.12),int(image.height*.08),int(image.width*.94),int(image.height*.74))
size=(bounds[2]-bounds[0],bounds[3]-bounds[1])
image.crop(bounds).save(dest/"atlas.webp",quality=89,method=6)
layer=Image.new("RGBA",size)
d=ImageDraw.Draw(layer)
for row in maps["trees"]:
    for ring in row.get("rings",[]):
        coords=[(x*sx-bounds[0],y*sy-bounds[1]) for x,y in ring]
        if len(coords)>2:
            d.polygon(coords,fill=(100,238,190,38))
            d.line(coords+[coords[0]],fill=(168,244,202,205),width=1)
    x,y=row["center"];x=x*sx-bounds[0];y=y*sy-bounds[1]
    d.ellipse((x-1,y-1,x+1,y+1),fill=(223,253,220,255))
layer.save(dest/"atlas-crowns.png",optimize=True)
# Photo from the reserved Palangka RGB cohort: no claim of annotations.
field=Path("data/palangka_external_review_v1/images/PAL_025.png")
with Image.open(field) as img:img.convert("RGB").save(dest/"field.webp",quality=88,method=6)
print("Portal asset sizes:", [(p.name,p.stat().st_size) for p in dest.iterdir() if p.is_file()])
