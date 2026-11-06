"""M8: five owned synthetic teams, fifteen actual operators, original labs untouched."""
import json,os,secrets,shlex,sys,threading,time,traceback
from pathlib import Path
from uuid import uuid4
from acceptance_client import Client
from smoke_desktop import run_browser
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'infra/linux'),str(ROOT/'node'),str(ROOT/'backend')]
import runtime


def remote(code,bundle):
    r=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code),input=json.dumps(bundle),timeout=90)
    if r.returncode:raise RuntimeError('Scoped synthetic identity operation failed')


def main():
    out=ROOT/'reports/local/m8-01';out.mkdir(parents=True,exist_ok=True)
    path=runtime.SECRETS/'acceptance-class-profile.json'
    if path.exists():raise RuntimeError('Recover existing synthetic scope before another run')
    assert runtime.virsh('list','--name').stdout.split()==['cra-control']
    original_names=set(runtime.virsh('list','--all','--name').stdout.split())
    original_disks={p:(p.stat().st_size,p.stat().st_mtime_ns) for name in original_names-{'cra-control'} for p in (runtime.STATE/'vms'/name).glob('*.qcow2')}
    from types import SimpleNamespace
    from cra.contracts import missions
    import memory_budget
    declarations=memory_budget.running_declarations()
    assert len(declarations)==1 and declarations[0]['name']=='cra-control'
    resources=next(m.resources for m in missions() if m.id=='first-contact')
    assert (resources.operator_memory_mib,resources.target_memory_mib)==(1024,768)
    probe=SimpleNamespace(lab_id=uuid4(),organization_id=uuid4(),user_id=uuid4(),generation=1)
    planned=5*(3*resources.operator_memory_mib+resources.target_memory_mib)
    budget=memory_budget.require_capacity(probe,planned)
    org=str(uuid4());accounts=[]
    for index in range(16):
        accounts.append({'user_id':str(uuid4()),'username':'cra_test_'+uuid4().hex[:12],'password':secrets.token_urlsafe(24),'role':'teacher' if index==0 else 'student','organization_id':org})
    b={'purpose':'cra-acceptance-class-profile','organization_id':org,'accounts':accounts,'url':'http://127.0.0.1:8000'}
    with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as f:json.dump(b,f)
    report={'task':'M8-01','status':'FAIL','profile':'15 CLI operators / 5 teams / 5 targets','declared_budget':budget}
    stop=threading.Event();samples=[];monitor_errors=[];owned_names=set()
    def monitor():
        try:
            while not stop.is_set():
                values={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.split(':')[0] in ('MemAvailable','SwapTotal','SwapFree')}
                samples.append({'monotonic':time.monotonic(),'available_mib':values['MemAvailable']//1024,'swap_used_mib':(values['SwapTotal']-values['SwapFree'])//1024})
                stop.wait(2)
        except Exception:monitor_errors.append('memory_monitor_failed')
    thread=threading.Thread(target=monitor);thread.start()
    try:
        remote('''import json,sys
from cra.auth import PASSWORDS
from cra.db import connect
b=json.load(sys.stdin)
assert b['purpose']=='cra-acceptance-class-profile' and len(b['accounts'])==16
with connect() as db:
 db.execute('INSERT INTO organizations VALUES (%s,%s)',(b['organization_id'],'Synthetic class profile'))
 for u in b['accounts']:
  assert u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id'] and u['role'] in ('teacher','student')
  db.execute('INSERT INTO users(id,username,password_hash) VALUES (%s,%s,%s)',(u['user_id'],u['username'],PASSWORDS.hash(u['password'])))
  db.execute('INSERT INTO memberships VALUES (%s,%s,%s)',(u['user_id'],u['organization_id'],u['role']))
''',b)
        teacher=Client(accounts[0]);report['team_create_seconds']=[]
        for index in range(5):
            assert min(x['available_mib'] for x in samples)>=4096
            members=accounts[1+index*3:4+index*3]
            team=teacher.request('/api/v1/teams',{'request_key':str(uuid4()),'name':'Synthetic group '+str(index+1),'members':[{'user_id':u['user_id'],'role':'operator'} for u in members]},expected=201)
            started=time.monotonic()
            lab=teacher.wait(teacher.request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1,'team_id':team['id']},expected=202),'READY',timeout=900)
            report['team_create_seconds'].append(round(time.monotonic()-started,2))
            prefix='cra-'+lab['id'].replace('-','')[:10]+'-1'
            owned_names.update(prefix+suffix for suffix in 'topq')
            for ordinal,u in enumerate(members):
                u['expected_operator']=prefix+'opq'[ordinal]
                manifest=json.loads((runtime.STATE/'vms'/u['expected_operator']/'manifest.json').read_text())
                assert manifest['memory_mib']==1024 and not manifest.get('egress',False)
            target_manifest=json.loads((runtime.STATE/'vms'/(prefix+'t')/'manifest.json').read_text())
            assert target_manifest['memory_mib']==768
            for name in [prefix+suffix for suffix in 'topq']:
                manifest=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text())
                assert memory_budget.declared_memory(runtime.owned_domain(name),manifest)==manifest['memory_mib']
                assert runtime.kvm_status(name)=={'enabled':True,'present':True}
            print('READY: synthetic team',index+1,'three operators',flush=True)
        assert set(runtime.virsh('list','--name').stdout.split())==owned_names|{'cra-control'}
        available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))//1024
        report['all_twenty_lab_vms_ready']='PASS'
        report['pre_browser_available_mib']=available
        report['pre_browser_required_mib']=4096+15*256+1024
        report['browser_load']='NOT RUN'
        if available<report['pre_browser_required_mib']:
            report['failed_gate']='insufficient_reserve_for_guest_load_and_browser'
            raise AssertionError('Insufficient reserve for owned guest load and browser')
        try:memory_budget.require_capacity(probe,4*2048+768)
        except ValueError as error:assert 'physical node capacity' in str(error)
        else:raise AssertionError('Live declared memory overcommit accepted')
        assert set(runtime.virsh('list','--name').stdout.split())==owned_names|{'cra-control'}
        report['live_inventory_oversized_profile_denied_without_mutation']='PASS'
        run_browser('browser_class_profile.py',b,timeout=360)
        report['browser_load']='PASS'
        assert min(x['available_mib'] for x in samples)>=4096
        last_generation=lab['generation']
        last_prefix='cra-'+lab['id'].replace('-','')[:10]+'-1'
        lab=teacher.wait(teacher.action(lab,'stop'),'STOPPED',timeout=300)
        assert set(runtime.virsh('list','--name').stdout.split())==(owned_names-{last_prefix+suffix for suffix in 'topq'})|{'cra-control'}
        lab=teacher.wait(teacher.action(lab,'start'),'READY',timeout=600)
        assert lab['generation']==last_generation
        assert set(runtime.virsh('list','--name').stdout.split())==owned_names|{'cra-control'}
        from smoke_advanced_course import command
        for account in accounts[-3:]:
            client=Client(account);own=client.request('/api/v1/labs/'+lab['id'])
            answer=command(client,own,"printf 'RESUME_%s\\n' VERIFIED",'CLASS_RESUME_DONE')
            assert 'RESUME_VERIFIED' in answer
        report.update(status='PASS',downscale_resume='PASS')
    finally:
        errors=[]
        try:teacher=Client(accounts[0]);labs=teacher.request('/api/v1/labs')
        except Exception:errors.append('synthetic_scope_list');labs=[]
        for lab in labs:
            try:
                end=time.monotonic()+900
                while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                    if time.monotonic()>end:raise TimeoutError('Cleanup lifecycle')
                    time.sleep(1);lab=teacher.request('/api/v1/labs/'+lab['id'])
                if lab['state']!='DESTROYED':teacher.wait(teacher.action(lab,'destroy'),'DESTROYED',timeout=300)
                prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                assert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.split())
                for folder in [runtime.STATE/'vms',runtime.SECRETS]:
                    assert not any(p.name.startswith(prefix) for p in folder.iterdir())
            except Exception:errors.append('owned_lab_cleanup')
        try:
            if errors:raise RuntimeError('Keep registered scope for retry')
            remote('''import json,sys
from cra.db import connect
b=json.load(sys.stdin)
assert b['purpose']=='cra-acceptance-class-profile'
with connect() as db:
 assert not db.execute("SELECT id FROM labs WHERE organization_id=%s AND state<>'DESTROYED'",(b['organization_id'],)).fetchone()
 for u in b['accounts']:
  assert u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id']
  db.execute('UPDATE users SET active=false WHERE id=%s AND username=%s',(u['user_id'],u['username']))
  db.execute('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s',(u['user_id'],))
''',b)
            path.rename(path.with_suffix('.completed.json'))
        except Exception:errors.append('synthetic_scope_cleanup')
        stop.set();thread.join(timeout=5)
        errors.extend(monitor_errors)
        if thread.is_alive() or not samples:errors.append('monitor_unconfirmed')
        if samples and min(x['available_mib'] for x in samples)<4096:errors.append('memory_reserve_breached')
        try:
            assert set(runtime.virsh('list','--name').stdout.split())=={'cra-control'}
            assert set(runtime.virsh('list','--all','--name').stdout.split())==original_names
            assert all(p.exists() and (p.stat().st_size,p.stat().st_mtime_ns)==stat for p,stat in original_disks.items())
            report['original_vms_and_disks_preserved']='PASS'
            r=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp env PYTHONPATH=. .venv/bin/python /opt/cra-completion-test/preservation.py after')
            assert r.returncode==0
            report['original_database_preserved']='PASS'
        except Exception:errors.append('original_preservation_unconfirmed')
        report.update(cleanup_errors=errors,memory_samples=len(samples),min_available_mib=min((x['available_mib'] for x in samples),default=0),max_swap_used_mib=max((x['swap_used_mib'] for x in samples),default=0))
        if errors:report['status']='FAIL'
        (out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        (out/'memory.json').write_text(json.dumps(samples)+'\n')
        if errors:raise RuntimeError('Synthetic cleanup incomplete')
    print('M8-01 '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL:',type(e).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
