"""M3-03 real service/VM/DB faults and orphan cleanup, synthetic owner only."""
import json
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import time
import traceback

from acceptance_client import Client
from acceptance_identity import provision,deactivate
from acceptance_terminal import Terminal
from smoke_reset_recovery import stop_node

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
import provision_node
from lab_tools import upload

def control(command):
    result=runtime.ssh('cra-control',command,timeout=60)
    if result.returncode:raise RuntimeError('Owned control operation failed')

def database(code,payload):
    source='''import json,sys
from cra.db import connect
d=json.load(sys.stdin);identity=d['identity']
assert identity['purpose']=='cra-acceptance-reconcile' and identity['username'].startswith('cra_test_')
with connect() as db:
 assert db.execute('SELECT id FROM users WHERE id=%s AND username=%s',(identity['user_id'],identity['username'])).fetchone()
'''+code
    result=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(source),input=json.dumps(payload))
    if result.returncode:raise RuntimeError('Scoped database fault/probe failed')
    return json.loads(result.stdout)

def wait_lab(client,lab,state,code=None,timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        current=client.request('/api/v1/labs/'+lab['id'])
        if current['state']==state and (code is None or current['error_code']==code):return current
        time.sleep(.5)
    raise TimeoutError('Observed state did not converge')

def main():
    paths=[];client=None;lab=None;terminal=None;orphan_id=None
    out=ROOT/'reports/local/m3-03';out.mkdir(parents=True,exist_ok=True)
    report={'task':'M3-03','status':'FAIL','started_at':time.time(),'checks':[]}
    def passed(label):report['checks'].append(label);print('PASS: '+label,flush=True)
    try:
        path=provision('reconcile',resume='--resume' in sys.argv,on_owned=paths.append);client=Client(path)
        if client.request('/api/v1/labs'):raise RuntimeError('Use a fresh acceptance scope for fault sequence')
        lab=client.wait(client.create(),'READY');first=lab
        terminal=Terminal(client,lab)
        output=terminal.command('curl -fsS http://'+lab['target_ip']+":8080/; printf 'FLAG_%s\\n' READ",b'FLAG_READ')
        flag=re.search(rb'CRA\{[0-9a-f]{32}\}',output)[0].decode()
        result=client.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':1,'flag':flag})
        assert result['accepted'] and result['total_points']==100
        terminal.command("clear; printf 'MISSION_%s\\n' VERIFIED",b'MISSION_VERIFIED');terminal.close();terminal=None
        passed('real mission flag validated before fault injection')
        upload('cra-control','/home/cra/cra-deploy/browser-stale-state.py',(ROOT/'scripts/browser_stale_state.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser-stale-state.py /opt/cra-browser/stale-state.py')
        result=runtime.ssh('cra-control','sudo -u cra-browser /opt/cra/backend/.venv/bin/python /opt/cra-browser/stale-state.py',input=path.read_text(),timeout=100)
        if result.returncode:raise RuntimeError('Actual stale browser test failed')
        print(result.stdout,end='',flush=True);passed('browser failure expires readiness and recovers')
        control('sudo systemctl stop cra-reconciler')
        wait_lab(client,lab,'ERROR','observation_stale',timeout=30)
        client.request('/api/v1/labs/'+lab['id']+'/terminal-grants',{},expected=409)
        client.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':1,'flag':flag},expected=409)
        control('sudo systemctl start cra-reconciler');lab=wait_lab(client,lab,'READY')
        passed('reconciler loss expires server readiness, terminal grants and new scoring; restart recovers')
        stop_node();lab=wait_lab(client,lab,'ERROR','node_unavailable')
        provision_node.start();lab=wait_lab(client,lab,'READY')
        passed('actual node process loss/restart produces explicit error and converges')
        operator='cra-'+lab['id'].replace('-','')[:10]+'-1o'
        runtime.stop(operator);wait_lab(client,lab,'ERROR','runtime_drift')
        runtime.start(operator);runtime.wait_ready(operator);lab=wait_lab(client,lab,'READY')
        passed('unexpected real VM stop detected; owned VM restart recovers')
        operation=client.action(lab,'reset')
        end=time.monotonic()+30
        while client.request('/api/v1/operations/'+operation['operation_id'])['status']!='RUNNING':
            if time.monotonic()>end:raise TimeoutError('Worker did not claim reset')
            time.sleep(.2)
        end=time.monotonic()+60
        while True:
            with sqlite3.connect('file:'+str(runtime.STATE/'node/state.sqlite')+'?mode=ro',uri=True) as db:
                node_row=db.execute('SELECT generation,target FROM labs WHERE id=?',(lab['id'],)).fetchone()
            if node_row and node_row[0]==2 and (runtime.STATE/'vms'/node_row[1]/'manifest.json').exists():break
            if time.monotonic()>end:raise TimeoutError('Reset did not clone a new VM before crash')
            time.sleep(.2)
        report['worker_crash_after_new_vm_manifest']=True
        control('sudo systemctl kill --signal=KILL --kill-whom=main cra-worker')
        lab=client.wait(operation,'READY',timeout=420)
        assert lab['generation']==2
        assert client.request('/api/v1/labs/'+lab['id']+'/progress')['total_points']==100
        passed('worker SIGKILL during real reset resumes durable job without losing score')
        payload={'identity':client.identity,'lab_id':lab['id']}
        before=database(" print(json.dumps(str(db.execute('SELECT pg_postmaster_start_time() AS started').fetchone()['started'])))\n",payload)
        control('sudo systemctl restart postgresql@16-main')
        end=time.monotonic()+30
        while True:
            try:
                after=database(" print(json.dumps(str(db.execute('SELECT pg_postmaster_start_time() AS started').fetchone()['started'])))\n",payload)
                assert after!=before
                assert client.request('/api/v1/labs/'+lab['id']+'/progress')['total_points']==100
                lab=wait_lab(client,lab,'READY');break
            except Exception:
                if time.monotonic()>end:raise
                time.sleep(.5)
        passed('actual PostgreSQL postmaster restart preserves login, lab and 100 points')
        client.wait(client.action(lab,'destroy'),'DESTROYED')
        lab=client.wait(client.create(),'READY')
        payload={'identity':client.identity,'lab_id':lab['id']}
        code=''' row=db.execute('SELECT * FROM labs WHERE id=%s FOR UPDATE',(d['lab_id'],)).fetchone()
 assert row and str(row['user_id'])==identity['user_id'] and str(row['organization_id'])==identity['organization_id'] and row['state']=='READY'
 assert not db.execute('SELECT id FROM score_ledger WHERE lab_id=%s',(d['lab_id'],)).fetchone()
 db.execute('DELETE FROM lab_flags WHERE lab_id=%s',(d['lab_id'],))
 db.execute('DELETE FROM labs WHERE id=%s',(d['lab_id'],))
 print(json.dumps({'fault':'synthetic_lab_row_lost'}))
'''
        database(code,payload);orphan_id=lab['id'];fault=time.monotonic()
        end=time.monotonic()+120
        while time.monotonic()<end:
            result=database(" row=db.execute('SELECT status,observations,cleanup_reserved FROM node_orphans WHERE lab_id=%s',(d['lab_id'],)).fetchone();print(json.dumps(row))\n",payload)
            if result and result['status']=='RESOLVED':break
            if result and result['status'] in ('QUARANTINED','FAILED'):raise RuntimeError('Orphan cleanup did not converge')
            time.sleep(2)
        else:raise TimeoutError('Orphan cleanup timeout')
        assert time.monotonic()-fault>=30 and result['observations']>=2 and result['cleanup_reserved']
        for role in ('o','t'):
            name='cra-'+orphan_id.replace('-','')[:10]+'-1'+role
            assert name not in runtime.virsh('list','--all','--name').stdout.splitlines()
            assert not (runtime.STATE/'vms'/name).exists() and not (runtime.SECRETS/name).exists()
        assert client.request('/api/v1/labs/'+first['id']+'/progress')['total_points']==100
        assert 'cra-control' in runtime.virsh('list','--name').stdout.splitlines()
        assert 'cra-smoke' in runtime.virsh('list','--all','--name').stdout.splitlines()
        passed('actual orphan discovered after lost scoped DB row; delayed durable cleanup removes only owned VM pair and preserves score/control')
        report['status']='PASS'
    finally:
        errors=[]
        if terminal:
            try:terminal.close()
            except Exception:errors.append('terminal_close')
        try:
            pid=runtime.STATE/'node-agent.pid'
            if not pid.exists() or not (Path('/proc')/pid.read_text()).exists():provision_node.start()
            control('sudo systemctl start postgresql@16-main cra-api cra-worker cra-reconciler cra-relay cra-edge')
        except Exception:errors.append('service_recovery')
        for path in paths:
            try:
                cleanup=Client(path)
                for lab in cleanup.request('/api/v1/labs'):
                    deadline=time.monotonic()+420
                    while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                        if time.monotonic()>deadline:raise TimeoutError('Pending cleanup job')
                        time.sleep(1);lab=cleanup.request('/api/v1/labs/'+lab['id'])
                    if lab['state']!='DESTROYED':cleanup.wait(cleanup.action(lab,'destroy'),'DESTROYED')
                if orphan_id:
                    state=database(" row=db.execute('SELECT status FROM node_orphans WHERE lab_id=%s',(d['lab_id'],)).fetchone();print(json.dumps(row))\n",{'identity':cleanup.identity,'lab_id':orphan_id})
                    if not state or state['status']!='RESOLVED':raise RuntimeError('Orphan remains; preserve identity for recovery')
                deactivate(path)
            except Exception:errors.append('scoped_cleanup')
        if errors:report['status']='FAIL'
        report['cleanup_errors']=errors;report['finished_at']=time.time()
        (out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Acceptance cleanup incomplete')
    print('M3-03 '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
