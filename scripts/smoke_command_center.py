"""M6-01 real teacher management acceptance; only owned synthetic resources."""
import json
from pathlib import Path
import sys
import time
import traceback
from acceptance_class import provision,deactivate
from acceptance_client import Client
from smoke_desktop import run_browser
from smoke_reconciliation import control
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload


def main():
    paths=[];report={'task':'M6-01','status':'FAIL'};out=ROOT/'reports/local/m6-01';out.mkdir(parents=True,exist_ok=True)
    try:
        path,bundle=provision(on_owned=paths.append);student=Client(bundle['users']['student'])
        lab=student.wait(student.create(),'READY');prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
        bundle.update(lab_id=lab['id'],target_ip=lab['target_ip'],next_operator=prefix+'2o')
        upload('cra-control','/home/cra/cra-deploy/browser_teaching.py',(ROOT/'scripts/browser_teaching.py').read_bytes());control('sudo install -m 0644 /home/cra/cra-deploy/browser_teaching.py /opt/cra-browser/teaching.py')
        run_browser('browser_command_center.py',bundle,timeout=600)
        report['status']='PASS'
    finally:
        errors=[]
        for path in paths:
            b=json.loads(path.read_text());scope_errors=[];seen=set()
            for role in ('teacher','student'):
                try:client=Client(b['users'][role]);labs=client.request('/api/v1/labs')
                except Exception:scope_errors.append('scoped_'+role+'_list');continue
                for lab in labs:
                    if lab['id'] in seen or not lab['can_manage']:continue
                    seen.add(lab['id'])
                    try:
                        deadline=time.monotonic()+900
                        while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                            if time.monotonic()>deadline:raise TimeoutError('Cleanup lifecycle')
                            time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                        if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED',timeout=300)
                        prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                        assert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.splitlines())
                        assert not any(p.name.startswith(prefix) for p in (runtime.STATE/'vms').iterdir())
                        assert not any(p.name.startswith(prefix) for p in runtime.SECRETS.iterdir())
                    except Exception:scope_errors.append('scoped_'+role+'_lab')
            if not scope_errors:
                try:deactivate(path)
                except Exception:scope_errors.append('scoped_deactivation')
            errors.extend(scope_errors)
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        (out/'product-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Command Center cleanup incomplete')
    print('M6-01 '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
