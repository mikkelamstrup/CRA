"""M3-03 focused actual UI recovery acceptance after an owned VM fault."""
import json
from pathlib import Path
import sys
import time
import traceback
from acceptance_client import Client
from acceptance_identity import provision,deactivate
from smoke_reconciliation import control,wait_lab

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload

def main():
    paths=[];report={'task':'M3-03','scenario':'ui-recovery-start','status':'FAIL'}
    out=ROOT/'reports/local/m3-03';out.mkdir(parents=True,exist_ok=True)
    try:
        path=provision('reconcile',on_owned=paths.append);client=Client(path)
        before=time.monotonic();lab=client.wait(client.create(),'READY');report['create_seconds']=round(time.monotonic()-before,2)
        name='cra-'+lab['id'].replace('-','')[:10]+'-1o'
        result=runtime.ssh(name,"printf synthetic-recovery > /home/cra/recovery-proof && sync")
        assert result.returncode==0
        runtime.stop(name);wait_lab(client,lab,'ERROR','runtime_drift')
        upload('cra-control','/home/cra/cra-deploy/browser-recovery-start.py',(ROOT/'scripts/browser_recovery_start.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser-recovery-start.py /opt/cra-browser/recovery-start.py')
        result=runtime.ssh('cra-control','sudo -u cra-browser /opt/cra/backend/.venv/bin/python /opt/cra-browser/recovery-start.py',input=path.read_text(),timeout=240)
        if result.returncode:raise RuntimeError('Browser recovery acceptance failed')
        print(result.stdout,end='',flush=True)
        actual=client.request('/api/v1/labs/'+lab['id']);assert actual['state']=='READY' and actual['generation']==1
        report['status']='PASS'
    finally:
        errors=[]
        for path in paths:
            try:
                client=Client(path)
                for lab in client.request('/api/v1/labs'):
                    deadline=time.monotonic()+300
                    while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                        if time.monotonic()>deadline:raise TimeoutError('Cleanup pending lifecycle')
                        time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                    if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED')
                deactivate(path)
            except Exception:errors.append('scoped_cleanup')
        if errors:report['status']='FAIL'
        report['cleanup_errors']=errors;(out/'ui-recovery.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Recovery acceptance cleanup incomplete')
    print('M3-03 UI recovery '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
