"""Verify local demo identities without storing a shared credential in source."""
import secrets

from crowntrace.portal_store import PortalStore,Problem,check_password


def test_demo_role_migration_and_operational_isolation(tmp_path):
    demo=PortalStore(tmp_path/'demo',demo=True)
    old_password=secrets.token_urlsafe(24)
    with demo.tx() as db:
        old=demo._add_user(db,'old-fixture@example.invalid',old_password,'Nadia · Admin','CrownTrace','admin')
    demo.seed_demo()
    token,csrf,actor=demo.demo_sign_in('admin')
    assert actor['id']==old['id'] and actor['email']!=old['email']
    assert demo.session(token)['csrf']==csrf
    with demo.connect() as db:
        stored=db.execute('SELECT password_hash FROM users WHERE id=?',(old['id'],)).fetchone()[0]
    assert not check_password(old_password,stored)
    for role in ('customer','operator'):
        _,_,user=demo.demo_sign_in(role);assert user['role']==role
    demo.seed_demo()
    assert demo.demo_sign_in('admin')[2]['id']==old['id']
    operational=PortalStore(tmp_path/'operational',demo=False)
    try:operational.demo_sign_in('admin')
    except Problem as error:assert error.status==404
    else:raise AssertionError('Operational mode accepted demonstration role.')
