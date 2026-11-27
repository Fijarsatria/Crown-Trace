"""Durable, single-machine research projects. Upload first; processing is server-owned."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import RLock, Thread
from uuid import uuid4
import hashlib
import json
import logging
import os
import re
import shutil

from .lab_segmentation import MAX_UPLOAD
from .lab_batch_map import compose_workspace
from .survey_api import MAX_SURVEY_BYTES

LOG = logging.getLogger(__name__)
ID = r"[a-f0-9]{32}"
ACTIVE = {"queued", "running", "mapping", "pausing"}


def now():
    return datetime.now(timezone.utc).isoformat()


def clean_name(name, limit=160):
    if not isinstance(name, str):
        raise ValueError("Nama berkas tidak valid.")
    name = name.replace("\\", "/").split("/")[-1].strip()[:limit]
    if not name or any(ord(c) < 32 for c in name):
        raise ValueError("Nama berkas tidak valid.")
    return name


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class WorkspaceProjects:
    def __init__(self, store, segmentation, surveys=None, *, photo_runner=None, geo_runner=None, composer=None):
        self.root = Path(store) / "workspace_projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self.segmentation, self.surveys = segmentation, surveys
        self.photo_runner = photo_runner or self._photo
        self.geo_runner = geo_runner or self._geo
        self.composer = composer or self._compose
        self.lock = RLock()
        self.queue = Queue()
        self.uploading = set()
        self.worker = None
        self.recover()

    def _load(self, pid):
        if not re.fullmatch(ID, pid):
            raise FileNotFoundError("Ruang kerja tidak ditemukan.")
        return read_json(self.root / pid / "project.json")

    def _save(self, project):
        project["updated_at"] = now()
        dest = self.root / project["id"] / "project.json"
        pending = dest.with_suffix(".tmp")
        pending.write_text(json.dumps(project, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(pending, dest)

    def recover(self):
        for path in self.root.glob("*/project.json"):
            try:
                p = read_json(path)
                if p["status"] in ACTIVE:
                    for f in p["files"]:
                        if f["status"] == "processing":
                            f["status"] = "uploaded"
                    p.update(status="paused", phase="Proses sebelumnya terhenti. Klik Lanjutkan untuk memakai hasil yang sudah selesai.", pause_requested=False)
                    self._save(p)
            except (OSError, ValueError, KeyError):
                LOG.warning("Tidak dapat membaca satu proyek lokal: %s", path.parent.name)

    @staticmethod
    def _public(p, *, detail=True):
        counts = Counter(f["status"] for f in p["files"])
        fields = {key: p[key] for key in ("id", "name", "status", "created_at", "updated_at", "phase", "current", "map_error")}
        fields.update(total=len(p["files"]), uploaded=len(p["files"])-counts["waiting"], waiting=counts["waiting"],
                      succeeded=counts["done"], failed=counts["error"], duplicates=counts["duplicate"],
                      areas=len(p.get("areas", [])), processed=counts["done"]+counts["error"]+counts["duplicate"])
        if detail:
            fields["files"] = [{k: f.get(k) for k in ("id", "name", "size", "kind", "status", "error", "trees", "masks")} for f in p["files"]]
        return fields

    def list(self):
        with self.lock:
            output=[]
            for path in self.root.glob("*/project.json"):
                try:output.append(self._public(read_json(path),detail=False))
                except (OSError, ValueError, KeyError):continue
            return sorted(output,key=lambda p:p["updated_at"],reverse=True)

    def get(self,pid):
        with self.lock:return self._public(self._load(pid))

    def result(self,pid):
        with self.lock:
            p=self._load(pid)
            return {"areas":p.get("areas",[]),"note":"Hasil tiap area tidak dijumlahkan sebagai sensus unik.","map_error":p["map_error"]}

    def create(self, payload):
        if not isinstance(payload,dict):raise ValueError("Data proyek tidak valid.")
        descriptors=payload.get("files")
        if not isinstance(descriptors,list) or not 1<=len(descriptors)<=300:raise ValueError("Pilih 1–300 berkas.")
        slots=[]
        for item in descriptors:
            if not isinstance(item,dict):raise ValueError("Data berkas tidak valid.")
            name=clean_name(item.get("name")); size=item.get("size")
            suffix=Path(name).suffix.lower();kind="geotiff" if suffix in {".tif",".tiff"} else "photo"
            if suffix not in {".jpg",".jpeg",".png",".tif",".tiff"} or type(size) is not int or not 0<size<=(MAX_SURVEY_BYTES if kind=="geotiff" else MAX_UPLOAD):
                raise ValueError(f"Format atau ukuran {name} tidak didukung.")
            slots.append({"id":uuid4().hex,"name":name,"size":size,"kind":kind,"status":"waiting","error":None})
        pid=uuid4().hex; label=payload.get("name") or "Pemetaan baru"
        if not isinstance(label,str):raise ValueError("Nama pekerjaan tidak valid.")
        p={"id":pid,"name":label.strip()[:80] or "Pemetaan baru","status":"uploading","created_at":now(),"updated_at":now(),
           "phase":"Menunggu unggahan","current":None,"map_error":None,"pause_requested":False,"files":slots,"areas":[]}
        with self.lock:
            (self.root/pid/"inputs").mkdir(parents=True);self._save(p)
        return self._public(p)

    def upload(self,pid,fid,length,stream):
        if not re.fullmatch(ID,fid):raise FileNotFoundError("Berkas tidak ditemukan.")
        key=(pid,fid)
        with self.lock:
            p=self._load(pid);f=next((x for x in p["files"] if x["id"]==fid),None)
            if not f:raise FileNotFoundError("Berkas tidak ditemukan.")
            if p["status"] in ACTIVE:raise ValueError("Pekerjaan sedang berjalan.")
            if f["status"]!="waiting":return self._public(p)
            if key in self.uploading:raise ValueError("Unggahan sedang berlangsung.")
            if length!=f["size"]:raise ValueError("Ukuran berkas berbeda dari pilihan awal.")
            folder=self.root/pid/"inputs";dest=folder/fid
            # Keep ample storage for results; actual decoder checks additional space.
            if shutil.disk_usage(folder).free<length+64*1024**2:raise ValueError("Ruang disk tidak cukup.")
            self.uploading.add(key)
        pending=dest.with_suffix(".part");digest=hashlib.sha256()
        try:
            remaining=length
            with pending.open("wb") as out:
                while remaining:
                    block=stream.read(min(1024**2,remaining))
                    if not block:raise ValueError("Unggahan terputus. Pilih ulang berkas ini.")
                    out.write(block);digest.update(block);remaining-=len(block)
            os.replace(pending,dest)
            with self.lock:
                p=self._load(pid);f=next(x for x in p["files"] if x["id"]==fid)
                f.update(status="uploaded",sha256=digest.hexdigest(),error=None)
                p.update(status="ready" if all(x["status"]!="waiting" for x in p["files"]) else "uploading",phase="Berkas tersimpan di komputer ini")
                self._save(p);return self._public(p)
        finally:
            pending.unlink(missing_ok=True)
            with self.lock:self.uploading.discard(key)

    def start(self,pid,*,retry=False):
        with self.lock:
            p=self._load(pid)
            if p["status"] in ACTIVE:return self._public(p)
            if any(k[0]==pid for k in self.uploading):raise ValueError("Tunggu unggahan yang sedang berjalan.")
            if any(f["status"]=="waiting" for f in p["files"]):raise ValueError("Masih ada berkas yang belum diunggah. Pilih ulang berkas yang kurang.")
            if retry:
                for f in p["files"]:
                    if f["status"]=="error":f.update(status="uploaded",error=None)
            if not any(f["status"] in {"uploaded","done","duplicate"} for f in p["files"]):raise ValueError("Belum ada berkas yang bisa diproses. Coba ulang yang gagal.")
            if p["status"]=="done" and not retry:return self._public(p)
            p.update(status="queued",phase="Menunggu giliran analisis",pause_requested=False,current=None,map_error=None)
            self._save(p)
            if self.worker is None or not self.worker.is_alive():
                self.worker=Thread(target=self._loop,name="workspace-projects",daemon=True);self.worker.start()
            self.queue.put(pid);return self._public(p)

    def pause(self,pid):
        with self.lock:
            p=self._load(pid)
            if p["status"]=="queued":p.update(status="paused",phase="Dijeda",pause_requested=False)
            elif p["status"] in {"running","mapping"}:p.update(status="pausing",phase="Menjeda setelah tahap saat ini selesai",pause_requested=True)
            self._save(p);return self._public(p)

    def _loop(self):
        while True:
            pid=self.queue.get()
            try:self._run(pid)
            except Exception:
                LOG.exception("Pekerjaan lokal gagal: %s",pid)
                with self.lock:
                    p=self._load(pid);p.update(status="paused",phase="Pekerjaan terhenti. Klik Lanjutkan untuk mencoba kembali.",current=None)
                    for f in p["files"]:
                        if f["status"]=="processing":f["status"]="uploaded"
                    self._save(p)
            finally:self.queue.task_done()

    def _phase(self,pid,phase):
        with self.lock:
            p=self._load(pid)
            if not p["pause_requested"]:p["phase"]=phase;self._save(p)

    def _photo(self,source,name,progress):
        return self.segmentation.analyze_bytes(source.read_bytes(),name,"workspace")

    def _geo(self,source,name,progress):
        if not self.surveys or not self.surveys.health()["ready"]:raise ValueError("Pembaca GeoTIFF belum siap.")
        with source.open('rb') as data:
            if data.read(4) not in (b'II*\x00',b'MM\x00*',b'II+\x00',b'MM\x00+'):
                raise ValueError("Berkas bukan TIFF/GeoTIFF yang valid.")
        folder,detail=self.surveys.create(name)
        try:os.link(source,folder/"input.tif")
        except OSError:shutil.copyfile(source,folder/"input.tif")
        with self.surveys.lock:self.surveys.active.add(detail["id"])
        self.surveys.work(folder,detail,{"segment":True},on_progress=progress)
        info=self.surveys.load(folder)
        if info["status"]!="done":raise ValueError(info.get("detail") or "Peta GeoTIFF gagal diproses.")
        s=read_json(folder/"result"/"summary.json");root=f"/api/surveys/{detail['id']}"
        area=sum(b.get("area_ha",0) for b in s["blocks"]);covered=sum(b.get("covered_ha",0) for b in s["blocks"])
        return {"id":detail["id"],"mode":"geotiff","source_files":[name],"images":1,"width":s["width"],"height":s["height"],
                "displayed_candidates":s["points"],"displayed_masks":s["crowns"],"visual_counts":s["visual_counts"],"covered_ha":covered,"area_ha":area,
                "relative_coverage_pct":100*covered/area if area else None,"details_url":root+"/map.json","crs":s["crs"],
                "files":{"original.jpg":root+"/preview.png","summary.json":root+"/summary.json","trees.geojson":root+"/trees.geojson","crowns.geojson":root+"/crowns.geojson","crowntrace.gpkg":root+"/crowntrace.gpkg"},
                "note":"Posisi menggunakan georeferensi pada berkas. Validasi koordinat dan jumlah masih diperlukan; contoh MOPAD memakai koordinat demonstrasi."}

    def _compose(self,ids):
        with self.segmentation.lock:return compose_workspace(self.segmentation.root,ids)

    def _run(self,pid):
        with self.lock:
            p=self._load(pid)
            if p["status"]!="queued":return
            p.update(status="running",phase="Mulai analisis",current=None);self._save(p)
        while True:
            with self.lock:
                p=self._load(pid)
                if p["pause_requested"]:
                    p.update(status="paused",phase="Dijeda. Hasil yang selesai sudah tersimpan.",current=None,pause_requested=False);self._save(p);return
                f=next((f for f in p["files"] if f["status"]=="uploaded"),None)
                if f is None:break
                previous=next((x for x in p["files"] if x["status"]=="done" and x.get("sha256")==f.get("sha256") and x["kind"]==f["kind"]),None)
                if previous:
                    f.update(status="duplicate",duplicate_of=previous["id"]);self._save(p);continue
                f.update(status="processing",error=None);p.update(current=f["name"],phase="Menganalisis foto" if f["kind"]=="photo" else "Memetakan GeoTIFF");self._save(p)
                fid,kind,name=f["id"],f["kind"],f["name"]
            try:
                fn=self.photo_runner if kind=="photo" else self.geo_runner
                result=fn(self.root/pid/"inputs"/fid,name,lambda phase,*counts:self._phase(pid,f"{phase}"+(f" ({counts[0]}/{counts[1]})" if len(counts)>1 and counts[1] else "")))
                updates={"status":"done","result":result,"trees":result.get("trees",result.get("displayed_candidates")),"masks":result.get("crowns",result.get("displayed_masks")),"error":None}
            except Exception as error:
                LOG.warning("Berkas analisis gagal (%s): %s",fid,type(error).__name__)
                detail=str(error) if isinstance(error,ValueError) else "Citra tidak dapat dibaca atau diproses. Periksa format dan bobot model."
                updates={"status":"error","error":detail[:300]}
            with self.lock:
                p=self._load(pid);next(x for x in p["files"] if x["id"]==fid).update(updates);p["current"]=None;self._save(p)
        with self.lock:
            p=self._load(pid);done=[x for x in p["files"] if x["status"]=="done"]
            p.update(status="mapping",phase="Mencocokkan foto dan membuat peta",current=None);self._save(p)
        photos=[f["result"] for f in done if f["kind"]=="photo"];areas=[];map_error=None
        if photos:
            try:
                areas=self.composer([r["id"] for r in photos])["areas"]
                for a in areas:
                    # Persist compact references; the map details are fetched only when opened.
                    a.pop("objects",None);a.pop("tiles",None);a["details_url"]=a["files"]["summary.json"]
            except Exception as error:
                LOG.exception("Gagal menyusun peta proyek %s",pid)
                map_error="Peta gabungan belum selesai. Hasil foto individual tersedia; klik Coba ulang untuk menyusun ulang."
                for r in photos:
                    areas.append({"mode":"fallback","source_files":[r["source"]],"images":1,"width":r["width"],"height":r["height"],"displayed_candidates":r["trees"],"displayed_masks":r["crowns"],"visual_counts":r["counts"],"files":{"original.jpg":r["files"]["original.png"],"overlay.jpg":r["files"]["overlay.png"],"summary.json":r["files"]["summary.json"]},"note":"Hasil individu; belum diselaraskan dengan foto lain."})
        areas.extend(f["result"] for f in done if f["kind"]=="geotiff")
        for i,a in enumerate(areas,1):a["name"]=f"Area {i}"
        with self.lock:
            p=self._load(pid);failed=any(f["status"]=="error" for f in p["files"])
            p.update(areas=areas,map_error=map_error,status="partial" if failed or map_error else "done",phase="Hasil tersimpan" if areas else "Belum ada berkas yang berhasil",current=None,pause_requested=False);self._save(p)

    @staticmethod
    def body(handler):
        if handler.headers.get_content_type()!="application/json":raise ValueError("Kirim data JSON.")
        length=int(handler.headers.get("Content-Length","0"))
        if not 0<length<=262144:raise ValueError("Data pekerjaan terlalu besar.")
        handler.connection.settimeout(30);data=handler.rfile.read(length)
        if len(data)!=length:raise ValueError("Data belum lengkap.")
        return json.loads(data)

    def http(self,handler,path,method):
        try:
            if path=="/api/projects":
                if method=="GET":handler.send_json(200,{"projects":self.list()})
                else:handler.send_json(201,self.create(self.body(handler)))
                return
            match=re.fullmatch(rf"/api/projects/({ID})(?:/(result|start|pause|retry|files/({ID})))?",path)
            if not match:raise FileNotFoundError("Pekerjaan tidak ditemukan.")
            pid,action,fid=match.groups()
            if method=="GET" and action in {None,"result"}:
                handler.send_json(200,self.result(pid) if action else self.get(pid));return
            if method=="POST":
                if fid:
                    length=int(handler.headers.get("Content-Length","0"));handler.connection.settimeout(90)
                    handler.send_json(200,self.upload(pid,fid,length,handler.rfile));return
                if action in {"start","retry","pause"}:
                    handler.send_json(202,self.pause(pid) if action=="pause" else self.start(pid,retry=action=="retry"));return
            raise FileNotFoundError("Tindakan tidak ditemukan.")
        except FileNotFoundError:handler.send_json(404,{"detail":"Pekerjaan atau berkas tidak ditemukan."})
        except (ValueError,TypeError,TimeoutError,OSError) as e:handler.send_json(422,{"detail":str(e) if isinstance(e,ValueError) else "Operasi lokal belum berhasil. Coba lagi."})
