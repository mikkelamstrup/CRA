"""Synthetic, separately scoped acceptance accounts; credentials never printed."""
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


def provision(label,*,resume=False,on_owned=None):
    if label not in ('score','isolation-a','isolation-b','session','reconcile','course'):raise ValueError('Unknown acceptance scope')
    path=NODE/'.secrets'/('acceptance-'+label+'.json')
    if resume:
        if not path.exists() or path.stat().st_mode&0o077:raise ValueError('Private acceptance identity required')
        if on_owned:on_owned(path)
        return path
    if path.exists():
        raise RuntimeError('Acceptance identity already exists; use explicit resume or archive a completed run')
    identity={'purpose':'cra-acceptance-'+label,'username':'cra_test_'+uuid4().hex[:12],
              'password':secrets.token_urlsafe(24),'user_id':str(uuid4()),'organization_id':str(uuid4())}
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:json.dump(identity,stream)
    if on_owned:on_owned(path)
    code='''
import json,sys
from cra.auth import PASSWORDS
from cra.db import connect
identity=json.load(sys.stdin)
assert identity['purpose'].startswith('cra-acceptance-') and identity['username'].startswith('cra_test_')
with connect() as db:
 db.execute('INSERT INTO organizations VALUES (%s,%s)',(identity['organization_id'],'Synthetic acceptance'))
 db.execute('INSERT INTO users(id,username,password_hash) VALUES (%s,%s,%s)',(identity['user_id'],identity['username'],PASSWORDS.hash(identity['password'])))
 db.execute("INSERT INTO memberships VALUES (%s,%s,'student')",(identity['user_id'],identity['organization_id']))
'''
    result=ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code),input=json.dumps(identity))
    if result.returncode:raise RuntimeError('Synthetic identity creation failed; credentials withheld')
    return path


def deactivate(path):
    identity=json.loads(path.read_text())
    code='''
import json,sys
from cra.db import connect
identity=json.load(sys.stdin)
assert identity['purpose'].startswith('cra-acceptance-') and identity['username'].startswith('cra_test_')
with connect() as db:
 assert not db.execute("SELECT id FROM labs WHERE user_id=%s AND state<>'DESTROYED'",(identity['user_id'],)).fetchone()
 db.execute('UPDATE users SET active=false WHERE id=%s AND username=%s',(identity['user_id'],identity['username']))
 db.execute('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s',(identity['user_id'],))
'''
    result=ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code),input=json.dumps(identity))
    if result.returncode:raise RuntimeError('Synthetic identity cleanup incomplete')
    path.rename(path.with_suffix('.completed.json'))
