"""Database-backed service workflow. All transitions, ownership and result release are server-side."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
import hmac
import json
import logging
import math
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
from time import time
from uuid import uuid4

from .portal_engine import (SERVICES, DATA_PLANS, PROFILES, TILE_SAMPLE, INVENTORY_TILE_SAMPLE, INSPECTION_SAMPLE, GEO_SAMPLE, GEO_BOUNDARY,
                            PUBLIC_SAMPLE, allowed_profiles, clone_report)

LOG = logging.getLogger(__name__)
DEMO_ROLES = ("customer", "admin", "operator")
STATUS = {"submitted":"Permintaan masuk","quoted":"Penawaran tersedia","accepted":"Deal · menunggu pembayaran",
          "payment_review":"Verifikasi pembayaran","paid":"Pembayaran terverifikasi","scheduled":"Siap dikerjakan",
          "processing":"Sedang dianalisis","quality_review":"Pemeriksaan kualitas","ready":"Hasil tersedia",
          "completed":"Selesai","revision_requested":"Revisi diminta","processing_failed":"Analisis perlu diulang",
          "cancelled":"Dibatalkan"}
UNPAID={"submitted","quoted","accepted","payment_review"}
UPLOAD_STATES={"accepted","payment_review","paid","scheduled","processing_failed","revision_requested"}
QA_KEYS=("source_checked","outputs_checked","scope_checked","review_checked")
SESSION_SECONDS=43200
MAX_UPLOADS={"source":2*1024**3,"boundary":4*1024**2,"payment_proof":8*1024**2}

SCHEMA="""
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE COLLATE NOCASE,name TEXT NOT NULL,
 organization TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('customer','admin','operator')),
 password_hash TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) NOT NULL,
 csrf TEXT NOT NULL,expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS demo_roles(role TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,customer_id TEXT REFERENCES users(id) NOT NULL,
 title TEXT NOT NULL,location TEXT NOT NULL,area_ha REAL,service TEXT NOT NULL,data_plan TEXT NOT NULL,
 target_date TEXT,brief TEXT NOT NULL,status TEXT NOT NULL,quote_id TEXT,payment_id TEXT,paid_at TEXT,
 assigned_to TEXT REFERENCES users(id),schedule_date TEXT,schedule_note TEXT,source_file_id TEXT,boundary_file_id TEXT,
 data_review TEXT,profile TEXT,current_job_id TEXT,published_job_id TEXT,revision_count INTEGER NOT NULL DEFAULT 0,
 example INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,order_id TEXT REFERENCES orders(id) NOT NULL,version INTEGER NOT NULL,
 amount INTEGER NOT NULL CHECK(amount>0),scope TEXT NOT NULL,estimated_days INTEGER NOT NULL,expires_on TEXT NOT NULL,
 payment_instruction TEXT NOT NULL,created_by TEXT REFERENCES users(id) NOT NULL,created_at TEXT NOT NULL,
 decision TEXT NOT NULL DEFAULT 'pending',decided_at TEXT,UNIQUE(order_id,version));
CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY,order_id TEXT REFERENCES orders(id) NOT NULL,quote_id TEXT REFERENCES quotes(id) NOT NULL,
 amount INTEGER NOT NULL,reference TEXT NOT NULL,proof_file_id TEXT,submitted_by TEXT REFERENCES users(id) NOT NULL,
 submitted_at TEXT NOT NULL,status TEXT NOT NULL,reviewed_by TEXT,reviewed_at TEXT,review_note TEXT);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,order_id TEXT REFERENCES orders(id) NOT NULL,kind TEXT NOT NULL,
 name TEXT NOT NULL,stored_name TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,
 uploaded_by TEXT REFERENCES users(id) NOT NULL,created_at TEXT NOT NULL,example INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,order_id TEXT REFERENCES orders(id) NOT NULL,attempt INTEGER NOT NULL,
 profile TEXT NOT NULL,source_file_id TEXT REFERENCES files(id),boundary_file_id TEXT REFERENCES files(id),
 status TEXT NOT NULL,phase TEXT NOT NULL,completed INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 1,
 error TEXT,created_by TEXT REFERENCES users(id) NOT NULL,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,
 example INTEGER NOT NULL DEFAULT 0,UNIQUE(order_id,attempt));
CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY,order_id TEXT REFERENCES orders(id) NOT NULL,
 job_id TEXT REFERENCES jobs(id) NOT NULL UNIQUE,version INTEGER NOT NULL,checklist TEXT NOT NULL,note TEXT NOT NULL,
 released_by TEXT REFERENCES users(id) NOT NULL,released_at TEXT NOT NULL,UNIQUE(order_id,version));
