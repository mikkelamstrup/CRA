"""Actual RAM-guard create/stop/start/reset using only a fresh synthetic scope."""
import json
from pathlib import Path
import sys
import time
import traceback
from acceptance_client import Client
from acceptance_class import provision,deactivate
from smoke_advanced_course import command
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime


def main():
    assert runtime.virsh('list','--name').stdout.split()==['cra-control']
    assert not (runtime.SECRETS/'acceptance-teaching.json').exists()
    original_names=set(runtime.virsh('list','--all','--name').stdout.split())
    original_disks={p:(p.stat().st_size,p.stat().st_mtime_ns) for name in original_names-{'cra-control'} for p in (runtime.STATE/'vms'/name).glob('*.qcow2')}
    out=ROOT/'reports/local/m8-01';out.mkdir(parents=True,exist_ok=True)
    paths=[];report={'task':'M8-01','status':'FAIL','scope':'one synthetic solo; RAM-guard lifecycle, not classroom capacity'}
    try:
        path,b=provision(on_owned=paths.append);student=Client(b['users']['student'])
        lab=student.wait(student.request('/api/v1/labs',{'mission_id':'first-files','mission_version':1},expected=202),'READY')
        first_generation=lab['generation']
        answer=command(student,lab,"printf 'persisted\\n' > /home/cra/ram-guard-proof.txt && printf 'GUARD_%s\\n' WRITTEN",'GUARD_WRITE_DONE')
        assert 'GUARD_WRITTEN' in answer
        lab=student.wait(student.action(lab,'stop'),'STOPPED')
        assert runtime.virsh('list','--name').stdout.split()==['cra-control']
        lab=student.wait(student.action(lab,'start'),'READY')
        assert lab['generation']==first_generation
        answer=command(student,lab,"grep -qx persisted /home/cra/ram-guard-proof.txt && printf 'GUARD_%s\\n' PRESERVED",'GUARD_START_DONE')
        assert 'GUARD_PRESERVED' in answer
        # Reset is deliberately exercised while this owned synthetic generation runs.
        lab=student.wait(student.action(lab,'reset'),'READY')
        assert lab['generation']==first_generation+1
        answer=command(student,lab,"test ! -e /home/cra/ram-guard-proof.txt && test -s /home/cra/academy/README.txt && printf 'RESET_CONTENT_%s\\n' VERIFIED",'GUARD_RESET_DONE')
        assert 'RESET_CONTENT_VERIFIED' in answer.replace('\r','').splitlines()
        report.update(status='PASS',stop_start_preservation='PASS',running_generation_reset='PASS')
    finally:
        errors=[]
        for path in paths:
            try:
                bundle=json.loads(path.read_text());owner=Client(bundle['users']['student'])
                for lab in owner.request('/api/v1/labs'):
                    deadline=time.monotonic()+600
                    while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                        if time.monotonic()>deadline:raise TimeoutError('Owned lifecycle cleanup')
                        time.sleep(1);lab=owner.request('/api/v1/labs/'+lab['id'])
                    if lab['state']!='DESTROYED':owner.wait(owner.action(lab,'destroy'),'DESTROYED')
                    prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                    assert not any(name.startswith(prefix) for name in runtime.virsh('list','--all','--name').stdout.split())
                    for folder in [runtime.STATE/'vms',runtime.SECRETS]:
                        assert not any(p.name.startswith(prefix) for p in folder.iterdir())
                deactivate(path)
            except Exception:errors.append('owned_scope_cleanup')
        try:
            assert runtime.virsh('list','--name').stdout.split()==['cra-control']
            assert set(runtime.virsh('list','--all','--name').stdout.split())==original_names
            assert all(p.exists() and (p.stat().st_size,p.stat().st_mtime_ns)==state for p,state in original_disks.items())
            r=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp env PYTHONPATH=. .venv/bin/python /opt/cra-completion-test/preservation.py after')
            assert r.returncode==0
        except Exception:errors.append('original_preservation_unconfirmed')
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        (out/'memory-lifecycle.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Owned cleanup incomplete')
    print('M8 RAM-guard actual lifecycle '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL:',type(error).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
