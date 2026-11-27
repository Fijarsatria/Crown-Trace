import unittest
import secrets

BACKUP_FIXTURE=secrets.token_urlsafe(24)
from pathlib import Path
from tempfile import TemporaryDirectory
from crowntrace.portal_store import PortalStore
from research.portal_backup import snapshot,restore

class BackupTests(unittest.TestCase):
    def test_restore_to_fresh_workspace_and_invalidate_sessions(self):
        with TemporaryDirectory() as t:
            root=Path(t);source=root/"source";store=PortalStore(source,demo=False)
            user=store.init_admin("backupfixture@example.invalid",BACKUP_FIXTURE,"Backup QA")
            token,_,_=store.sign_in(user["email"],BACKUP_FIXTURE)
            (source/"files"/"fixture.txt").write_text("test fixture only")
            a=root/"backup.zip";result=snapshot(source,a)
            self.assertEqual(result["mode"],"operational");target=root/"restored"
            restored=restore(a,target);self.assertEqual(restored["mode"],"operational")
            new=PortalStore(target,demo=False)
            self.assertIsNone(new.session(token));self.assertEqual((target/"files"/"fixture.txt").read_text(),"test fixture only")
            self.assertTrue(store.session(token))
            with self.assertRaises(ValueError):restore(a,target)
if __name__=="__main__":unittest.main(verbosity=2)
