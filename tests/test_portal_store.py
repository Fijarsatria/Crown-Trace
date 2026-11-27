"""Verify backend service state; fixture engine exercises failure/recovery only.
The actual model-run acceptance tests live in portal_integration.py.
"""
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import unittest
import secrets

ADMIN_FIXTURE=secrets.token_urlsafe(24)
CLIENT_FIXTURE=secrets.token_urlsafe(24)
OPERATOR_FIXTURE=secrets.token_urlsafe(24)
CLIENT_NEXT=secrets.token_urlsafe(24)
from tempfile import TemporaryDirectory
from PIL import Image
from crowntrace.portal_store import PortalStore, Problem, today, check_password


class StateOnlyEngine:
    """No model output is produced or claimed in state-machine unit tests."""
    def available(self):
        return {"fast":True,"detail":True}

    def validate(self,source,service,data_plan,boundary):
        with Image.open(source) as img:
            if img.size!=(640,640):raise ValueError("Expected test tile")
        return {"format":"tile","width":640,"height":640,"crs":None}


def add_users(store):
    admin=store.init_admin("admin@example.invalid",ADMIN_FIXTURE,"Admin QA")
    customer=store.register({"name":"QA Pelanggan","email":"customer@example.invalid","password":CLIENT_FIXTURE,"role":"admin"})
    op=store.create_operator(admin,{"name":"QA Operator","email":"operator@example.invalid","password":OPERATOR_FIXTURE})
    return admin,customer,op


def fail(status,fn):
    try:
        fn()
    except Problem as raised:
        assert raised.status==status
    else:
        raise AssertionError(f"Expected Problem({status})")


def _case_separate_databases_and_password_session(tmp_path):
    path=tmp_path/"operational"
    store=PortalStore(path,demo=False)
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM users").fetchone()
    admin,cust,op=add_users(store)
    assert cust["role"]=="customer" and op["role"]=="operator"
    with store.connect() as db:
        ph=db.execute("SELECT password_hash FROM users WHERE id=?",(admin["id"],)).fetchone()[0]
        assert ADMIN_FIXTURE not in ph and check_password(ADMIN_FIXTURE,ph)
    token,csrf,user=store.sign_in(cust["email"],CLIENT_FIXTURE)
    assert store.session(token)["user"]==user and store.session(token)["csrf"]==csrf
    store.password_change(cust,{"old_password":CLIENT_FIXTURE,"password":CLIENT_NEXT})
    assert store.session(token) is None
    fail(401,lambda:store.sign_in(cust["email"],CLIENT_FIXTURE))
    newtoken,_,_=store.sign_in(cust["email"],CLIENT_NEXT)
    store.sign_out(newtoken);assert store.session(newtoken) is None
    with unittest.TestCase().assertRaises(ValueError):PortalStore(path,demo=True)
    with unittest.TestCase().assertRaises(ValueError):store.seed_demo()


def prepare(store,admin,cust,op):
    engine=StateOnlyEngine()
    order=store.create_order(cust,{"title":"QA recovery state machine","location":"QA only","service":"segmentation","data_plan":"tile"})
    oid=order["id"]
    def action(actor,name,**body):
        return store.action(actor,oid,name,{"version":store.detail(actor,oid)["version"],**body},engine)
    day=(today()+timedelta(days=1)).isoformat()
    _,_=action(admin,"quote",amount=12000,scope="Pengujian unit; bukan transaksi.",estimated_days=2,expires_on=day)
    action(cust,"accept_quote")
    action(cust,"submit_payment",amount=12000,reference="UNIT-ONLY")
    action(admin,"approve_payment",verified=True,note="Unit test only")
    fail(409,lambda:action(admin,"use_sample"))
    test_tile=store.staging/"fixture.png";Image.new("RGB",(640,640)).save(test_tile)
    version=store.detail(cust,oid)["version"]
    raw=test_tile.read_bytes()
    store.add_upload(cust,oid,"source","fixture.png","image/png",test_tile,len(raw),sha256(raw).hexdigest(),version)
    action(admin,"validate_data",note="Fixture unit test, no prediction",acknowledge=True)
    action(admin,"schedule",assigned_to=op["id"],profile="fast",schedule_date=day)
    return oid,action


