"""Offline local backup/restore to a new workspace. Contains private customer data.
No external transfer. Never include a real backup in the distribution ZIP.
"""
import argparse,hashlib,json,sqlite3,shutil
from pathlib import Path,PurePosixPath
from tempfile import TemporaryDirectory
from contextlib import closing
from zipfile import ZipFile,ZIP_DEFLATED
from crowntrace.portal_server import exclusive_workspace

MANIFEST="backup-manifest.json"
DATABASE="crowntrace.sqlite3"

def hashfile(p):
    with p.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()

def valid_member(name):
    p=PurePosixPath(name)
    return (name==DATABASE or len(p.parts)>1 and p.parts[0] in ("files","results")) and not p.is_absolute() and ".." not in p.parts and "\\" not in name and ":" not in name

def snapshot(workspace,output):
    workspace=workspace.resolve();output=output.resolve()
    if not (workspace/DATABASE).is_file() or output.exists() or output.is_relative_to(workspace):
        raise ValueError("Use an existing workspace and a new backup outside it.")
    output.parent.mkdir(parents=True,exist_ok=True)
    with exclusive_workspace(workspace),TemporaryDirectory() as tmp:
        copy=Path(tmp)/DATABASE
        with closing(sqlite3.connect((workspace/DATABASE).as_uri()+"?mode=ro",uri=True)) as src,closing(sqlite3.connect(copy)) as dst:
            src.backup(dst)
            # Existing browser sessions should not survive a restored snapshot.
            dst.execute("DELETE FROM sessions");dst.commit()
            if dst.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("SQLite integrity check failed")
            mode=dst.execute("SELECT mode FROM environment").fetchone()[0]
        files={DATABASE:copy}
        for name in ("files","results"):
            folder=workspace/name
            for path in folder.rglob("*"):
                if path.is_symlink():raise ValueError("Symlinks are not supported in backups")
                if path.is_file():
                    member=path.relative_to(workspace).as_posix()
                    if not valid_member(member):raise ValueError("Invalid file path")
                    files[member]=path
        manifest={"format":"crowntrace-private-backup-v1","workspace_mode":mode,
                  "files":{n:{"sha256":hashfile(p),"bytes":p.stat().st_size} for n,p in files.items()}}
        # Reserve a new destination; no overwrite.
        with ZipFile(output,"x",ZIP_DEFLATED,compresslevel=3) as z:
            for name,path in files.items():z.write(path,name)
            z.writestr(MANIFEST,json.dumps(manifest,indent=2))
        with ZipFile(output) as z:
            if z.testzip() is not None:raise ValueError("Archive integrity check failed")
    return {"mode":mode,"files":len(files),"archive_bytes":output.stat().st_size,"note":"PRIVATE archive; contains records/uploads/password hashes. Keep in authorized encrypted storage."}

def restore(archive,target):
    target=target.resolve()
    if target.exists():raise ValueError("Restore only to a new directory; existing workspace is never overwritten.")
    with ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or MANIFEST not in names:raise ValueError("Invalid archive members")
        manifest=json.loads(z.read(MANIFEST));files=manifest.get("files")
        if manifest.get("format")!="crowntrace-private-backup-v1" or not isinstance(files,dict) or set(names)!=(set(files)|{MANIFEST}) or DATABASE not in files:
            raise ValueError("Invalid manifest")
        for name,meta in files.items():
            if not valid_member(name) or not (target/name).resolve().is_relative_to(target) or z.getinfo(name).file_size!=meta["bytes"]:
                raise ValueError("Mismatched backup path/size")
        target.mkdir(parents=True,exist_ok=False)
        for name,meta in files.items():
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True)
            with z.open(name) as source,dest.open("xb") as sink:shutil.copyfileobj(source,sink,1024**2)
            if hashfile(dest)!=meta["sha256"]:raise ValueError("Restored hash mismatch; do not use incomplete workspace")
        with closing(sqlite3.connect(target/DATABASE)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Restored integrity check failed")
            mode=db.execute("SELECT mode FROM environment").fetchone()[0]
            if mode!=manifest["workspace_mode"]:raise ValueError("Workspace mode mismatch")
        for name in ("files","results","staging"):(target/name).mkdir(exist_ok=True)
    return {"mode":mode,"files":len(files),"target":str(target),"note":"Restore verified; sessions invalidated. Open separately first."}

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("action",choices=("snapshot","restore"))
    p.add_argument("source",type=Path);p.add_argument("destination",type=Path)
    p.add_argument("--confirm-local-private",action="store_true",help="Confirm authorized private offline workspace.")
    a=p.parse_args()
    if not a.confirm_local_private:p.error("Explicit confirmation of private local backup required.")
    print(json.dumps((snapshot if a.action=="snapshot" else restore)(a.source,a.destination),indent=2))
