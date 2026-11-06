"""M6-02 real product capture, partial/DONE node crashes, browser, reset, cleanup."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time
import traceback
from uuid import uuid4
from acceptance_class import provision,deactivate
from acceptance_client import Client
from acceptance_terminal import Terminal
from smoke_desktop import run_browser
from smoke_reconciliation import control

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
import provision_node
from lab_tools import upload


def eventually(predicate,timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.25)
    raise TimeoutError('Product capture condition')


def node_pid():
    pid=int((runtime.STATE/'node-agent.pid').read_text())
    assert str(ROOT/'node/agent.py').encode() in (Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')
    return pid


def proxies():
    parent=node_pid();found=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if int((p/'stat').read_text().split()[3])!=parent:continue
            if str(ROOT/'infra/linux/flow_proxy.py').encode() not in (p/'cmdline').read_bytes().split(b'\0'):continue
            found.append(int(p.name))
        except (OSError,ValueError,IndexError):continue
    return found


def front_states(pids,fronts):
    states=[]
    for pid in pids:
        inodes=set()
        for fd in (Path('/proc')/str(pid)/'fd').iterdir():
            try:value=str(fd.readlink())
            except OSError:continue
            if value.startswith('socket:['):inodes.add(value[8:-1])
        for row in Path('/proc/net/tcp').read_text().splitlines()[1:]:
            fields=row.split()
            if fields[9] in inodes and int(fields[1].split(':')[1],16) in fronts:states.append(fields[3])
    return states


def kill_node(expected):
    pid=node_pid();assert len(proxies())==expected
    children=proxies();os.kill(pid,signal.SIGKILL)
    def reaped():
        # A node started by this harness is our child. Reap it before testing
        # /proc: a dead zombie still has a PID but no command line.
        try:os.waitpid(pid,os.WNOHANG)
        except ChildProcessError:pass
        return not (Path('/proc')/str(pid)).exists()
    eventually(reaped,10)
    eventually(lambda:all(not (Path('/proc')/str(child)/'fd').exists() or not list((Path('/proc')/str(child)/'fd').iterdir()) for child in children),5)
    provision_node.start(restart=False)
    return children


def operation_done(job):
    with sqlite3.connect('file:'+str(runtime.STATE/'node/state.sqlite')+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT status FROM operations WHERE id=?',(job,)).fetchone()
        return bool(row and row[0]=='DONE')


def main():
    paths=[];out=ROOT/'reports/local/m6-02';out.mkdir(parents=True,exist_ok=True)
    report={'task':'M6-02','status':'FAIL','checks':[]};paused=False;streams=[]
    stop=threading.Event();generator=None
    def check(label,result=True):
        assert result,label;report['checks'].append(label);print('PASS: '+label,flush=True)
    try:
        path,b=provision(on_owned=paths.append);teacher=Client(b['users']['teacher']);student=Client(b['users']['student'])
        team=teacher.request('/api/v1/teams',{'request_key':str(uuid4()),'name':'Capture acceptance','members':[{'user_id':b['users']['teacher']['user_id'],'role':'coordinator'},{'user_id':b['users']['student']['user_id'],'role':'operator'}]},expected=201)
        operation=teacher.request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1,'team_id':team['id']},expected=202)
        b['lab_id']=operation['lab_id'];lab=teacher.request('/api/v1/labs/'+b['lab_id']);prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
        # Capture ports are derived from the node journal, never other apps.
        def partial():
            with sqlite3.connect('file:'+str(runtime.STATE/'node/state.sqlite')+'?mode=ro',uri=True) as db:
                row=db.execute('SELECT slot,organization_id,user_id FROM labs WHERE id=?',(b['lab_id'],)).fetchone()
            if not row:return False
            assert row[1:]==(b['organization_id'],b['users']['teacher']['user_id'])
            current=proxies()
            return len(current)==2 and set(front_states(current,{23200+row[0],23210+row[0]}))=={'01','0A'}
        eventually(partial,240)
        kill_node(2)
        check('actual node SIGKILL releases CONNECTED and WAITING proxy descriptors within 5s')
        lab=teacher.wait(operation,'READY',timeout=900)
        check('same durable job resumes partial multioperator provisioning after node crash')
        eventually(lambda:len(teacher.request('/api/v1/command-center/labs/'+lab['id']+'/traffic')['captures'])==2,20)
        for client in (teacher,student):
            own=client.request('/api/v1/labs/'+lab['id']);stream=Terminal(client,own);streams.append(stream)
            stream.command("touch /home/cra/capture-preserve; sync; printf 'FLOW_FILE_%s\\n' READY",b'FLOW_FILE_READY')
            stream.command('nmap -n -Pn -sT -p22,8080 '+own['target_ip']+"; printf 'FLOW_SCAN_%s\\n' DONE",b'FLOW_SCAN_DONE')
        for stream in streams:stream.close()
        streams=[]
        old_capture=teacher.request('/api/v1/command-center/labs/'+lab['id']+'/traffic')['captures']
        old_epochs={c['epoch'] for c in old_capture}
        assert len(old_epochs)==2 and all(c['connected'] for c in old_capture)
        kill_node(2)
        eventually(lambda:teacher.request('/api/v1/labs/'+lab['id'])['error_code']=='runtime_drift',25)
        student.request('/api/v1/labs/'+lab['id']+'/terminal-grants',{},expected=409)
        lab=teacher.wait(teacher.action(teacher.request('/api/v1/labs/'+lab['id']),'start'),'READY')
        for client in (teacher,student):
            stream=Terminal(client,client.request('/api/v1/labs/'+lab['id']));streams.append(stream)
            stream.command("test -f /home/cra/capture-preserve && printf 'FLOW_RECOVERY_%s\\n' PRESERVED",b'FLOW_RECOVERY_PRESERVED')
        for stream in streams:stream.close()
        streams=[]
        def fresh_epochs():
            current=teacher.request('/api/v1/command-center/labs/'+lab['id']+'/traffic')['captures']
            return len(current)==2 and all(c['connected'] for c in current) and {c['epoch'] for c in current}.isdisjoint(old_epochs)
        eventually(fresh_epochs,20)
        check('connected node crash denies grants and coordinated start preserves both operator files with new epochs')
        # Freeze worker while node is executing reset; leave the response unread
        # until DONE is durably committed, then kill node and worker.
        reset=teacher.action(lab,'reset')
        eventually(lambda:any(p.name.startswith(prefix+'2') for p in (runtime.STATE/'vms').iterdir()),60)
        paused=True;control('sudo systemctl kill --signal=SIGSTOP --kill-whom=main cra-worker')
        eventually(lambda:operation_done(reset['operation_id']),300)
        kill_node(2)
        control('sudo systemctl kill --signal=SIGKILL --kill-whom=main cra-worker && sudo systemctl start cra-worker');paused=False
        lab=teacher.wait(reset,'READY',timeout=600)
        check('DONE-before-ACK node+worker crash replays same operation and repairs current links without exhausting retries',lab['generation']==2)
        for client in (teacher,student):
            stream=Terminal(client,client.request('/api/v1/labs/'+lab['id']));streams.append(stream)
            stream.command("test ! -e /home/cra/capture-preserve && printf 'FLOW_RESET_%s\\n' CLEAN",b'FLOW_RESET_CLEAN')
        for stream in streams:stream.close()
        streams=[]
        errors=[]
        def traffic():
            local=[]
            try:
                for client in (teacher,student):
                    own=client.request('/api/v1/labs/'+lab['id']);local.append((Terminal(client,own),own['target_ip']))
                while not stop.is_set():
                    for stream,address in local:stream.command('curl -fsS http://'+address+":8080/ >/dev/null; printf 'REAL_FLOW_%s\\n' GENERATED",b'REAL_FLOW_GENERATED')
                    stop.wait(1)
            except Exception:errors.append('traffic_generator')
            finally:
                for stream,_ in local:stream.close()
        generator=threading.Thread(target=traffic);generator.start()
        upload('cra-control','/home/cra/cra-deploy/browser_teaching.py',(ROOT/'scripts/browser_teaching.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser_teaching.py /opt/cra-browser/teaching.py')
        run_browser('browser_flow_capture.py',b,timeout=150)
        stop.set();generator.join(20);assert not generator.is_alive() and not errors
        value=teacher.request('/api/v1/command-center/labs/'+lab['id']+'/traffic')
        assert len(value['captures'])==2 and all(c['connected'] for c in value['captures'])
        assert value['events'] and all(e['generation']==2 and e['evidence_level']=='observed' for e in value['events'])
        assert {e['operator_user_id'] for e in value['events']}=={b['users'][role]['user_id'] for role in ('teacher','student')}
        assert all(row['earned_points']==0 for row in teacher.request('/api/v1/command-center/overview')['labs'])
        check('actual two-operator traffic is attributed, current-generation only and awards no points; sandboxed browser acceptance passes')
        report['status']='PASS'
    except Exception as error:
        report['failure']={'type':type(error).__name__,'lines':[f.lineno for f in traceback.extract_tb(error.__traceback__)]}
        raise
    finally:
        stop.set()
        if generator:generator.join(20)
        for stream in streams:stream.close()
        errors=[]
        try:
            if paused:control('sudo systemctl kill --signal=SIGCONT --kill-whom=main cra-worker');paused=False
            provision_node.start(restart=False)
            control('sudo systemctl start cra-worker')
        except Exception:errors.append('project_process_recovery')
        for path in paths:
            b=json.loads(path.read_text());scoped=[]
            try:
                client=Client(b['users']['teacher'])
                for lab in client.request('/api/v1/labs'):
                    try:
                        eventually(lambda:client.request('/api/v1/labs/'+lab['id'])['state'] in ('READY','STOPPED','ERROR','DESTROYED'),900)
                        lab=client.request('/api/v1/labs/'+lab['id'])
                        if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED',timeout=300)
                        prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                        assert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.splitlines())
                        assert not any(p.name.startswith(prefix) for p in (runtime.STATE/'vms').iterdir())
                        assert not any(p.name.startswith(prefix) for p in runtime.SECRETS.iterdir())
                    except Exception:scoped.append('owned_lab_cleanup')
                if not scoped:deactivate(path)
            except Exception:scoped.append('scoped_identity_cleanup')
            errors.extend(scoped)
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        (out/'product-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Product capture cleanup incomplete')
    print('M6-02 product '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
