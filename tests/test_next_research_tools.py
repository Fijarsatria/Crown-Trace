import csv,hashlib,json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from research.review_blind_packs import compare, status
from research.drone_intake import audit,components

class ResearchToolTests(unittest.TestCase):
    def test_blind_independent_gate(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/"images").mkdir()
            f=root/"images"/"fixture.png";Image.new("RGB",(20,20),"green").save(f)
            sha=hashlib.sha256(f.read_bytes()).hexdigest()
            with (root/"manifest.csv").open("w",encoding="utf-8",newline="") as h:
                w=csv.DictWriter(h,fieldnames=["tile","tile_sha256","width","height","review_status"]);w.writeheader()
                w.writerow({"tile":"fixture.png","tile_sha256":sha,"width":20,"height":20,"review_status":"pending"})
            self.assertEqual(status(root)["pending"],1)
            tile={"tile":"fixture.png","sha256":sha,"width":20,"height":20,"status":"complete","polygons":[[[2,2],[12,2],[12,12],[2,12]]]}
            def pack(name,round,item):
                p=root/(round+".json");p.write_text(json.dumps({"schema":"crowntrace-blind-v1","reviewer":name,"round":round,"tiles":[item]}));return p
            a=pack("Fixture A","A",tile);b=pack("Fixture B","B",tile)
            r=compare(root,a,b,root/"report.json")
            self.assertEqual(r["pairs75"],1);self.assertEqual(r["mean_matched_iou"],1)
            self.assertEqual(r["status"],"requires_adjudication")
            b=pack("Fixture A","B",tile)
            with self.assertRaises(ValueError):compare(root,a,b,root/"bad.json")
            b=pack("Fixture B","B",{**tile,"status":"pending"})
            with self.assertRaises(ValueError):compare(root,a,b,root/"pending.json")

    def test_intake_small_fixture(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"raw";source.mkdir()
            Image.new("RGB",(24,24),"green").save(source/"a.png")
            (source/"b.png").write_bytes((source/"a.png").read_bytes())
            Image.new("RGB",(24,24),"blue").save(source/"c.png")
            audit(source,root/"audit")
            s=json.loads((root/"audit/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(s["photos_total"],3);self.assertEqual(s["groups"],2)
            self.assertEqual(s["gps_representatives"],0);self.assertEqual(s["exact_duplicate_files"],1)
            self.assertEqual([len(c) for c in components(["a","b","c"],[("a","b")])],[2,1])

if __name__=="__main__":unittest.main(verbosity=2)
