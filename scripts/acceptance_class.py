"""Owned synthetic teacher/student pair for real class-flow acceptance."""
import json
import os
from pathlib import Path
import secrets
import shlex
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
from runtime import NODE,ssh


def provision(*,on_owned):
    path=NODE/'.secrets/acceptance-teaching.json'
    if path.exists():raise RuntimeError('Existing acceptance scope must be recovered explicitly')
    org=str(uuid4())
    users={role:{'username':'cra_test_'+uuid4().hex[:12],'password':secrets.token_urlsafe(24),'user_id':str(uuid4()),'organization_id':org,'role':role} for role in ('teacher','student')}
    bundle={'purpose':'cra-acceptance-teaching','organization_id':org,'users':users}
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:json.dump(bundle,f)
    on_owned(path)
    code='''import json,sys
from cra.auth import PASSWORDS
from cra.db import connect
b=json.load(sys.stdin)
assert b['purpose']=='cra-acceptance-teaching'
with connect() as db:
 db.execute('INSERT INTO organizations VALUES (%s,%s)',(b['organization_id'],'Synthetic class acceptance'))
 for role,u in b['users'].items():
  assert role in ('teacher','student') and role==u['role'] and u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id']
  db.execute('INSERT INTO users(id,username,password_hash) VALUES (%s,%s,%s)',(u['user_id'],u['username'],PASSWORDS.hash(u['password'])))
  db.execute('INSERT INTO memberships VALUES (%s,%s,%s)',(u['user_id'],u['organization_id'],role))
'''
    r=ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code),input=json.dumps(bundle))
    if r.returncode:raise RuntimeError('Class acceptance provisioning failed; private scope retained')
    return path,bundle


def deactivate(path):
    bundle=json.loads(path.read_text())
    code='''import json,sys
from cra.db import connect
b=json.load(sys.stdin)
assert b['purpose']=='cra-acceptance-teaching'
with connect() as db:
 for role,u in b['users'].items():
  assert u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id']
  assert not db.execute("SELECT id FROM labs WHERE organization_id=%s AND user_id=%s AND state<>'DESTROYED'",(b['organization_id'],u['user_id'])).fetchone()
  db.execute('UPDATE users SET active=false WHERE id=%s AND username=%s',(u['user_id'],u['username']))
  db.execute('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s',(u['user_id'],))
 db.execute('UPDATE teaching_sessions SET closed=true WHERE organization_id=%s',(b['organization_id'],))
'''
    r=ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code),input=json.dumps(bundle))
    if r.returncode:raise RuntimeError('Class acceptance cleanup failed')
    path.rename(path.with_suffix('.completed.json'))
