"""M6-03 real VM/score/traffic and browser projection; synthetic class only."""
import json
from pathlib import Path
import re
import sys
import threading
import time
import traceback
from acceptance_class import provision,deactivate
from acceptance_client import Client
from acceptance_terminal import Terminal
from smoke_desktop import run_browser
from smoke_reconciliation import control

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload


def main():
    paths=[];streams=[];stop=threading.Event();generator=None;errors=[]
    report={'task':'M6-03','status':'FAIL'};out=ROOT/'reports/local/m6-03';out.mkdir(parents=True,exist_ok=True)
    try:
        path,b=provision(on_owned=paths.append);student=Client(b['users']['student'])
        lab=student.wait(student.create(),'READY');b['lab_id']=lab['id'];b['target_ip']=lab['target_ip']
        teacher=Client(b['users']['teacher']);other=teacher.wait(teacher.create(),'READY');b['other_lab_id']=other['id']
        stream=Terminal(student,lab);streams.append(stream)
        text=stream.command('curl -fsS http://'+lab['target_ip']+":8080/; printf 'PROJECTOR_FLAG_%s\\n' READ",b'PROJECTOR_FLAG_READ')
        flag=re.search(rb'CRA\{[0-9a-f]{32}\}',text)[0].decode()
        assert student.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':1,'flag':flag})['points_awarded']==100
        stream.close();streams=[]
        def traffic():
            stream=None
            try:
                stream=Terminal(student,lab)
                while not stop.is_set():
                    stream.command('curl -fsS http://'+lab['target_ip']+":8080/ >/dev/null; printf 'PROJECTOR_FLOW_%s\\n' OK",b'PROJECTOR_FLOW_OK');stop.wait(1)
            except Exception:errors.append('traffic_generator')
            finally:
                if stream:stream.close()
        generator=threading.Thread(target=traffic);generator.start()
        upload('cra-control','/home/cra/cra-deploy/browser_teaching.py',(ROOT/'scripts/browser_teaching.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser_teaching.py /opt/cra-browser/teaching.py')
        run_browser('browser_projector.py',b,timeout=150)
        stop.set();generator.join(20);assert not generator.is_alive() and not errors
        report['status']='PASS'
    except Exception as error:
        report['failure']={'type':type(error).__name__,'lines':[f.lineno for f in traceback.extract_tb(error.__traceback__)]};raise
    finally:
        stop.set()
        if generator:generator.join(20)
        for stream in streams:stream.close()
        cleanup=[]
        for path in paths:
            b=json.loads(path.read_text())
            try:
                scoped=[]
                for role in ('teacher','student'):
                    try:
                        client=Client(b['users'][role]);owned=client.request('/api/v1/labs')
                    except Exception:
                        scoped.append('owned_role_access');continue
                    for lab in owned:
                        try:
                            end=time.monotonic()+900
                            while client.request('/api/v1/labs/'+lab['id'])['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                                if time.monotonic()>end:raise TimeoutError('Owned provisioning cleanup wait')
                                time.sleep(1)
                            lab=client.request('/api/v1/labs/'+lab['id'])
                            if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED')
                            prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                            assert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.splitlines())
                            assert not any(p.name.startswith(prefix) for p in (runtime.STATE/'vms').iterdir())
                            assert not any(p.name.startswith(prefix) for p in runtime.SECRETS.iterdir())
                        except Exception:scoped.append('owned_lab_cleanup')
                if scoped:raise RuntimeError('Owned cleanup incomplete')
                deactivate(path)
            except Exception:cleanup.append('owned_scope_cleanup')
        report['cleanup_errors']=cleanup
        if cleanup:report['status']='FAIL'
        (out/'projector-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if cleanup:raise RuntimeError('Projector cleanup incomplete')
    print('M6-03 projector '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