CREATE TABLE IF NOT EXISTS timeline(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id TEXT REFERENCES orders(id) NOT NULL,
 actor_id TEXT REFERENCES users(id),action TEXT NOT NULL,label TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',
 visibility TEXT NOT NULL DEFAULT 'client',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,order_id TEXT REFERENCES orders(id) NOT NULL,
 actor_id TEXT REFERENCES users(id) NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS orders_customer ON orders(customer_id,updated_at);
CREATE INDEX IF NOT EXISTS orders_operator ON orders(assigned_to,updated_at);
CREATE INDEX IF NOT EXISTS jobs_order ON jobs(order_id,attempt);
CREATE INDEX IF NOT EXISTS timeline_order ON timeline(order_id,id);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
"""


class Problem(Exception):
    def __init__(self,status,detail):
        self.status,self.detail=status,detail
        super().__init__(detail)


def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def today(): return (datetime.now(timezone.utc)+timedelta(hours=7)).date()
def uid(): return uuid4().hex
def whole(row): return dict(row) if row is not None else None


def string(value,label,low=1,high=500):
    if not isinstance(value,str) or not low<=len(value.strip())<=high or "\x00" in value:
        raise Problem(422,f"{label} harus {low}–{high} karakter.")
    return value.strip()


def email_value(value):
    value=string(value,"Email",3,254).lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",value):
        raise Problem(422,"Masukkan alamat email yang valid.")
    return value


def integer(value,label,low=1,high=10**9):
    if type(value) is int: n=value
    elif isinstance(value,str) and re.fullmatch(r"[0-9]{1,12}",value): n=int(value)
    else: raise Problem(422,f"{label} harus bilangan bulat.")
    if not low<=n<=high: raise Problem(422,f"{label} di luar rentang.")
    return n


def calendar(value,label,*,optional=False,minimum=None):
    if optional and not value: return None
    try:
        parsed=date.fromisoformat(value)
        if parsed.isoformat()!=value: raise ValueError()
    except (ValueError,TypeError): raise Problem(422,f"{label} tidak valid.")
    if minimum and parsed<minimum: raise Problem(422,f"{label} tidak boleh sebelum {minimum.isoformat()}.")
    return parsed.isoformat()


def password_hash(password):
    if not isinstance(password,str) or not 10<=len(password)<=128:
        raise Problem(422,"Kata sandi minimal 10 dan maksimal 128 karakter.")
    salt=secrets.token_bytes(16); rounds=400000
    digest=pbkdf2_hmac("sha256",password.encode(),salt,rounds)
    return "$".join(("pbkdf2_sha256",str(rounds),salt.hex(),digest.hex()))


def check_password(password,encoded):
    if not isinstance(password,str) or len(password)>128: return False
    try:
        name,rounds,salt,digest=encoded.split("$")
        if name!="pbkdf2_sha256": return False
        computed=pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),int(rounds))
        return hmac.compare_digest(computed.hex(),digest)
    except (TypeError,ValueError): return False


def user_public(row):
    return {k:row[k] for k in ("id","email","name","organization","role","created_at")}


class PortalStore:
    def __init__(self,root,*,demo=False):
        self.root=Path(root).resolve();self.demo=demo
        self.root.mkdir(parents=True,exist_ok=True)
        self.uploads=self.root/"files";self.results=self.root/"results";self.staging=self.root/"staging"
        for p in (self.uploads,self.results,self.staging): p.mkdir(exist_ok=True)
        self.database=self.root/"crowntrace.sqlite3"
        with self.connect() as db:
            from .portal_quality import SCHEMA as QUALITY_SCHEMA
            from .portal_operations import SCHEMA as OPS_SCHEMA
            db.executescript(SCHEMA+QUALITY_SCHEMA+OPS_SCHEMA);db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS environment(mode TEXT PRIMARY KEY)")
            stored=db.execute("SELECT mode FROM environment").fetchone()
            mode="demo" if demo else "operational"
            if stored and stored["mode"]!=mode:
                raise ValueError("Database demo dan operasional tidak boleh dipertukarkan.")
            db.execute("INSERT OR IGNORE INTO environment VALUES (?)",(mode,))

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.database,timeout=20,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON");db.execute("PRAGMA busy_timeout=20000")
        try: yield db
        finally: db.close()

    @contextmanager
    def tx(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try: yield db
            except BaseException: db.rollback();raise
            else: db.commit()

    def _add_user(self,db,email,password,name,organization,role):
        email=email_value(email);name=string(name,"Nama",2,100);organization=string(organization,"Organisasi",0,120)
        identifier=uid()
        try:
            db.execute("INSERT INTO users(id,email,name,organization,role,password_hash,created_at) VALUES (?,?,?,?,?,?,?)",
                       (identifier,email,name,organization,role,password_hash(password),now()))
        except sqlite3.IntegrityError: raise Problem(409,"Email ini sudah terdaftar.")
        return user_public(db.execute("SELECT * FROM users WHERE id=?",(identifier,)).fetchone())

    def init_admin(self,email,password,name):
        with self.tx() as db: return self._add_user(db,email,password,name,"CrownTrace","admin")

    def register(self,body):
        with self.tx() as db:
            return self._add_user(db,body.get("email"),body.get("password"),body.get("name"),body.get("organization",""),"customer")

    def sign_in(self,email,password):
        email=email_value(email)
        with self.connect() as db: row=db.execute("SELECT * FROM users WHERE email=? AND active=1",(email,)).fetchone()
        fake="pbkdf2_sha256$400000$"+"2d"*16+"$"+"11"*32
        if not check_password(password,row["password_hash"] if row else fake) or row is None:
            raise Problem(401,"Email atau kata sandi tidak cocok.")
        return self._issue_session(row)

    def _issue_session(self,row):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE expires_at<?",(int(time()),))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)",(sha256(token.encode()).hexdigest(),row["id"],csrf,int(time())+SESSION_SECONDS))
        return token,csrf,user_public(row)

    def demo_sign_in(self, role):
        # Only the explicitly isolated demonstration database can use role buttons.
        # No shared password or real operational account is exposed to the client.
        if not self.demo or role not in DEMO_ROLES:
            raise Problem(404,"Peran demo tidak tersedia.")
        with self.connect() as db:
            row=db.execute("SELECT u.* FROM users u JOIN demo_roles d ON d.user_id=u.id WHERE d.role=? AND u.role=? AND u.active=1",(role,role)).fetchone()
        if row is None:raise Problem(404,"Peran demo belum disiapkan.")
        return self._issue_session(row)

    def session(self,token):
        if not token or len(token)>180: return None
        with self.connect() as db:
            row=db.execute("""SELECT u.*,s.csrf FROM users u JOIN sessions s ON s.user_id=u.id
              WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",(sha256(token.encode()).hexdigest(),int(time()))).fetchone()
        return {"user":user_public(row),"csrf":row["csrf"]} if row else None

    def sign_out(self,token):
        if token:
            with self.tx() as db: db.execute("DELETE FROM sessions WHERE token_hash=?",(sha256(token.encode()).hexdigest(),))

    def password_change(self,actor,body):
        with self.tx() as db:
            row=db.execute("SELECT password_hash FROM users WHERE id=?",(actor["id"],)).fetchone()
            if not check_password(body.get("old_password"),row["password_hash"]):
                raise Problem(422,"Kata sandi saat ini tidak cocok.")
            db.execute("UPDATE users SET password_hash=? WHERE id=?",(password_hash(body.get("password")),actor["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?",(actor["id"],))

    def staff(self,actor):
        if actor["role"] not in {"admin","operator"}: raise Problem(403,"Akses ini hanya untuk tim CrownTrace.")
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,name,email,role,active FROM users WHERE role IN ('admin','operator') ORDER BY name")]

    def create_operator(self,actor,body):
        if actor["role"]!="admin": raise Problem(403,"Hanya admin yang dapat membuat akun operator.")
        with self.tx() as db: return self._add_user(db,body.get("email"),body.get("password"),body.get("name"),"CrownTrace","operator")

    @staticmethod
    def _role_order(db,actor,identifier):
        r=db.execute("SELECT * FROM orders WHERE id=?",(identifier,)).fetchone()
        if not r or (actor["role"]=="customer" and r["customer_id"]!=actor["id"]) or (actor["role"]=="operator" and r["assigned_to"]!=actor["id"]):
            raise Problem(404,"Pesanan tidak ditemukan.")
        return dict(r)

    def _version(self,order,body):
        if integer(body.get("version"),"Versi pesanan")!=order["version"]:
            raise Problem(409,"Pesanan telah diperbarui. Muat ulang detail sebelum melanjutkan.")

    def _event(self,db,oid,actor,action,label,note="",visibility="client"):
        db.execute("INSERT INTO timeline(order_id,actor_id,action,label,note,visibility,created_at) VALUES (?,?,?,?,?,?,?)",
                   (oid,actor,action,label,note,visibility,now()))

    def _update(self,db,oid,**fields):
        fields["updated_at"]=now()
        sql=",".join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE orders SET {sql},version=version+1 WHERE id=?",(*fields.values(),oid))

    def _new_order(self,db,customer,body,*,example=0):
        service=body.get("service");data_plan=body.get("data_plan")
        if service not in SERVICES or data_plan not in DATA_PLANS: raise Problem(422,"Pilih layanan dan jenis data yang tersedia.")
        if service=="inspection" and data_plan!="tile": raise Problem(422,"Inspeksi kondisi pilot hanya menerima tile RGB 640 × 640.")
        area=body.get("area_ha")
        if area in (None,""): area=None
        else:
            try: area=float(area)
            except (TypeError,ValueError): raise Problem(422,"Luas kebun harus angka yang valid.")
            if not math.isfinite(area) or not 0<area<=1e6: raise Problem(422,"Luas estimasi di luar rentang.")
        title=string(body.get("title"),"Nama kebun/proyek",3,110)
        location=string(body.get("location"),"Lokasi",3,180)
        brief=string(body.get("brief",""),"Kebutuhan",0,2000)
        target=calendar(body.get("target_date"),"Target tanggal",optional=True,minimum=today())
        oid=uid();code="CT-"+today().strftime("%y%m")+"-"+oid[:6].upper();stamp=now()
        db.execute("""INSERT INTO orders(id,code,customer_id,title,location,area_ha,service,data_plan,target_date,
         brief,status,example,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (oid,code,customer,title,location,area,service,data_plan,target,brief,"submitted",example,stamp,stamp))
        self._event(db,oid,customer,"submit","Permintaan diajukan","Permintaan masuk untuk ditinjau oleh tim.")
        return oid

    def create_order(self,actor,body):
        if actor["role"]!="customer": raise Problem(403,"Pemesanan dibuat menggunakan akun pelanggan.")
        with self.tx() as db: oid=self._new_order(db,actor["id"],body,example=int(self.demo))
        return self.detail(actor,oid)

    @staticmethod
    def _quote_expired(quote):
        return bool(quote and date.fromisoformat(quote["expires_on"])<today())

    def _permissions(self,db,actor,o,quote=None):
        role,s=actor["role"],o["status"];admin=role=="admin";cust=role=="customer";staff=not cust
        has_source=bool(o["source_file_id"]);reviewed=bool(o["data_review"]); assigned=bool(o["assigned_to"])
        a=[]
        if admin and s in {"submitted","quoted"}: a.append("quote")
        if cust and s=="quoted":
            if not self._quote_expired(quote): a.append("accept_quote")
            a.append("decline_quote")
        if cust and s=="accepted": a.extend(["submit_payment","upload_payment_proof"])
        if admin and s=="payment_review": a.extend(["approve_payment","reject_payment"])
        if s in UNPAID and (admin or cust): a.append("cancel")
        if s in UPLOAD_STATES:
            a.append("upload_source")
            if o["data_plan"]!="tile": a.append("upload_boundary")
        if staff and o["paid_at"] and s in {"paid","scheduled","processing_failed","revision_requested"}:
            if has_source: a.append("validate_data")
            if self.demo and not has_source: a.append("use_sample")
        if admin and o["paid_at"] and reviewed and s in {"paid","scheduled","processing_failed","revision_requested"}: a.append("schedule")
        if staff and o["paid_at"] and reviewed and assigned and o["profile"] and s in {"scheduled","processing_failed"}:
            a.append("start_job")
        if staff and s=="quality_review":
            a.append("rework")
            if admin:a.append("publish")
        if cust and s=="ready": a.extend(["complete","request_revision"])
        if s!="cancelled": a.append("message")
        if o["published_job_id"] or (staff and s=="quality_review"): a.append("view_result")
        return a

    @staticmethod
    def _public_job(job,staff=False):
        keys=["id","attempt","profile","status","phase","completed","total","created_at","started_at","finished_at","example"]
        if staff: keys.append("error")
        return {k:job[k] for k in keys}

    def _summary(self,db,actor,o):
        q=whole(db.execute("SELECT * FROM quotes WHERE id=?",(o["quote_id"],)).fetchone()) if o["quote_id"] else None
        customer=db.execute("SELECT name,organization FROM users WHERE id=?",(o["customer_id"],)).fetchone()
        assigned=db.execute("SELECT name FROM users WHERE id=?",(o["assigned_to"],)).fetchone() if o["assigned_to"] else None
        job=whole(db.execute("SELECT * FROM jobs WHERE id=?",(o["current_job_id"],)).fetchone()) if o["current_job_id"] else None
        out={k:o[k] for k in ("id","code","title","location","area_ha","service","data_plan","status","target_date",
             "schedule_date","version","created_at","updated_at","example","published_job_id","current_job_id","revision_count")}
        out.update(customer=dict(customer),assigned_name=assigned["name"] if assigned else None,
                   service_name=SERVICES[o["service"]]["name"],status_name=STATUS[o["status"]],
                   permissions=self._permissions(db,actor,o,q),paid=bool(o["paid_at"]),
                   job=self._public_job(job,actor["role"]!="customer") if job else None)
        if actor["role"]!="operator": out["amount"]=q["amount"] if q else None
        return out

    def list_orders(self,actor):
        where,args={"admin":("",()),"customer":("WHERE customer_id=?",(actor["id"],)),
                    "operator":("WHERE assigned_to=?",(actor["id"],))}[actor["role"]]
        with self.connect() as db:
            return [self._summary(db,actor,dict(r)) for r in db.execute(f"SELECT * FROM orders {where} ORDER BY updated_at DESC,id DESC",args)]

    @staticmethod
    def _public_file(row):
        return {k:row[k] for k in ("id","kind","name","mime","size","sha256","created_at","example")}

    def detail(self,actor,oid):
        with self.connect() as db:
            o=self._role_order(db,actor,oid);p=self._summary(db,actor,o)
            p.update(brief=o["brief"],assigned_to=o["assigned_to"],schedule_note=o["schedule_note"],paid_at=o["paid_at"],
                     profile=o["profile"],data_review=json.loads(o["data_review"]) if o["data_review"] else None)
            q=whole(db.execute("SELECT * FROM quotes WHERE id=?",(o["quote_id"],)).fetchone()) if o["quote_id"] else None
            payment=whole(db.execute("SELECT * FROM payments WHERE id=?",(o["payment_id"],)).fetchone()) if o["payment_id"] else None
            if actor["role"]!="operator":
                p.update(quote=q,quote_expired=self._quote_expired(q),payment=payment,
                         quote_history=[dict(r) for r in db.execute("SELECT version,amount,scope,estimated_days,expires_on,decision,created_at FROM quotes WHERE order_id=? ORDER BY version DESC",(oid,))])
            p["files"]=[self._public_file(r) for r in db.execute("SELECT * FROM files WHERE order_id=? ORDER BY created_at DESC,id DESC",(oid,))
                        if r["kind"]!="payment_proof" or actor["role"]!="operator"]
            p["source_file_id"]=o["source_file_id"];p["boundary_file_id"]=o["boundary_file_id"]
            p["profiles"]=allowed_profiles(o["service"],p["data_review"]["format"]) if p["data_review"] else []
            where="AND t.visibility='client'" if actor["role"]=="customer" else ""
            if actor["role"]=="operator":
                where="AND t.action NOT IN ('quote','accept_quote','decline_quote','submit_payment','approve_payment','reject_payment','upload_payment_proof')"
            p["timeline"]=[dict(r) for r in db.execute(f"""SELECT t.action,t.label,t.note,t.created_at,
                COALESCE(u.name,'Sistem') AS actor_name FROM timeline t LEFT JOIN users u ON u.id=t.actor_id
                WHERE order_id=? {where} ORDER BY t.id DESC LIMIT 100""",(oid,))]
            p["messages"]=[dict(r) for r in db.execute("""SELECT m.id,m.body,m.created_at,u.name AS actor_name,u.role AS actor_role,m.actor_id
                FROM messages m JOIN users u ON u.id=m.actor_id WHERE order_id=? ORDER BY m.created_at,m.id""",(oid,))]
            p["publications"]=[dict(r) for r in db.execute("SELECT job_id,version,note,released_at FROM publications WHERE order_id=? ORDER BY version DESC",(oid,))]
            if actor["role"]!="customer":
                p["jobs"]=[self._public_job(dict(r),True) for r in db.execute("SELECT * FROM jobs WHERE order_id=? ORDER BY attempt DESC",(oid,))]
            return p

    def _file_row(self,db,oid,file_id,kind=None):
        row=db.execute("SELECT * FROM files WHERE id=? AND order_id=?",(file_id,oid)).fetchone()
        if not row or (kind and row["kind"]!=kind): raise Problem(422,"Berkas tidak sesuai dengan pesanan ini.")
        return dict(row)

    def file_path(self,row): return self.uploads/row["stored_name"]

    def authorize_file(self,actor,oid,file_id):
        with self.connect() as db:
            self._role_order(db,actor,oid)
            row=self._file_row(db,oid,file_id)
            if actor["role"]=="operator" and row["kind"]=="payment_proof": raise Problem(404,"Berkas tidak ditemukan.")
            return self.file_path(row),row

    def upload_allowed(self,actor,oid,kind,version):
        action={"source":"upload_source","boundary":"upload_boundary","payment_proof":"upload_payment_proof"}.get(kind)
        if not action: raise Problem(422,"Jenis berkas tidak dikenal.")
        with self.connect() as db:
            o=self._role_order(db,actor,oid);self._version(o,{"version":version})
            if action not in self._permissions(db,actor,o): raise Problem(409,"Unggahan tidak tersedia pada tahap ini.")
            return o

    def add_upload(self,actor,oid,kind,name,mime,staging,size,checksum,version,*,example=False):
        action={"source":"upload_source","boundary":"upload_boundary","payment_proof":"upload_payment_proof"}[kind]
        fid=uid();suffix={"image/tiff":".tif","image/png":".png","image/jpeg":".jpg","application/pdf":".pdf","application/geo+json":".geojson"}[mime]
        stored=fid+suffix;dest=self.uploads/stored;moved=False
        try:
            with self.tx() as db:
                o=self._role_order(db,actor,oid);self._version(o,{"version":version})
                if action not in self._permissions(db,actor,o): raise Problem(409,"Tahap pesanan telah berubah.")
                Path(staging).replace(dest);moved=True
                db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (fid,oid,kind,name,stored,mime,size,checksum,actor["id"],now(),int(example)))
                if kind in {"source","boundary"}:
                    fields={kind+"_file_id":fid,"data_review":None,"profile":None}
                    if o["status"] in {"scheduled","processing_failed"}:
                        fields["status"]="revision_requested" if o["published_job_id"] else "paid"
                    if example: fields["example"]=1
                    self._update(db,oid,**fields)
                    self._event(db,oid,actor["id"],action,"Data citra diperbarui" if kind=="source" else "Batas blok diperbarui","Data harus divalidasi sebelum analisis.")
                else:
                    self._update(db,oid)
                    self._event(db,oid,actor["id"],action,"Bukti pembayaran diunggah","Bukti bukan pembayaran terverifikasi.","staff")
        except Exception:
            if moved and dest.is_file(): dest.unlink()
            raise
        return self.detail(actor,oid)

    def _new_job(self,db,actor,o):
        if not all(o[k] for k in ("paid_at","data_review","assigned_to","profile","source_file_id")):
            raise Problem(409,"Pembayaran, validasi, dan penugasan harus selesai.")
        review=json.loads(o["data_review"])
        if review["source_file_id"]!=o["source_file_id"] or review["boundary_file_id"]!=o["boundary_file_id"]:
            raise Problem(409,"Versi sumber telah berubah. Validasi data kembali.")
        jid=uid();attempt=db.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM jobs WHERE order_id=?",(o["id"],)).fetchone()[0]
        db.execute("""INSERT INTO jobs(id,order_id,attempt,profile,source_file_id,boundary_file_id,status,phase,created_by,created_at,example)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (jid,o["id"],attempt,o["profile"],o["source_file_id"],o["boundary_file_id"],"queued","queued",actor["id"],now(),o["example"]))
        self._update(db,o["id"],status="processing",current_job_id=jid)
        self._event(db,o["id"],actor["id"],"start_job","Analisis masuk antrean",f"Percobaan analisis {attempt}. Hasil akan diperiksa sebelum dirilis.")
        return jid

    def action(self,actor,oid,action,body,engine):
        queued=None;validated=None
        if not isinstance(body,dict): raise Problem(422,"Data tindakan tidak valid.")
        # I/O validation outside DB write lock; check version again on commit.
        if action=="validate_data":
            with self.connect() as db:
                pre=self._role_order(db,actor,oid);self._version(pre,body)
                if action not in self._permissions(db,actor,pre): raise Problem(409,"Validasi belum diperbolehkan.")
                source=self._file_row(db,oid,pre["source_file_id"],"source")
                boundary=self._file_row(db,oid,pre["boundary_file_id"],"boundary") if pre["boundary_file_id"] else None
            if body.get("acknowledge") is not True: raise Problem(422,"Konfirmasi sumber, skala, cakupan dan izin penggunaan telah diperiksa.")
            note=string(body.get("note"),"Catatan validasi",5,1000)
            try: validated=engine.validate(self.file_path(source),pre["service"],pre["data_plan"],self.file_path(boundary) if boundary else None)
            except Exception as err:
                LOG.warning("Data validation failed for %s: %s",oid,err)
                raise Problem(422,str(err) if isinstance(err,ValueError) else "Citra atau batas blok tidak dapat divalidasi.")
            validated.update(source_file_id=source["id"],boundary_file_id=boundary["id"] if boundary else None,
                             checked_by=actor["name"],checked_at=now(),review_note=note)
        with self.tx() as db:
            o=self._role_order(db,actor,oid);self._version(o,body)
            q=whole(db.execute("SELECT * FROM quotes WHERE id=?",(o["quote_id"],)).fetchone()) if o["quote_id"] else None
            if action not in self._permissions(db,actor,o,q):
                raise Problem(409,"Tindakan ini tidak tersedia untuk peran atau tahap pesanan saat ini.")
            fields={};event="";note=""
            if action=="quote":
                amount=integer(body.get("amount"),"Nominal penawaran",1,10**12)
                scope=string(body.get("scope"),"Lingkup pekerjaan",5,2000)
                days=integer(body.get("estimated_days"),"Perkiraan hari",1,365)
                expires=calendar(body.get("expires_on"),"Berlaku hingga",minimum=today())
                instr=string(body.get("payment_instruction",""),"Petunjuk pembayaran",0,1200)
                if self.demo: instr="SIMULASI — tidak ada transfer nyata. Gunakan referensi DEMO."
                if q and q["decision"]=="pending":
                    db.execute("UPDATE quotes SET decision='superseded',decided_at=? WHERE id=?",(now(),q["id"]))
                qv=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM quotes WHERE order_id=?",(oid,)).fetchone()[0];qid=uid()
                db.execute("""INSERT INTO quotes(id,order_id,version,amount,scope,estimated_days,expires_on,payment_instruction,created_by,created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?)""",(qid,oid,qv,amount,scope,days,expires,instr,actor["id"],now()))
                fields={"status":"quoted","quote_id":qid};event="Penawaran disiapkan";note=f"Penawaran versi {qv} tersedia."
            elif action in {"accept_quote","decline_quote"}:
                if not q or q["decision"]!="pending": raise Problem(409,"Penawaran tidak lagi aktif.")
                if action=="accept_quote":
                    if self._quote_expired(q): raise Problem(409,"Masa berlaku penawaran berakhir.")
                    decision,status,event="accepted","accepted","Penawaran diterima"
                    note="Lingkup dan nominal disepakati; menunggu konfirmasi pembayaran."
                else:
                    decision,status,event="declined","submitted","Penawaran ditolak"
                    note=string(body.get("reason"),"Alasan penolakan",3,1000)
                db.execute("UPDATE quotes SET decision=?,decided_at=? WHERE id=?",(decision,now(),q["id"]))
                fields={"status":status}
            elif action=="submit_payment":
                if not q or q["decision"]!="accepted": raise Problem(409,"Terima penawaran terlebih dahulu.")
                amount=integer(body.get("amount"),"Nominal pembayaran",1,10**12)
                if amount!=q["amount"]: raise Problem(422,"Nominal konfirmasi harus sesuai penawaran.")
                ref=string(body.get("reference"),"Referensi pembayaran",3,120);proof=body.get("proof_file_id") or None
                if proof: self._file_row(db,oid,proof,"payment_proof")
                pid=uid()
                db.execute("""INSERT INTO payments(id,order_id,quote_id,amount,reference,proof_file_id,submitted_by,submitted_at,status)
                          VALUES (?,?,?,?,?,?,?,?,?)""",(pid,oid,q["id"],amount,ref,proof,actor["id"],now(),"pending"))
                fields={"status":"payment_review","payment_id":pid}
                event="Konfirmasi pembayaran diterima";note="Tim akan memeriksa pembayaran; belum terverifikasi."
            elif action in {"approve_payment","reject_payment"}:
                payment=db.execute("SELECT * FROM payments WHERE id=? AND order_id=? AND status='pending'",(o["payment_id"],oid)).fetchone()
                if not payment: raise Problem(409,"Tidak ada konfirmasi pembayaran aktif.")
                approved=action=="approve_payment"
                if approved and body.get("verified") is not True: raise Problem(422,"Konfirmasi bahwa pembayaran telah diperiksa manual.")
                note=string(body.get("note"),"Catatan pemeriksaan",3,1000)
                db.execute("UPDATE payments SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE id=?",
                           ("approved" if approved else "rejected",actor["id"],now(),note,payment["id"]))
                fields={"status":"paid" if approved else "accepted","paid_at":now() if approved else None}
                event="Pembayaran diverifikasi manual" if approved else "Konfirmasi pembayaran perlu diperbaiki"
            elif action=="cancel":
                fields={"status":"cancelled"};note=string(body.get("reason"),"Alasan pembatalan",3,1000)
                event="Pesanan dibatalkan sebelum verifikasi"
                if o["status"]=="payment_review":
                    db.execute("UPDATE payments SET status='cancelled',reviewed_by=?,reviewed_at=? WHERE id=?",(actor["id"],now(),o["payment_id"]))
                if q and q["decision"]=="pending":
                    db.execute("UPDATE quotes SET decision='cancelled',decided_at=? WHERE id=?",(now(),q["id"]))
            elif action=="validate_data":
                fields={"data_review":json.dumps(validated,ensure_ascii=False)}
                if o["status"] in {"scheduled","processing_failed"}:
                    fields.update(status="revision_requested" if o["published_job_id"] else "paid",profile=None)
                event,note="Data diperiksa",validated["review_note"]
            elif action=="schedule":
                person=db.execute("SELECT id,name,role,active FROM users WHERE id=?",(body.get("assigned_to"),)).fetchone()
                if not person or not person["active"] or person["role"] not in {"admin","operator"}:
                    raise Problem(422,"Pilih anggota tim aktif.")
                profile=body.get("profile");review=json.loads(o["data_review"])
                if profile not in PROFILES or PROFILES[profile]["service"]!=o["service"] or PROFILES[profile]["format"]!=review["format"]:
                    raise Problem(422,"Profil tidak sesuai layanan atau data.")
                if not engine.available().get(profile): raise Problem(422,"Profil model atau pembaca raster belum tersedia.")
                scheduled=calendar(body.get("schedule_date"),"Tanggal pengerjaan",minimum=today())
                note=string(body.get("note",""),"Catatan pengerjaan",0,1000)
                fields={"status":"scheduled","assigned_to":person["id"],"profile":profile,"schedule_date":scheduled,"schedule_note":note}
                event="Pengerjaan dijadwalkan";note=f"{person['name']} · {scheduled}. {note}"
            elif action=="start_job":
                if not engine.available().get(o["profile"]): raise Problem(422,"Model atau pembaca raster belum siap.")
                queued=self._new_job(db,actor,o)
            elif action=="rework":
                note=string(body.get("note"),"Catatan koreksi",5,1000);fields={"status":"scheduled"};event="Draft dikembalikan untuk perbaikan"
            elif action=="publish":
                checklist=body.get("checklist")
                if not isinstance(checklist,dict) or not all(checklist.get(k) is True for k in QA_KEYS):
                    raise Problem(422,"Seluruh pemeriksaan kualitas harus dikonfirmasi.")
                note=string(body.get("note"),"Catatan QA dan batas hasil",10,2000)
                job=db.execute("SELECT * FROM jobs WHERE id=? AND order_id=? AND status='succeeded'",(o["current_job_id"],oid)).fetchone()
                if not job or not (self.results/job["id"]/"report.json").is_file(): raise Problem(409,"Draft hasil lengkap belum tersedia.")
                from .portal_quality import freeze
                freeze(self,db,job["id"])
                pv=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM publications WHERE order_id=?",(oid,)).fetchone()[0]
                db.execute("INSERT INTO publications VALUES (?,?,?,?,?,?,?,?)",(uid(),oid,job["id"],pv,json.dumps(checklist),note,actor["id"],now()))
                fields={"status":"ready","published_job_id":job["id"]};event=f"Hasil versi {pv} dirilis"
            elif action=="complete":
                fields={"status":"completed"};event="Pelanggan menerima hasil";note="Pekerjaan selesai; hasil tetap dapat diakses."
            elif action=="request_revision":
                fields={"status":"revision_requested","revision_count":o["revision_count"]+1}
                note=string(body.get("note"),"Permintaan revisi",5,2000);event="Revisi diminta"
            elif action=="message":
                note=string(body.get("body"),"Pesan",1,2000)
                db.execute("INSERT INTO messages VALUES (?,?,?,?,?)",(uid(),oid,actor["id"],note,now()))
                self._update(db,oid)
            elif action=="use_sample":
                if not self.demo: raise Problem(403,"Contoh hanya untuk workspace demo.")
                source=INSPECTION_SAMPLE if o["service"]=="inspection" else ((INVENTORY_TILE_SAMPLE if o["service"]=="inventory" else TILE_SAMPLE) if o["data_plan"]=="tile" else GEO_SAMPLE)
                if not source.is_file(): raise Problem(404,"Contoh model tidak tersedia.")
                fid=self._copy_demo_file(db,oid,actor["id"],source,"source")
                fields={"source_file_id":fid,"data_review":None,"profile":None,"example":1}
                if source==GEO_SAMPLE and not o["boundary_file_id"] and GEO_BOUNDARY.is_file():
                    fields["boundary_file_id"]=self._copy_demo_file(db,oid,actor["id"],GEO_BOUNDARY,"boundary")
                event,note="Citra penelitian dipasang","Contoh penelitian, bukan sumber lapangan pelanggan."
            else: raise Problem(422,"Tindakan tidak dikenal.")
            if fields:
                self._update(db,oid,**fields);self._event(db,oid,actor["id"],action,event,note)
        return self.detail(actor,oid),queued

    def _copy_demo_file(self,db,oid,actor,source,kind):
        fid=uid();suffix=source.suffix.lower()
        mime={".jpg":"image/jpeg",".png":"image/png",".tif":"image/tiff",".geojson":"application/geo+json"}[suffix]
        stored=fid+suffix; shutil.copyfile(source,self.uploads/stored)
        checksum=sha256((self.uploads/stored).read_bytes()).hexdigest()
        db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (fid,oid,kind,source.name,stored,mime,source.stat().st_size,checksum,actor,now(),1))
        return fid

    def recover_interrupted(self):
        with self.tx() as db:
            for job in db.execute("SELECT * FROM jobs WHERE status IN ('queued','running')").fetchall():
                db.execute("UPDATE jobs SET status='failed',phase='interrupted',error=?,finished_at=? WHERE id=?",
                           ("Proses terputus saat server berhenti. Jalankan ulang.",now(),job["id"]))
                o=db.execute("SELECT * FROM orders WHERE id=?",(job["order_id"],)).fetchone()
                if o["current_job_id"]==job["id"] and o["status"]=="processing":
                    self._update(db,o["id"],status="processing_failed")
                    self._event(db,o["id"],None,"job_interrupted","Analisis terputus","Tim dapat menjalankan ulang dari sumber tersimpan.")

    def job_begin(self,jid):
        with self.tx() as db:
            job=db.execute("SELECT * FROM jobs WHERE id=? AND status='queued'",(jid,)).fetchone()
            if not job: return None
            db.execute("UPDATE jobs SET status='running',phase='preparing',started_at=? WHERE id=?",(now(),jid))
            source=self._file_row(db,job["order_id"],job["source_file_id"],"source")
            boundary=self._file_row(db,job["order_id"],job["boundary_file_id"],"boundary") if job["boundary_file_id"] else None
            return dict(job),source,boundary

    def job_progress(self,jid,phase,completed,total):
        with self.tx() as db:
            db.execute("UPDATE jobs SET phase=?,completed=?,total=? WHERE id=? AND status='running'",
                       (str(phase)[:40],max(0,int(completed)),max(0,int(total)),jid))

    def job_end(self,jid,error=None):
        with self.tx() as db:
            job=db.execute("SELECT * FROM jobs WHERE id=? AND status IN ('queued','running')",(jid,)).fetchone()
            if not job: return
            db.execute("UPDATE jobs SET status=?,phase=?,completed=?,total=1,error=?,finished_at=? WHERE id=?",
                       ("failed" if error else "succeeded","failed" if error else "done",0 if error else 1,error,now(),jid))
            o=db.execute("SELECT * FROM orders WHERE id=?",(job["order_id"],)).fetchone()
            if o["current_job_id"]==jid and o["status"]=="processing":
                self._update(db,o["id"],status="processing_failed" if error else "quality_review")
                self._event(db,o["id"],None,"job_failed" if error else "job_succeeded","Analisis perlu diulang" if error else "Draft analisis selesai",
                            "Tim dapat memperbaiki sumber atau menjalankan ulang." if error else "Menunggu QA dan rilis oleh admin.")

    def authorize_result(self,actor,oid,jid):
        with self.connect() as db:
            self._role_order(db,actor,oid)
            job=db.execute("SELECT * FROM jobs WHERE id=? AND order_id=? AND status='succeeded'",(jid,oid)).fetchone()
            pub=db.execute("SELECT * FROM publications WHERE order_id=? AND job_id=?",(oid,jid)).fetchone()
            if not job or (actor["role"]=="customer" and not pub): raise Problem(404,"Hasil belum tersedia.")
            folder=self.results/jid
            if not (folder/"report.json").is_file(): raise Problem(404,"Berkas hasil belum tersedia.")
            return folder,whole(pub),self._public_job(dict(job),actor["role"]!="customer")

    def seed_demo(self):
        if not self.demo: raise ValueError("Demo data prohibited in operational store")
        with self.tx() as db:
            existing=bool(db.execute("SELECT 1 FROM users LIMIT 1").fetchone())
            actors={}
            for role,name,org in (("admin","Nadia · Admin","CrownTrace"),("operator","Raka · Operator","CrownTrace"),("customer","Ari Pratama","Perkebunan Demo")):
                row=db.execute("SELECT u.* FROM users u JOIN demo_roles d ON d.user_id=u.id WHERE d.role=?",(role,)).fetchone()
                if row is None:
                    # Migrate only previously seeded identities in a demo database.
                    old=db.execute("SELECT * FROM users WHERE role=? AND name=? AND organization=? ORDER BY created_at LIMIT 1",(role,name,org)).fetchone()
                    alias=f"{role}-{secrets.token_hex(10)}@example.invalid";secret=secrets.token_urlsafe(32)
                    if old is not None:
                        db.execute("UPDATE users SET email=?,password_hash=? WHERE id=?",(alias,password_hash(secret),old['id']))
                        db.execute("DELETE FROM sessions WHERE user_id=?",(old['id'],))
                        actor=user_public(db.execute("SELECT * FROM users WHERE id=?",(old['id'],)).fetchone())
                    else:actor=self._add_user(db,alias,secret,name,org,role)
                    db.execute("INSERT INTO demo_roles VALUES (?,?)",(role,actor['id']))
                    actors[role]=actor
                else:actors[role]=user_public(row)
            if existing:return
            admin,operator,customer=(actors[r] for r in ("admin","operator","customer"))

            def order(title,location,service,plan,area,brief):
                return self._new_order(db,customer["id"],dict(title=title,location=location,service=service,
                                       data_plan=plan,area_ha=area,brief=brief),example=1)

            def offer(oid,amount,scope,days,accept=False,paid=False,pending=False):
                qid=uid();stamp=now()
                db.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (qid,oid,1,amount,scope,days,(today()+timedelta(days=7)).isoformat(),"SIMULASI — tidak ada transfer nyata.",
                            admin["id"],stamp,"accepted" if accept else "pending",stamp if accept else None))
                fields={"quote_id":qid,"status":"accepted" if accept else "quoted"}
                self._event(db,oid,admin["id"],"quote","Penawaran disiapkan","Penawaran contoh untuk simulasi.")
                if accept:self._event(db,oid,customer["id"],"accept_quote","Penawaran diterima","Kesepakatan simulasi.")
                if paid or pending:
                    pid=uid()
                    db.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (pid,oid,qid,amount,"DEMO-"+oid[:6].upper(),None,customer["id"],stamp,"approved" if paid else "pending",
                                admin["id"] if paid else None,stamp if paid else None,"Simulasi, bukan transaksi nyata." if paid else None))
                    fields.update(payment_id=pid,paid_at=stamp if paid else None,status="paid" if paid else "payment_review")
                    self._event(db,oid,customer["id"],"submit_payment","Konfirmasi pembayaran diterima","Referensi simulasi.")
                    if paid:self._event(db,oid,admin["id"],"approve_payment","Pembayaran simulasi diverifikasi","Tidak ada uang dipindahkan.")
                self._update(db,oid,**fields)

            ready=order("MOPAD · Inventaris dua blok","Lokasi demonstrasi · bukan GPS kebun asli","segmentation","geotiff",24.6407,
                        "Contoh pengiriman paket layer penelitian yang sudah diproses.")
            offer(ready,2950000,"Paket contoh peta tajuk, titik, batas blok dan ekspor GIS.",5,accept=True,paid=True)
            if (PUBLIC_SAMPLE/"summary.json").is_file():
                jid=uid();clone_report(PUBLIC_SAMPLE,self.results/jid);stamp=now()
                db.execute("""INSERT INTO jobs(id,order_id,attempt,profile,status,phase,completed,total,created_by,created_at,started_at,finished_at,example)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",(jid,ready,1,"geo_crowns","succeeded","done",1,1,operator["id"],stamp,stamp,stamp,1))
                db.execute("INSERT INTO publications VALUES (?,?,?,?,?,?,?,?)",
                           (uid(),ready,jid,1,json.dumps({k:True for k in QA_KEYS}),
                            "Contoh QA MOPAD dari penelitian. Georeferensi demonstrasi, bukan sensus atau pekerjaan lapangan.",admin["id"],stamp))
                self._update(db,ready,status="ready",assigned_to=operator["id"],profile="geo_crowns",
                             schedule_date=today().isoformat(),current_job_id=jid,published_job_id=jid)
                self._event(db,ready,admin["id"],"publish","Hasil contoh dirilis","Lihat peta, rekap blok dan hasil ekspor.")
            order("Kebun uji · permintaan inventaris","Kalimantan Tengah · contoh brief","inventory","field",35.0,
                  "Simulasi permintaan survey drone. Tim perlu menyepakati lingkup peliputan.")
            quoted=order("Cek kondisi · blok pilot","Tile RGB penelitian","inspection","tile",None,"Evaluasi kandidat visual dari tile sebanding Ganoderma V2.")
            offer(quoted,250000,"Penyaringan satu tile RGB, rekap kandidat dan lampiran hasil.",2)
            awaiting=order("Inventaris · data dari pelanggan","Lokasi contoh","inventory","tile",None,"Satu tile RGB untuk inventaris piksel.")
            offer(awaiting,175000,"Inventaris titik dan hitungan satu tile tanpa koordinat GPS.",2,accept=True,pending=True)
            task=order("Segmentasi · uji model Detail","Tile Trees v3 · demo","segmentation","tile",None,"Coba analisis CrownPrompt pada citra penelitian.")
            offer(task,300000,"Segmentasi per mahkota, COCO RLE, CSV dan overlay satu tile.",2,accept=True,paid=True)
            if TILE_SAMPLE.is_file():
                source=self._copy_demo_file(db,task,admin["id"],TILE_SAMPLE,"source")
                review={"format":"tile","coordinate_mode":"pixel","width":640,"height":640,"crs":None,"source_file_id":source,
                        "boundary_file_id":None,"checked_by":"Admin demo","checked_at":now(),"review_note":"Tile penelitian 640 × 640 sesuai profil contoh.","note":"Contoh penelitian."}
                self._update(db,task,status="scheduled",assigned_to=operator["id"],schedule_date=today().isoformat(),
                             schedule_note="Mulai analisis untuk melihat pekerjaan dan draft QA sebenarnya.",profile="detail",
                             source_file_id=source,data_review=json.dumps(review))
                self._event(db,task,admin["id"],"schedule","Pengerjaan contoh siap","Operator dapat menjalankan analisis yang sebenarnya.")
