"""Post-rollout acceptance: scoped synthetic users and connection-only original lab checks."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import time
from uuid import uuid4

import deploy_cybercon as deploy
from private_client import PrivateClient
import acceptance_client as ac
import bounded_browser
import node2_acceptance as n2

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=deploy.PRIVATE
BUNDLE=PRIVATE/'synthetic.json'
bounded_browser.ROOT=ROOT
ac.ORIGIN=Path('/home/crauser/cra-classroom-access/url.txt').read_text().strip()


def provision():
    if not BUNDLE.exists():
        org=str(uuid4())
        data={'purpose':'CLASS-CYBERCON-01-acceptance','organization_id':org,'url':ac.ORIGIN,'users':{
            role:{'username':'cra_test_'+uuid4().hex[:12],'password':secrets.token_urlsafe(24),
                  'user_id':str(uuid4()),'organization_id':org,'role':'teacher' if role=='teacher' else 'student'}
            for role in ('teacher','student','other')}}
        deploy.atomic(BUNDLE,data)
    data=json.loads(BUNDLE.read_text())
    assert deploy.control('''import json,sys
from cra.auth import PASSWORDS
from cra.db import connect
b=json.load(sys.stdin);assert b['purpose']=='CLASS-CYBERCON-01-acceptance'
with connect() as db:
 db.execute('INSERT INTO organizations VALUES (%s,%s) ON CONFLICT DO NOTHING',(b['organization_id'],'Synthetic Cybercon acceptance'))
 for u in b['users'].values():
  assert u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id']
  db.execute('INSERT INTO users(id,username,password_hash) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(u['user_id'],u['username'],PASSWORDS.hash(u['password'])))
  db.execute('INSERT INTO memberships(user_id,organization_id,role) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(u['user_id'],u['organization_id'],u['role']))
print('true')
''',data)
    print('PASS: three private synthetic accounts provisioned in a separate organization')


def browser(data,phase):
    bounded_browser.run_browser('browser_cybercon_live.py',{**data,'phase':phase},timeout=900 if phase=='dispatch' else 120,
        evidence_path=ROOT/'reports/local/cybercon'/('live-'+phase+'.log'))


def lab_for(data):
    client=PrivateClient(data['users']['student'])
    labs=[l for l in client.request('/api/v1/labs') if l['state']!='DESTROYED']
    assert len(labs)==1 and labs[0]['mission_id']=='cybercon-incident'
    return client,labs[0]


def solve(data):
    client,lab=lab_for(data);assert lab['state']=='READY'
    node=n2.binding(lab['id'])
    name='cra-'+lab['id'].replace('-','')[:10]+'-'+format(lab['generation'],'x')+'o'
    def guest(command):
        if node==2:return n2.guest(lab['id'],name,command)
        manifest=json.loads((deploy.runtime.STATE/'vms'/name/'manifest.json').read_text())
        assert manifest['ownership']['lab_id']==lab['id']
        result=deploy.runtime.ssh(name,command,timeout=90)
        assert result.returncode==0,'Synthetic guest command failed'
        return result.stdout
    def analyst(command):
        return guest('ssh -F /home/cra/academy/cybercon/ssh_config nordtele '+shlex.quote(command))
    assert 'IKKE GODKENDT' in analyst('cybercon-check || true')
    patch="import json; p='/etc/cybercon/policy.json'; v=json.load(open(p)); v.update(emergency_lockdown=False,enforce_report_ownership=True,expose_support_bundle=False,revoked_sessions=['nt-support-legacy']); open(p,'w').write(json.dumps(v))"
    analyst('python3 -c '+shlex.quote(patch))
    checked=analyst('cybercon-check')
    flag=re.search(r'CRA\{[0-9a-f]{32}\}',checked)
    assert flag
    data.update(flag=flag[0],lab_id=lab['id'],node_id=node);deploy.atomic(BUNDLE,data)
    time.sleep(6)
    code="import json; rows=[json.loads(x) for x in open('/var/log/cybercon/attack.jsonl')]; assert [r['status'] for r in rows[-2:]]==[404,401]; print('BLOCKED')"
    assert 'BLOCKED' in analyst('python3 -c '+shlex.quote(code))
    print('PASS: installed mission on node',node,'repaired via analyst SSH; real continuing attack attempts denied; flag withheld from logs',flush=True)


def recovery():
    state=json.loads(deploy.STATE_FILE.read_text());assert state['phase']=='open'
    raw=json.loads(Path('/home/crauser/cra-classroom-access/credentials.json').read_text())
    ids={l['user_id'] for l in state['restore']}
    accounts=[a for a in raw['accounts'] if a['id'] in ids]
    assert len(accounts)==len(state['restore']) and all(a['role']=='student' for a in accounts)
    bounded_browser.run_browser('browser_cybercon_recovery.py',{'url':ac.ORIGIN,'accounts':accounts,
        'labs':{l['user_id']:l['id'] for l in state['restore']}},timeout=600,
        evidence_path=ROOT/'reports/local/cybercon/recovery-browser.log')


def cleanup(data):
    data['cleanup_incomplete']=True;deploy.atomic(BUNDLE,data)
    client=PrivateClient(data['users']['student'])
    for lab in client.request('/api/v1/labs'):
        if lab['state']=='DESTROYED':continue
        assert lab['mission_id']=='cybercon-incident'
        node=n2.binding(lab['id'])
        deadline=time.monotonic()+900
        while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
            if time.monotonic()>deadline:raise TimeoutError('Synthetic lab still transitioning; cleanup marker retained')
            time.sleep(3);lab=client.request('/api/v1/labs/'+lab['id'])
        if lab['state']=='DESTROYED':continue
        client.wait(client.action(lab,'destroy'),'DESTROYED',timeout=300)
        if node==2:assert n2.absent(lab['id'])
        else:
            prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
            assert not any(name.startswith(prefix) for name in deploy.runtime.virsh('list','--all','--name').stdout.split())
    assert deploy.control('''import json,sys
from cra.db import connect
b=json.load(sys.stdin)
with connect() as db:
 assert not db.execute("SELECT 1 FROM labs WHERE organization_id=%s AND state<>'DESTROYED'",(b['organization_id'],)).fetchone()
 for u in b['users'].values():
  assert u['username'].startswith('cra_test_')
  db.execute('UPDATE users SET active=false WHERE id=%s AND username=%s',(u['user_id'],u['username']))
  db.execute('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s',(u['user_id'],))
print('true')
''',data)
    data.pop('flag',None);data.pop('cleanup_incomplete',None);data['cleaned']=True;deploy.atomic(BUNDLE,data)
    print('PASS: synthetic lab removed and synthetic accounts deactivated')


if __name__=='__main__':
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['provision','dispatch','solve','finish','recovery','cleanup'])
    phase=parser.parse_args().phase
    if phase in ('provision','recovery'):globals()[phase]()
    else:
        data=json.loads(BUNDLE.read_text());assert data['purpose']=='CLASS-CYBERCON-01-acceptance'
        if phase in ('dispatch','finish'):browser(data,phase)
        else:globals()[phase](data)