def _case_interrupted_and_failed_jobs_can_recover(tmp_path):
    store=PortalStore(tmp_path/"operational",demo=False)
    admin,cust,op=add_users(store)
    oid,action=prepare(store,admin,cust,op)
    d,first=action(op,"start_job")
    assert first and d["status"]=="processing"
    assert store.job_begin(first)[0]["profile"]=="fast"
    # Software restart marks unfinished work as failed, never customer-published.
    store.recover_interrupted()
    assert store.detail(admin,oid)["status"]=="processing_failed"
    fail(404,lambda:store.authorize_result(cust,oid,first))
    d,second=action(op,"start_job")
    assert second!=first and d["job"]["attempt"]==2
    store.job_begin(second);store.job_end(second,error="Fixture inference failure")
    assert store.detail(admin,oid)["status"]=="processing_failed"
    d,third=action(op,"start_job")
    assert third!=second and d["job"]["attempt"]==3
    assert not d["published_job_id"] and not d["publications"]
    store.recover_interrupted()
    assert store.detail(admin,oid)["job"]["phase"]=="interrupted"


def _case_expired_quote_and_cancelled_orders(tmp_path):
    store=PortalStore(tmp_path/"operational",demo=False)
    admin,cust,op=add_users(store)
    order=store.create_order(cust,{"title":"QA quote expiration","location":"QA only","service":"inventory","data_plan":"geotiff"})
    oid=order["id"]
    def action(actor,name,**body):
        return store.action(actor,oid,name,{"version":store.detail(actor,oid)["version"],**body},StateOnlyEngine())
    d,_=action(admin,"quote",amount=1000,scope="QA expiry test",estimated_days=2,expires_on=today().isoformat())
    # Simulate the passage of time solely inside the isolated fixture.
    with store.tx() as db:db.execute("UPDATE quotes SET expires_on=? WHERE id=?",((today()-timedelta(days=1)).isoformat(),d["quote"]["id"]))
    assert store.detail(cust,oid)["quote_expired"]
    fail(409,lambda:action(cust,"accept_quote"))
    d,_=action(cust,"cancel",reason="Expired quote")
    assert d["status"]=="cancelled"
    fail(409,lambda:action(admin,"quote",amount=1000,scope="QA expiry test",estimated_days=2,expires_on=today().isoformat()))
    fail(404,lambda:store.detail(op,oid))

class PortalStoreUnitTests(unittest.TestCase):
    def test_operational_mode_and_sessions(self):
        with TemporaryDirectory() as d:
            _case_separate_databases_and_password_session(Path(d))

    def test_recovery(self):
        with TemporaryDirectory() as d:
            _case_interrupted_and_failed_jobs_can_recover(Path(d))

    def test_expired_quote(self):
        with TemporaryDirectory() as d:
            _case_expired_quote_and_cancelled_orders(Path(d))

    def test_demo_source_selection(self):
        from crowntrace.portal_engine import INVENTORY_TILE_SAMPLE,GEO_SAMPLE,GEO_BOUNDARY
        with TemporaryDirectory() as d:
            store=PortalStore(Path(d)/"demo",demo=True)
            admin,cust,op=add_users(store)
            for plan,expected in (("tile",INVENTORY_TILE_SAMPLE),("geotiff",GEO_SAMPLE)):
                order=store.create_order(cust,{"title":"QA example selection","location":"QA public dataset","service":"inventory","data_plan":plan})
                oid=order["id"]
                def act(actor,name,**body):
                    detail=store.detail(actor,oid)
                    return store.action(actor,oid,name,{"version":detail["version"],**body},StateOnlyEngine())[0]
                act(admin,"quote",amount=12000,scope="Example selection only",estimated_days=2,expires_on=today().isoformat())
                act(cust,"accept_quote");act(cust,"submit_payment",amount=12000,reference="QA-DEMO")
                act(admin,"approve_payment",verified=True,note="Demo simulation")
                actual=act(admin,"use_sample")
                assert actual["example"]==1
                assert next(f for f in actual["files"] if f["id"]==actual["source_file_id"])["name"]==expected.name
                if plan=="geotiff":
                    assert next(f for f in actual["files"] if f["id"]==actual["boundary_file_id"])["name"]==GEO_BOUNDARY.name

if __name__=="__main__":
    unittest.main(verbosity=2)
