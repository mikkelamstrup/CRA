"""M4-03 real class-flow acceptance. Only owned synthetic resources are cleaned."""
import json
from pathlib import Path
import sys
import time
import traceback
from acceptance_client import Client
from acceptance_class import provision,deactivate
from smoke_reconciliation import control

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload


def main():
    paths=[];report={'task':'M4-03','status':'FAIL'}
    out=ROOT/'reports/local/m4-03';out.mkdir(parents=True,exist_ok=True)
    try:
        path,bundle=provision(on_owned=paths.append)
        client=Client(bundle['users']['student'])
        upload('cra-control','/home/cra/cra-deploy/browser-teaching.py',(ROOT/'scripts/browser_teaching.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser-teaching.py /opt/cra-browser/browser_teaching.py')
        upload('cra-control','/home/cra/cra-deploy/browser-accessibility.py',(ROOT/'scripts/browser_accessibility.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser-accessibility.py /opt/cra-browser/accessibility.py')
        result=runtime.ssh('cra-control','sudo -u cra-browser /opt/cra/backend/.venv/bin/python /opt/cra-browser/accessibility.py',input=path.read_text(),timeout=240)
        print(result.stdout,end='',flush=True)
        if result.returncode:raise RuntimeError('Browser teaching acceptance failed')
        report['status']='PASS'
    finally:
        errors=[]
        for path in paths:
            try:
                bundle=json.loads(path.read_text())
                for identity in bundle['users'].values():
                    client=Client(identity)
                    for lab in client.request('/api/v1/labs'):
                        deadline=time.monotonic()+300
                        while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                            if time.monotonic()>deadline:raise TimeoutError('Cleanup lifecycle')
                            time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                        if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED')
                deactivate(path)
            except Exception:errors.append('scoped_cleanup')
        if errors:report['status']='FAIL'
        report['cleanup_errors']=errors;(out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Teaching cleanup incomplete')
    print('M4-03 teaching '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
