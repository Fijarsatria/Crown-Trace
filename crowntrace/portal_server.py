"""CrownTrace managed-service portal server. Local pilot only (loopback HTTP)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from getpass import getpass
from hashlib import sha256, file_digest
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import mimetypes
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, quote, urlsplit
import webbrowser

from PIL import Image, UnidentifiedImageError

from .dataset import ROOT
from .portal_engine import (MappingEngine, SERVICES, DATA_PLANS, PROFILES, FILES, PUBLIC_SAMPLE, public_sample_report)
from .portal_quality import review_state, edit as quality_edit, export_csv, export_geojson
from .portal_operations import inbox, candidates, compare, billing, billing_html
from .portal_store import (PortalStore, Problem, SESSION_SECONDS, MAX_UPLOADS, STATUS, today, string)

LOG = logging.getLogger(__name__)
STATIC = ROOT / "web/portal"
ID = r"[a-f0-9]{32}"
ASSETS = {
    "/portal.css": ("portal.css", "text/css; charset=utf-8"),
    "/portal.js": ("portal.js", "text/javascript; charset=utf-8"),
    "/portal-ops.js": ("portal-ops.js", "text/javascript; charset=utf-8"),
    "/portal-map.js": ("portal-map.js", "text/javascript; charset=utf-8"),
    "/assets/brand.svg": ("assets/brand.svg","image/svg+xml"),
    "/assets/atlas.webp": ("assets/atlas.webp","image/webp"),
    "/assets/atlas-crowns.png": ("assets/atlas-crowns.png","image/png"),
    "/assets/field.webp": ("assets/field.webp","image/webp"),
    "/assets/dm-sans.woff2": ("assets/dm-sans.woff2","font/woff2"),
    "/assets/plus-jakarta-sans.woff2": ("assets/plus-jakarta-sans.woff2","font/woff2"),
}
PAGES = re.compile(r"/(?:masuk|daftar|pesan|contoh|app(?:/(?:orders|results|staff|account|new|monitoring|updates|order/"+ID+r"(?:/result/"+ID+r")?))?)?$")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; "
       "base-uri 'none'; frame-ancestors 'none'; object-src 'none'; form-action 'self'")


class JobQueue:
    def __init__(self,store,engine):
        self.store,self.engine=store,engine
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="mapping")

    def enqueue(self,jid):
        self.pool.submit(self.run,jid)

    def run(self,jid):
        try:
            context=self.store.job_begin(jid)
            if context is None:return
            job,source,boundary=context
            paths=[]
            for row in (source,boundary):
                if row:
                    path=self.store.file_path(row)
                    with path.open("rb") as f:
                        if file_digest(f,"sha256").hexdigest()!=row["sha256"]:
                            raise ValueError("Integritas sumber berubah; unggah kembali dan validasi.")
                    paths.append(path)
                else:paths.append(None)
            output=self.store.results/jid
            report=self.engine.run(job["profile"],paths[0],paths[1],output,
                                   lambda phase,n,total:self.store.job_progress(jid,phase,n,total),
                                   example=bool(job["example"]))
            if report["profile"]!=job["profile"] or not (output/"report.json").is_file() or not (output/"map.json").is_file():
                raise ValueError("Paket hasil tidak lengkap.")
            with (output/"report.json").open(encoding="utf-8") as f:
                recorded=json.load(f)
            for file in recorded["files"]:
                known=FILES.get(file["name"])
                if not known or not (output/file["name"]).is_file() or (output/file["name"]).stat().st_size!=file["bytes"]:
                    raise ValueError("Katalog hasil tidak lengkap.")
            self.store.job_end(jid)
        except Exception as err:
            LOG.exception("Mapping job %s failed",jid)
            detail=str(err)[:500] if isinstance(err,ValueError) else "Analisis tidak dapat diselesaikan. Periksa sumber, model, dan ruang penyimpanan."
            self.store.job_end(jid,error=detail)

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=True)


def validate_boundary(path):
    try:
        d=json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError,ValueError):
        raise Problem(422,"Batas blok harus GeoJSON yang valid.")
    if not isinstance(d,dict) or d.get("type")!="FeatureCollection" or d.get("crs") or not isinstance(d.get("features"),list) or not 1<=len(d["features"])<=500:
        raise Problem(422,"Gunakan FeatureCollection WGS84 dengan 1–500 poligon blok.")
    def rings(g):
        if not isinstance(g,dict):raise ValueError()
        if g.get("type")=="Polygon":return [g["coordinates"]]
        if g.get("type")=="MultiPolygon":return g["coordinates"]
        raise ValueError()
    try:
        n=0
        for feature in d["features"]:
            if feature["type"]!="Feature":raise ValueError()
            for polygon in rings(feature["geometry"]):
                if not polygon:raise ValueError()
                for ring in polygon:
                    if len(ring)<4 or ring[0]!=ring[-1]:raise ValueError()
                    for xy in ring:
                        n+=1
                        if len(xy)<2 or type(xy[0]) not in (float,int) or type(xy[1]) not in (float,int) or not (-180<=xy[0]<=180 and -90<=xy[1]<=90) or n>100000:
                            raise ValueError()
    except (TypeError,ValueError,KeyError,IndexError):
        raise Problem(422,"Geometri harus poligon tertutup dalam longitude/latitude WGS84.")
    return "application/geo+json"


def upload_mime(path,kind,order,size):
    if kind=="boundary":
        return validate_boundary(path)
    with path.open("rb") as f:head=f.read(12)
    tiff=head[:4] in (b"II*\x00",b"MM\x00*",b"II+\x00",b"MM\x00+")
    if kind=="payment_proof" and head.startswith(b"%PDF-"):
        return "application/pdf"
    if kind=="source" and tiff:
        if order["service"]=="inspection" or order["data_plan"]=="tile":
            raise Problem(422,"Pesanan ini memerlukan tile RGB 640 × 640.")
        return "image/tiff"
    if kind=="source" and order["data_plan"]!="tile":
        raise Problem(422,"Pesanan ini memerlukan orthomosaic GeoTIFF yang telah dipersiapkan.")
    if kind=="source" and size>(5*1024**2 if order["service"]=="inspection" else 12*1024**2):
        raise Problem(413,"Ukuran tile melebihi batas layanan.")
    try:
        with Image.open(path) as photo:
            if photo.format not in {"JPEG","PNG"}:
                raise ValueError()
            if kind=="source" and (photo.size!=(640,640) or photo.mode not in {"RGB","RGBA"}):
                raise Problem(422,"Gunakan tile warna RGB 640 × 640.")
            if photo.width*photo.height>20_000_000:
                raise ValueError()
            mime={"JPEG":"image/jpeg","PNG":"image/png"}[photo.format]
            photo.verify()
            return mime
    except Problem:
        raise
    except (UnidentifiedImageError,Image.DecompressionBombError,OSError,ValueError):
        raise Problem(422,"Berkas bukan JPEG/PNG yang valid"+(" atau PDF." if kind=="payment_proof" else "."))


def safe_name(name):
    name=name.replace("\\","/").split("/")[-1]
    name="".join(ch for ch in name if ch.isprintable() and ch not in "\r\n")[:160].strip()
    return name if name and name not in {".",".."} else "berkas"


def make_handler(store,engine,queue):
    cookie_name="ct_session_demo" if store.demo else "ct_session_local"
    attempts={};attempt_lock=Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version="CrownTrace"
        sys_version=""

        def log_message(self,fmt,*args):
            LOG.info("%s %s",self.client_address[0],fmt%args)

        def _host(self):
            host=self.headers.get("Host","")
            allowed={f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"}
            if host.lower() not in allowed:
                raise Problem(403,"Gunakan alamat localhost yang ditampilkan server.")
            return host

        def headers_common(self,content_type,*,cache=False):
            self.send_header("Content-Type",content_type)
            self.send_header("Cache-Control","private, max-age=86400" if cache else "no-store")
            self.send_header("Content-Security-Policy",CSP)
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("X-Frame-Options","DENY")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Permissions-Policy","camera=(), microphone=(), geolocation=()")

        def json_response(self,status,body,*,cookie=None):
            data=json.dumps(body,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode("utf-8")
            self.send_response(status);self.headers_common("application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(data)))
            if cookie is not None:self.send_header("Set-Cookie",cookie)
            self.end_headers();self.wfile.write(data)

        def send_file(self,path,mime,*,download=None,cache=False):
            if not path.is_file():raise Problem(404,"Berkas tidak ditemukan.")
            with path.open("rb") as stream:
                self.send_response(200);self.headers_common(mime,cache=cache)
                self.send_header("Content-Length",str(path.stat().st_size))
                if download:
                    self.send_header("Content-Disposition","attachment; filename*=UTF-8''"+quote(download,safe=""))
                self.end_headers()
                shutil.copyfileobj(stream,self.wfile,1024**2)

        def token(self):
            try:
                cookies=SimpleCookie();cookies.load(self.headers.get("Cookie",""))
                return cookies[cookie_name].value if cookie_name in cookies else None
            except Exception:return None

        def actor(self,*,write=False):
            session=store.session(self.token())
            if not session:raise Problem(401,"Silakan masuk terlebih dahulu.")
            if write and not hmac.compare_digest(self.headers.get("X-CSRF-Token",""),session["csrf"]):
                raise Problem(403,"Sesi formulir tidak valid. Muat ulang halaman.")
            return session["user"]

        def origin(self):
            host=self._host()
            origin=self.headers.get("Origin")
            if origin and origin!=f"http://{host}":
                raise Problem(403,"Permintaan harus berasal dari portal lokal.")
            if self.headers.get("Sec-Fetch-Site")=="cross-site":
                raise Problem(403,"Permintaan lintas situs tidak diizinkan.")

        def body(self):
            if self.headers.get_content_type()!="application/json":raise Problem(415,"Kirim data JSON.")
            try:size=int(self.headers.get("Content-Length","0"))
            except ValueError:raise Problem(400,"Ukuran permintaan tidak valid.")
            if not 0<size<=65536:raise Problem(413,"Permintaan terlalu besar atau kosong.")
            self.connection.settimeout(20);raw=self.rfile.read(size)
            if len(raw)!=size:raise Problem(400,"Permintaan belum lengkap.")
            try:doc=json.loads(raw)
            except (UnicodeError,ValueError):raise Problem(422,"Data JSON tidak valid.")
            if not isinstance(doc,dict):raise Problem(422,"Data JSON harus objek.")
            return doc

        def set_session(self,token):
            return f"{cookie_name}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict"

        def clear_session(self):
            return f"{cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"

        def send_upload(self,actor,oid,query):
            kind=query.get("kind",[""])[0];version=query.get("version",[""])[0]
            order=store.upload_allowed(actor,oid,kind,version)
            limit=MAX_UPLOADS[kind]
            if kind=="source" and order["data_plan"]=="tile":limit=min(limit,5*1024**2 if order["service"]=="inspection" else 12*1024**2)
            try:size=int(self.headers.get("Content-Length","0"))
            except ValueError:raise Problem(400,"Ukuran berkas tidak valid.")
            if not 0<size<=limit:raise Problem(413,"Berkas kosong atau melebihi batas.")
            if shutil.disk_usage(store.root).free<size+64*1024**2:raise Problem(507,"Ruang penyimpanan tidak cukup.")
            name=safe_name(query.get("name",["berkas"])[0])
            digest=sha256();remaining=size;staging=None
            self.connection.settimeout(60)
            try:
                with NamedTemporaryFile(dir=store.staging,suffix=".part",delete=False) as f:
                    staging=Path(f.name)
                    while remaining:
                        chunk=self.rfile.read(min(remaining,1024**2))
                        if not chunk:raise Problem(400,"Unggahan terputus.")
                        f.write(chunk);digest.update(chunk);remaining-=len(chunk)
                mime=upload_mime(staging,kind,order,size)
                detail=store.add_upload(actor,oid,kind,name,mime,staging,size,digest.hexdigest(),version)
                self.json_response(201,detail)
            finally:
                if staging is not None and staging.is_file():staging.unlink()

        def do_GET(self):
            try:
                self._host()
                url=urlsplit(self.path);path=url.path;query=parse_qs(url.query)
                if path in ASSETS:
                    filename,mime=ASSETS[path];return self.send_file(STATIC/filename,mime,cache=not path.endswith((".css",".js")))
                if path=="/favicon.ico":
                    return self.send_file(STATIC/"assets/brand.svg","image/svg+xml",cache=True)
                if path=="/api/bootstrap":
                    session=store.session(self.token())
                    return self.json_response(200,{"demo":store.demo,"portal_release":"20261127-public","today":today().isoformat(),"user":session["user"] if session else None,
                        "csrf":session["csrf"] if session else None,"services":SERVICES,"data_plans":DATA_PLANS,"profiles":PROFILES,
                        "status":STATUS,"models":engine.available(),"example_ready":(PUBLIC_SAMPLE/"summary.json").is_file(),
                        "demo_role_buttons":bool(store.demo)})
                if path=="/api/example/report":
                    return self.json_response(200,public_sample_report())
                example=re.fullmatch(r"/api/example/files/([a-z0-9_.-]+)",path)
                if example:
                    name=example[1]
                    if name not in FILES or name=="report.json":raise Problem(404,"Contoh berkas tidak ditemukan.")
                    return self.send_file(PUBLIC_SAMPLE/name,FILES[name][1],download=name if query.get("download")==["1"] else None)
                if path=="/api/updates":return self.json_response(200,inbox(store,self.actor()))
                if path=="/api/monitoring/candidates":return self.json_response(200,candidates(store,self.actor()))
                invoice=re.fullmatch(r"/api/orders/("+ID+r")/invoice",path)
                if invoice:
                    info=billing(store,self.actor(),invoice[1])
                    data=billing_html(info)
                    self.send_response(200);self.headers_common("text/html; charset=utf-8")
                    self.send_header("Content-Disposition",'attachment; filename="crowntrace-tagihan.html"')
                    self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data);return
                if path=="/api/orders":
                    actor=self.actor()
                    return self.json_response(200,{"orders":store.list_orders(actor)})
                if path=="/api/staff":
                    actor=self.actor()
                    if actor["role"]!="admin":raise Problem(403,"Akses ini hanya untuk admin.")
                    return self.json_response(200,{"staff":store.staff(actor)})
                match=re.fullmatch(r"/api/orders/("+ID+r")",path)
                if match:
                    return self.json_response(200,store.detail(self.actor(),match[1]))
                match=re.fullmatch(r"/api/orders/("+ID+r")/uploads/("+ID+r")",path)
                if match:
                    file,row=store.authorize_file(self.actor(),*match.groups())
                    return self.send_file(file,row["mime"],download=row["name"])
                qc=re.fullmatch(r"/api/orders/("+ID+r")/results/("+ID+r")/quality(?:/(csv|json|geojson))?",path)
                if qc:
                    oid,jid,fmt=qc.groups()
                    info=review_state(store,self.actor(),oid,jid,include_rows=bool(fmt))
                    if fmt=="csv":
                        data=export_csv(info)
                        self.send_response(200);self.headers_common("text/csv; charset=utf-8")
                        self.send_header("Content-Disposition",'attachment; filename="reviewed-inventory.csv"')
                        self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data);return
                    if fmt in ("json","geojson"):
                        data=json.dumps(info if fmt=="json" else export_geojson(info),ensure_ascii=False,allow_nan=False).encode("utf-8")
                        self.send_response(200);self.headers_common("application/geo+json" if fmt=="geojson" else "application/json; charset=utf-8")
                        self.send_header("Content-Disposition",f'attachment; filename="reviewed-inventory.{fmt}"')
                        self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data);return
                    return self.json_response(200,info)
                match=re.fullmatch(r"/api/orders/("+ID+r")/results/("+ID+r")(?:/files/([a-z0-9_.-]+))?",path)
                if match:
                    oid,jid,name=match.groups();folder,pub,job=store.authorize_result(self.actor(),oid,jid)
                    report=json.loads((folder/"report.json").read_text(encoding="utf-8"))
                    if name is None:
                        return self.json_response(200,{"report":report,"publication":pub,"job":job})
                    allowed={f["name"] for f in report["files"]}|{"report.json"}
                    if name not in allowed or name not in FILES:raise Problem(404,"Berkas hasil tidak tersedia.")
                    return self.send_file(folder/name,FILES[name][1],download=name if query.get("download")==["1"] else None)
                if PAGES.fullmatch(path):
                    return self.send_file(STATIC/"index.html","text/html; charset=utf-8")
                raise Problem(404,"Halaman tidak ditemukan.")
            except Problem as error:self.json_response(error.status,{"detail":error.detail})
            except FileNotFoundError:self.json_response(404,{"detail":"Berkas belum tersedia."})
            except (BrokenPipeError,ConnectionResetError):return
            except Exception:
                LOG.exception("Request failed");self.json_response(500,{"detail":"Permintaan tidak dapat diproses."})

        def do_POST(self):
            try:
                self.origin();url=urlsplit(self.path);path=url.path;query=parse_qs(url.query)
                if path=="/api/demo-login":
                    if not store.demo:raise Problem(404,"Peran demo tidak tersedia.")
                    token,csrf,user=store.demo_sign_in(self.body().get("role"))
                    return self.json_response(200,{"user":user,"csrf":csrf},cookie=self.set_session(token))
                if path=="/api/login":
                    body=self.body()
                    key=(self.client_address[0],str(body.get("email","")).lower()[:254])
                    with attempt_lock:
                        stamp=monotonic()
                        for other,record in list(attempts.items()):
                            if stamp-record[1]>600:attempts.pop(other,None)
                        count,_=attempts.get(key,(0,stamp))
                        if count>=8:raise Problem(429,"Terlalu banyak percobaan. Coba lagi dalam beberapa menit.")
                    try:token,csrf,user=store.sign_in(body.get("email"),body.get("password"))
                    except Problem:
                        with attempt_lock:attempts[key]=(count+1,stamp)
                        raise
                    with attempt_lock:attempts.pop(key,None)
                    return self.json_response(200,{"user":user,"csrf":csrf},cookie=self.set_session(token))
                if path=="/api/register":
                    body=self.body();user=store.register(body)
                    token,csrf,user=store.sign_in(user["email"],body["password"])
                    return self.json_response(201,{"user":user,"csrf":csrf},cookie=self.set_session(token))
                actor=self.actor(write=True)
                if path=="/api/updates/read":return self.json_response(200,inbox(store,actor,mark=True))
                if path=="/api/monitoring/compare":return self.json_response(200,compare(store,actor,self.body()))
                if path=="/api/logout":
                    store.sign_out(self.token())
                    return self.json_response(200,{"ok":True},cookie=self.clear_session())
                if path=="/api/account/password":
                    store.password_change(actor,self.body())
                    return self.json_response(200,{"ok":True},cookie=self.clear_session())
                if path=="/api/orders":
                    return self.json_response(201,store.create_order(actor,self.body()))
                if path=="/api/staff":
                    return self.json_response(201,store.create_operator(actor,self.body()))
                qc=re.fullmatch(r"/api/orders/("+ID+r")/results/("+ID+r")/quality",path)
                if qc:return self.json_response(200,quality_edit(store,actor,*qc.groups(),self.body()))
                match=re.fullmatch(r"/api/orders/("+ID+r")/files",path)
                if match:return self.send_upload(actor,match[1],query)
                match=re.fullmatch(r"/api/orders/("+ID+r")/actions/([a-z_]+)",path)
                if match:
                    detail,job=store.action(actor,match[1],match[2],self.body(),engine)
                    if job:queue.enqueue(job)
                    return self.json_response(200,detail)
                raise Problem(404,"Tindakan tidak ditemukan.")
            except Problem as error:self.json_response(error.status,{"detail":error.detail})
            except (TimeoutError,ConnectionResetError,BrokenPipeError):
                LOG.warning("Connection ended while processing request")
            except Exception:
                LOG.exception("Action failed");self.json_response(500,{"detail":"Tindakan tidak dapat diproses. Muat ulang dan coba kembali."})

    return Handler


@contextmanager
def exclusive_workspace(directory):
    """One server process per database; releases the OS lock on exit."""
    lock=directory/"server.lock"
    handle=lock.open("a+b")
    try:
        handle.seek(0,2)
        if handle.tell()==0:handle.write(b"\0");handle.flush()
        handle.seek(0)
        if __import__("os").name=="nt":
            import msvcrt
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ValueError("Workspace sudah dibuka oleh server CrownTrace lain.")
        else:
            import fcntl
            try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise ValueError("Workspace sudah dibuka oleh server CrownTrace lain.")
        yield
    finally:handle.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group()
    mode.add_argument("--demo",action="store_true")
    mode.add_argument("--operational",action="store_true")
    p.add_argument("--port",type=int,default=8765)
    p.add_argument("--store",type=Path)
    p.add_argument("--open-browser",action="store_true")
    p.add_argument("--init-admin",nargs="?",const="",metavar="EMAIL")
    p.add_argument("--admin-name",default="Administrator CrownTrace")
    args=p.parse_args()
    demo=not args.operational
    if args.init_admin is not None and demo:p.error("Inisialisasi admin hanya untuk --operational.")
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    root=args.store or ROOT/"portal_workspace"/("demo" if demo else "operational")
    store=PortalStore(root,demo=demo)
    if args.init_admin is not None:
        email=args.init_admin or input("Email administrator: ").strip()
        password=getpass("Buat kata sandi admin (minimal 10 karakter): ")
        if password!=getpass("Ulangi kata sandi: "):p.error("Kata sandi tidak sama.")
        try:user=store.init_admin(email,password,args.admin_name)
        except Problem as err:p.error(err.detail)
        print("Akun admin lokal dibuat:",user["email"])
        return
    with exclusive_workspace(store.root):
        if demo:store.seed_demo()
        else:
            with store.connect() as db:
                if not db.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone():
                    p.error("Buat admin lokal dengan --operational --init-admin EMAIL terlebih dahulu.")
        store.recover_interrupted()
        engine=MappingEngine();queue=JobQueue(store,engine)
        server=ThreadingHTTPServer(("127.0.0.1",args.port),make_handler(store,engine,queue))
        url=f"http://127.0.0.1:{server.server_port}"
        print("CrownTrace", "DEMO (simulasi)" if demo else "OPERASIONAL LOKAL",url,flush=True)
        if args.open_browser:webbrowser.open(url)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:
            server.server_close();queue.close()


if __name__=="__main__":main()
