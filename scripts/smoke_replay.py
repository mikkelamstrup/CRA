"""M6-04 actual VM generations, historical evidence and measured browser budgets."""
import json
from pathlib import Path
import re
import sys
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
    paths=[];stream=None
    report={'task':'M6-04','status':'FAIL'};out=ROOT/'reports/local/m6-04';out.mkdir(parents=True,exist_ok=True)
    try:
        path,b=provision(on_owned=paths.append);student=Client(b['users']['student']);teacher=Client(b['users']['teacher'])
        lab=student.wait(student.create(),'READY');b['lab_id']=lab['id']
        stream=Terminal(student,lab)
        text=stream.command('curl -fsS http://'+lab['target_ip']+":8080/; printf 'REPLAY_FLAG_%s\\n' READ",b'REPLAY_FLAG_READ')
        flag=re.search(rb'CRA\{[0-9a-f]{32}\}',text)[0].decode()
        assert student.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':1,'flag':flag})['points_awarded']==100
        stream.close();stream=None
        end=time.monotonic()+20
        while not teacher.request('/api/v1/command-center/labs/'+lab['id']+'/traffic')['events']:
            if time.monotonic()>end:raise TimeoutError('Real flow absent before reset')
            time.sleep(1)
        lab=student.wait(student.action(lab,'reset'),'READY');assert lab['generation']==2
        assert not student.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':2,'flag':flag})['accepted']
        stream=Terminal(student,lab)
        text=stream.command('curl -fsS http://'+lab['target_ip']+":8080/; printf 'REPLAY_NEXT_%s\\n' READ",b'REPLAY_NEXT_READ')
        b['flag']=re.search(rb'CRA\{[0-9a-f]{32}\}',text)[0].decode()
        stream.command("clear; tmux clear-history; printf 'REPLAY_SCREEN_%s\\n' CLEAN",b'REPLAY_SCREEN_CLEAN')
        # Traffic continues inside the owned VM while the browser uses its PTY.
        stream.command('nohup sh -c '+__import__('shlex').quote('for i in $(seq 1 240); do curl -fsS http://'+lab['target_ip']+':8080/ >/dev/null; sleep 1; done')+" >/dev/null 2>&1 & printf 'REPLAY_TRAFFIC_%s\\n' STARTED",b'REPLAY_TRAFFIC_STARTED')
        stream.close();stream=None
        upload('cra-control','/home/cra/cra-deploy/browser_teaching.py',(ROOT/'scripts/browser_teaching.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser_teaching.py /opt/cra-browser/teaching.py')
        run_browser('browser_replay.py',b,timeout=180)
        report['status']='PASS'
    except Exception as error:
        report['failure']={'type':type(error).__name__,'lines':[f.lineno for f in traceback.extract_tb(error.__traceback__)]};raise
    finally:
        if stream:stream.close()
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
        (out/'replay-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if cleanup:raise RuntimeError('Replay cleanup incomplete')
    print('M6-04 replay '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
