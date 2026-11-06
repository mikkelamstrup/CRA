"""M5-01 actual browser team journey and foreign-scope denial; strict cleanup."""
import json
from pathlib import Path
import sys
import shlex
import time
import traceback
from acceptance_client import Client
from acceptance_terminal import Terminal
from websockets.exceptions import ConnectionClosed
from acceptance_class import provision,deactivate
from acceptance_identity import provision as provision_foreign,deactivate as deactivate_foreign
from smoke_reconciliation import control

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload


def main():
    paths=[];foreign_paths=[];report={'task':'M5-01','status':'FAIL'}
    out=ROOT/'reports/local/m5-01';out.mkdir(parents=True,exist_ok=True)
    try:
        path,bundle=provision(on_owned=paths.append)
        foreign=provision_foreign('isolation-a',on_owned=foreign_paths.append)
        for source,target in [('browser_teaching.py','teaching.py'),('browser_teams.py','teams.py')]:
            upload('cra-control','/home/cra/cra-deploy/'+source,(ROOT/'scripts'/source).read_bytes())
            control('sudo install -m 0644 /home/cra/cra-deploy/'+source+' /opt/cra-browser/'+target)
        result=runtime.ssh('cra-control','sudo -u cra-browser /opt/cra/backend/.venv/bin/python /opt/cra-browser/teams.py',input=path.read_text(),timeout=1000)
        print(result.stdout,end='',flush=True)
        if result.returncode:raise RuntimeError('Browser team acceptance failed')
        teacher=Client(bundle['users']['teacher']);student=Client(bundle['users']['student']);outsider=Client(foreign)
        lab=next(l for l in teacher.request('/api/v1/labs') if l['state']!='DESTROYED')
        assert outsider.request('/api/v1/teams')==[] and outsider.request('/api/v1/labs')==[]
        outsider.request('/api/v1/labs/'+lab['id'],expected=404)
        outsider.request('/api/v1/labs/'+lab['id']+'/terminal-grants',{},expected=404)
        student.request('/api/v1/labs/'+lab['id']+'/destroy',{},expected=404)
        print('PASS: actual cross-organization API/grant denial and learner lifecycle denial',flush=True)
        streams=[]
        try:
            streams.append(Terminal(teacher,lab))
            streams.append(Terminal(student,lab))
            code="""import json,sys
from cra.db import connect
b=json.load(sys.stdin)
assert b['purpose']=='cra-acceptance-teaching'
u=b['users']['student'];owner=b['users']['teacher']
with connect() as db:
 assert db.execute('SELECT l.id FROM labs l JOIN teams t ON t.id=l.team_id WHERE l.id=%s AND l.organization_id=%s AND l.user_id=%s AND t.created_by=%s',(b['lab_id'],b['organization_id'],owner['user_id'],owner['user_id'])).fetchone()
 assert u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id']
 assert db.execute('SELECT id FROM users WHERE id=%s AND username=%s',(u['user_id'],u['username'])).fetchone()
 assert db.execute('DELETE FROM team_members WHERE team_id=%s AND organization_id=%s AND user_id=%s',(b['team_id'],b['organization_id'],u['user_id'])).rowcount==1
"""
            began=time.monotonic()
            result=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code),input=json.dumps({**bundle,'lab_id':lab['id'],'team_id':lab['team_id']}))
            if result.returncode:raise RuntimeError('Scoped membership revocation failed')
            end=began+10
            while True:
                try:streams[1].read(max(.1,end-time.monotonic()))
                except ConnectionClosed:break
                if time.monotonic()>end:raise TimeoutError('Membership revocation did not close terminal')
            report['membership_revocation_seconds']=round(time.monotonic()-began,3)
            streams[0].command("printf 'OTHER_ACTOR_%s\\n' PRESERVED",b'OTHER_ACTOR_PRESERVED')
            student.request('/api/v1/labs/'+lab['id']+'/terminal-grants',{},expected=404)
            student.request('/api/v1/labs/'+lab['id'],expected=404)
            print('PASS: actual membership loss closes only removed actor terminal in '+str(report['membership_revocation_seconds'])+'s',flush=True)
        finally:
            for stream in streams:stream.close()
        report['status']='PASS'
    finally:
        errors=[]
        for path in paths:
            try:
                bundle=json.loads(path.read_text());client=Client(bundle['users']['teacher'])
                for lab in client.request('/api/v1/labs'):
                    deadline=time.monotonic()+300
                    while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                        if time.monotonic()>deadline:raise TimeoutError('Cleanup lifecycle')
                        time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                    if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED')
                    prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                    assert not any(name.startswith(prefix) for name in runtime.virsh('list','--all','--name').stdout.splitlines())
                    assert not any(p.name.startswith(prefix) for p in (runtime.STATE/'vms').iterdir())
                    assert not any(p.name.startswith(prefix) for p in runtime.SECRETS.iterdir())
                deactivate(path)
            except Exception:errors.append('scoped_team_cleanup')
        for path in foreign_paths:
            try:deactivate_foreign(path)
            except Exception:errors.append('scoped_foreign_cleanup')
        if errors:report['status']='FAIL'
        report['cleanup_errors']=errors;(out/'product-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Team cleanup incomplete')
    print('M5-01 product '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
