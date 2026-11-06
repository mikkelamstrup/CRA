"""M4-01 real filesystem mission, notebook, browser and reset acceptance."""
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
    paths=[];report={'task':'M4-01','scenario':'course-filesystem','status':'FAIL'}
    out=ROOT/'reports/local/m4-01';out.mkdir(parents=True,exist_ok=True)
    try:
        path=provision('course',on_owned=paths.append);client=Client(path)
        before=time.monotonic();lab=client.wait(client.request('/api/v1/labs',{'mission_id':'first-files','mission_version':1},expected=202),'READY');report['create_seconds']=round(time.monotonic()-before,2)
        name='cra-'+lab['id'].replace('-','')[:10]+'-1o'
        import re
        result=runtime.ssh(name,"cd /home/cra/academy && cat README.txt && grep NL-104 journal/events.log && cat arkiv/2026/rapport-104.txt")
        assert result.returncode==0
        flag=re.search(r'CRA\{[0-9a-f]{32}\}',result.stdout).group()
        answer=client.request('/api/v1/labs/'+lab['id']+'/objectives/find-evidence/attempts',{'generation':1,'flag':flag})
        assert answer['points_awarded']==100
        upload('cra-control','/home/cra/cra-deploy/browser-course.py',(ROOT/'scripts/browser_course.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser-course.py /opt/cra-browser/course.py')
        result=runtime.ssh('cra-control','sudo -u cra-browser /opt/cra/backend/.venv/bin/python /opt/cra-browser/course.py',input=path.read_text(),timeout=180)
        print(result.stdout,end='',flush=True)
        if result.returncode:raise RuntimeError('Browser course acceptance failed')
        proof=runtime.ssh(name,'touch /home/cra/academy/reset-proof && sync && test -f /home/cra/academy/reset-proof')
        assert proof.returncode==0
        lab=client.wait(client.action(lab,'reset'),'READY')
        new_name='cra-'+lab['id'].replace('-','')[:10]+'-2o'
        result=runtime.ssh(new_name,'test ! -f /home/cra/academy/reset-proof && cat /home/cra/academy/arkiv/2026/rapport-104.txt')
        assert result.returncode==0
        new_flag=re.search(r'CRA\{[0-9a-f]{32}\}',result.stdout).group()
        assert new_flag!=flag
        assert not client.request('/api/v1/labs/'+lab['id']+'/objectives/find-evidence/attempts',{'generation':2,'flag':flag})['accepted']
        answer=client.request('/api/v1/labs/'+lab['id']+'/objectives/find-evidence/attempts',{'generation':2,'flag':new_flag})
        assert answer['accepted'] and answer['points_awarded']==0 and answer['total_points']==100
        note=client.request('/api/v1/course/days/1/notebook')
        assert note['theory_read'] and 'NL-104' in note['body'] and note['revision']==2
        print('PASS: real file solution, fresh reset flag and retained notebook/score',flush=True)
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
        report['cleanup_errors']=errors;(out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Recovery acceptance cleanup incomplete')
    print('M4-01 course '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
