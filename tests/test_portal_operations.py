"""Focused local acceptance checks, using real precomputed map artifacts.
No model training or new geographic field truth is claimed by these tests.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta
import json
import unittest
import secrets
import shutil
from crowntrace.portal_store import PortalStore, Problem, today, QA_KEYS
from crowntrace.portal_quality import edit, review_state, export_csv, export_geojson, polygon, coordinate_transform
from crowntrace.portal_operations import compare, candidates, inbox, billing, billing_html
from crowntrace.portal_engine import PUBLIC_SAMPLE, clone_report, ROOT

GEO_RESULT=ROOT/"results/portal_final_20261126/run_evidence/geo_crowns"


class GeoFixtureEngine:
    def available(self):return {"geo_crowns":True}
    def validate(self,source,service,data_plan,boundary):
        return {"format":"geotiff","coordinate_mode":"geographic","width":2800,"height":2300,"crs":"EPSG:32750",
                "note":"Existing synthetic-coordinate QA fixture; not a new field survey."}


class OperationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.store=PortalStore(Path(self.tmp.name)/"store",demo=True)
        self.admin=self.store.init_admin("admin@example.invalid",secrets.token_urlsafe(24),"Admin QA")
        self.customer=self.store.register({"name":"Pelanggan QA","email":"cust@example.invalid","password":secrets.token_urlsafe(24)})
        self.operator=self.store.create_operator(self.admin,{"name":"Operator QA","email":"op@example.invalid","password":secrets.token_urlsafe(24)})
        self.other=self.store.register({"name":"Pelanggan lain","email":"other@example.invalid","password":secrets.token_urlsafe(24)})
        self.engine=GeoFixtureEngine()

    def tearDown(self):self.tmp.cleanup()

    def rejected(self,status,fn):
        with self.assertRaises(Problem) as ctx:fn()
        self.assertEqual(ctx.exception.status,status)

    def action(self,actor,oid,name,**body):
        return self.store.action(actor,oid,name,{"version":self.store.detail(actor,oid)["version"],**body},self.engine)

    def draft(self,source=GEO_RESULT,customer=None):
        customer=customer or self.customer
        o=self.store.create_order(customer,{"title":"Demo kebun QA","location":"QA synthetic","service":"segmentation","data_plan":"geotiff"})
        oid=o["id"];day=(today()+timedelta(days=1)).isoformat()
        self.action(self.admin,oid,"quote",amount=125000,scope="QA fixture only",estimated_days=2,expires_on=day)
        self.action(customer,oid,"accept_quote")
        self.action(customer,oid,"submit_payment",amount=125000,reference="DEMO-ONLY")
        self.action(self.admin,oid,"approve_payment",verified=True,note="Demo fixture")
        self.action(self.admin,oid,"use_sample")
        self.action(self.admin,oid,"validate_data",acknowledge=True,note="Precomputed synthetic QA fixture")
        self.action(self.admin,oid,"schedule",assigned_to=self.operator["id"],profile="geo_crowns",schedule_date=day)
        _,jid=self.action(self.operator,oid,"start_job")
        self.store.job_begin(jid)
        clone_report(source,self.store.results/jid,"geo_crowns")
        self.store.job_end(jid)
        return oid,jid

    def publish(self,oid):
        self.action(self.admin,oid,"publish",checklist={k:True for k in QA_KEYS},note="Reviewed software QA fixture; no field diagnosis.")

    def test_quality_revision_and_release(self):
        oid,jid=self.draft()
        original=json.loads((self.store.results/jid/"map.json").read_text())
        tree=original["trees"][0];other=original["trees"][1]
        self.rejected(404,lambda:review_state(self.store,self.customer,oid,jid))
        self.rejected(404,lambda:review_state(self.store,self.other,oid,jid))
        def change(revision,action,key=None,**kwargs):
            return edit(self.store,self.operator,oid,jid,{"revision":revision,"action":action,"tree_id":key,"note":"QA fixture correction",**kwargs})
        info=change(0,"review",tree["id"],visual_condition="normal_visual")
        self.assertEqual(info["summary"]["reviewed_count"],1)
        self.rejected(409,lambda:change(0,"exclude",other["id"]))
        info=change(1,"exclude",other["id"]);self.assertEqual(info["summary"]["effective_count"],439)
        info=change(2,"add",center=[14,14],visual_condition="uncertain")
        self.assertEqual(info["summary"]["effective_count"],440)
        self.assertEqual(info["summary"]["missing_geographic_coordinates"],1)
        added=next(r for r in info["changes"] if r["source"]=="manual")
        self.rejected(422,lambda:change(3,"reshape",added["id"],visual_condition="uncertain",polygon_px=[[0,0],[30,30],[0,30],[30,0]]))
        info=change(3,"reshape",added["id"],visual_condition="uncertain",polygon_px=[[4,4],[24,4],[24,24],[4,24]])
        self.assertEqual(info["summary"]["corrected_contours"],1)
        info=change(4,"restore",other["id"]);self.assertEqual(info["summary"]["effective_count"],441)
        self.rejected(422,lambda:change(5,"review",tree["id"],visual_condition="normal_visual",review_source="field_observation"))
        self.publish(oid)
        released=review_state(self.store,self.customer,oid,jid,include_rows=True)
        self.assertEqual(released["revision"],5);self.assertFalse(released["can_edit"])
        self.assertEqual(released["summary"]["effective_count"],441)
        self.assertIn(b"manual_polygon",export_csv(released))
        geo=export_geojson(released)
        self.assertEqual(len(geo["features"]),440)
        self.assertEqual(geo["crowntrace_review"]["manual_polygons_without_geo"],1)
        self.rejected(409,lambda:change(5,"exclude",tree["id"]))
        self.assertEqual(original,json.loads((self.store.results/jid/"map.json").read_text()))
        self.rejected(404,lambda:review_state(self.store,self.other,oid,jid))
        # Billing and local inbox follow server-side access.
        self.assertEqual(billing(self.store,self.customer,oid)["amount"],125000)
        self.assertIn(b"DEMO",billing_html(billing(self.store,self.admin,oid)))
        self.rejected(403,lambda:billing(self.store,self.operator,oid))
        self.rejected(404,lambda:billing(self.store,self.other,oid))
        self.assertGreater(inbox(self.store,self.customer)["unread"],0)
        self.assertEqual(inbox(self.store,self.customer,mark=True)["unread"],0)
        self.assertEqual(inbox(self.store,self.other)["unread"],0)
        self.assertTrue(all("Pembayaran" not in r["label"] and "Penawaran" not in r["label"] for r in inbox(self.store,self.operator)["items"]))

    def test_monitoring_actual_geo_artifacts(self):
        a,ja=self.draft(GEO_RESULT);b,jb=self.draft(PUBLIC_SAMPLE)
        self.publish(a);self.publish(b)
        payload={"before_order":a,"before_job":ja,"after_order":b,"after_job":jb,
                 "before_date":"2026-09-01","after_date":"2026-11-01","tolerance_m":2.5,"same_area":True}
        data=compare(self.store,self.customer,payload)
        self.assertTrue(data["example"])
        s=data["summary"]
        self.assertEqual(s["before_geographic"],440);self.assertEqual(s["after_geographic"],1698)
        self.assertEqual(s["matched"]+s["unmatched_before"],440)
        self.assertEqual(s["matched"]+s["unmatched_after"],1698)
        self.assertEqual(s["comparable_reviewed"],0)
        self.assertEqual(len(candidates(self.store,self.customer)["surveys"]),2)
        self.rejected(404,lambda:compare(self.store,self.other,payload))
        self.rejected(422,lambda:compare(self.store,self.customer,{**payload,"same_area":False}))
        self.rejected(422,lambda:compare(self.store,self.customer,{**payload,"after_date":"2026-08-01"}))
        self.rejected(422,lambda:compare(self.store,self.customer,{**payload,"tolerance_m":float("nan")}))
        print("MONITORING_FIXTURE:",json.dumps(s),flush=True)

    def test_monitoring_known_correspondence_and_visual_change(self):
        a,ja=self.draft();b,jb=self.draft()
        key=json.loads((self.store.results/ja/"map.json").read_text())["trees"][0]["id"]
        for oid,jid,condition in ((a,ja,"normal_visual"),(b,jb,"stress_visual")):
            edit(self.store,self.operator,oid,jid,{"revision":0,"action":"review","tree_id":key,"note":"Fixture QA only","visual_condition":condition})
            self.publish(oid)
        payload={"before_order":a,"before_job":ja,"after_order":b,"after_job":jb,
                 "before_date":"2026-09-01","after_date":"2026-11-01","tolerance_m":.1,"same_area":True}
        r=compare(self.store,self.customer,payload)
        self.assertEqual(r["summary"]["matched"],440)
        self.assertEqual(r["summary"]["observed_changes"],1)
        self.assertEqual(r["summary"]["comparable_reviewed"],1)
        self.assertEqual(r["transitions"]["normal_visual → stress_visual"],1)
        print("MONITORING_CONTROL:",json.dumps(r["summary"]),flush=True)

    def test_http_quality_and_operations(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from crowntrace.portal_server import make_handler
        from portal_integration import Client
        oid,jid=self.draft()
        server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(self.store,self.engine,None))
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url=f"http://127.0.0.1:{server.server_port}"
            def login(email,password):
                c=Client(url);r=c.post("/api/login",{"email":email,"password":password});c.csrf=r["csrf"];return c
            admin=login("admin@example.invalid",secrets.token_urlsafe(24))
            op=login("op@example.invalid",secrets.token_urlsafe(24))
            customer=login("cust@example.invalid",secrets.token_urlsafe(24))
            base=f"/api/orders/{oid}/results/{jid}/quality"
            customer.get(base,404)
            info=op.get(base);self.assertTrue(info["can_edit"]);self.assertTrue(info["review_queue"])
            key=info["review_queue"][0]["id"]
            body={"revision":0,"action":"review","tree_id":key,"visual_condition":"uncertain","note":"HTTP fixture review"}
            op.post(base,body,status=403,csrf=False)
            result=op.post(base,body);self.assertEqual(result["revision"],1)
            self.publish(oid)
            self.assertTrue(customer.get(base)["released"])
            self.assertIn(b"visual_condition",customer.get(base+"/csv"))
            self.assertEqual(json.loads(customer.get(base+"/geojson"))["type"],"FeatureCollection")
            self.assertIn(b"Ringkasan tagihan",customer.get(f"/api/orders/{oid}/invoice"))
            op.get(f"/api/orders/{oid}/invoice",403)
            self.assertTrue(admin.get("/api/monitoring/candidates")["surveys"])
            self.assertGreater(customer.get("/api/updates")["unread"],0)
            self.assertEqual(customer.post("/api/updates/read",{})["unread"],0)
            self.assertIn(b"portal-ops.js",customer.get("/app/monitoring"))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)

    def test_affine_geojson_manual_corrections(self):
        oid,jid=self.draft(ROOT/"results/next_20261126/geo_crowns_actual")
        added=edit(self.store,self.operator,oid,jid,{"revision":0,"action":"add","center":[14,14],"note":"QA fixture manual point","visual_condition":"uncertain"})
        key=next(x["id"] for x in added["changes"] if x["source"]=="manual")
        edit(self.store,self.operator,oid,jid,{"revision":1,"action":"reshape","tree_id":key,"note":"QA fixture manual polygon","visual_condition":"uncertain","polygon_px":[[4,4],[24,4],[24,24],[4,24]]})
        self.publish(oid)
        info=review_state(self.store,self.customer,oid,jid,include_rows=True)
        layer=export_geojson(info)
        self.assertEqual(info["summary"]["effective_count"],441)
        self.assertEqual(info["summary"]["missing_geographic_coordinates"],0)
        self.assertEqual(layer["crowntrace_review"]["manual_polygons_without_geo"],0)
        self.assertEqual(sum(f["geometry"]["type"]=="Point" for f in layer["features"]),441)
        self.assertEqual(sum(f["geometry"]["type"]=="Polygon" for f in layer["features"]),1)
    def test_geometry_edge_cases(self):
        self.assertEqual(polygon([[0,0],[10,0],[10,10],[0,10]],100,100,[5,5])[1],100)
        self.rejected(422,lambda:polygon([[0,0],[10,10],[0,10],[10,0]],100,100,[5,5]))
        self.rejected(422,lambda:polygon([[0,0],[10,0],[10,10],[0,10]],100,100,[20,20]))
        tf=coordinate_transform({"coordinate_mode":"geographic","geotransform":[110,.00001,0,-5,0,-.00001],"crs":"EPSG:4326"},{"trees":[]})
        self.assertAlmostEqual(tf([10,10])[0],110.0001)
        self.assertIsNone(coordinate_transform({"coordinate_mode":"geographic","geotransform":None},{"trees":[]}))


if __name__=="__main__":unittest.main(verbosity=2)
