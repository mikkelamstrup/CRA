"""M3-02 actual connection/service faults; no fabricated terminal output."""
import json
from pathlib import Path
import re
import shlex
import socket
import sys
import time
import traceback
from uuid import uuid4
from websockets.exceptions import ConnectionClosed,InvalidStatus

from acceptance_client import Client
from acceptance_identity import provision,deactivate
from acceptance_terminal import Terminal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload

def control(command):
    result=runtime.ssh('cra-control',command,timeout=45)
    if result.returncode:raise RuntimeError('Owned control operation failed')

def active(path,enabled):
    identity=json.loads(path.read_text());identity['active']=enabled
    code='''import json,sys
from cra.db import connect
d=json.load(sys.stdin)
assert d['purpose']=='cra-acceptance-session' and d['username'].startswith('cra_test_')
with connect() as db:
 assert db.execute('UPDATE users SET active=%s WHERE id=%s AND username=%s',(d['active'],d['user_id'],d['username'])).rowcount==1
'''
    result=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code),input=json.dumps(identity))
    if result.returncode:raise RuntimeError('Synthetic account state change failed')

def closed(terminal,seconds=8):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        try:terminal.read(max(.1,end-time.monotonic()))
        except ConnectionClosed:return
    raise TimeoutError('Revocation did not close connection')

def ready_terminal(client,lab):
    deadline=time.monotonic()+20
    while True:
        try:return Terminal(client,lab)
        except InvalidStatus as error:
            if error.response.status_code not in (502,503) or time.monotonic()>deadline:raise
            time.sleep(.3)


def main():
    paths=[];terminal=None;client=None;lab=None
    out=ROOT/'reports/local/m3-02';out.mkdir(parents=True,exist_ok=True)
    report={'task':'M3-02','status':'FAIL','started_at':time.time(),'checks':[]}
    def passed(label):report['checks'].append(label);print('PASS: '+label,flush=True)
    try:
        path=provision('session',resume='--resume' in sys.argv,on_owned=paths.append);client=Client(path)
        existing=[l for l in client.request('/api/v1/labs') if l['state']!='DESTROYED']
        lab=existing[0] if existing else client.wait(client.create(),'READY')
        upload('cra-control','/home/cra/cra-deploy/browser-terminal-safety.py',(ROOT/'scripts/browser_terminal_safety.py').read_bytes())
        control('sudo install -m 0644 /home/cra/cra-deploy/browser-terminal-safety.py /opt/cra-browser/terminal-safety.py')
        result=runtime.ssh('cra-control','sudo -u cra-browser /opt/cra/backend/.venv/bin/python /opt/cra-browser/terminal-safety.py',input=path.read_text(),timeout=90)
        if result.returncode:raise RuntimeError('Actual browser terminal safety failed')
        print(result.stdout,end='',flush=True);passed('browser HTML/OSC52 handling')
        terminal=ready_terminal(client,lab)
        def run(command):
            token=uuid4().hex
            return terminal.command(command+"; printf 'END_%s\\n' "+token,('END_'+token).encode())
        def probe():
            token=uuid4().hex
            output=run("printf 'PROBE_"+token+":%s:%s\\n' \"$$\" \"$(wc -l </home/cra/reconnect-count)\"")
            match=re.search(('PROBE_'+token+':([0-9]+):([0-9]+)').encode(),output)
            assert match is not None
            return match[1],int(match[2])
        def fault_command():
            run(': > /home/cra/reconnect-count')
            token=uuid4().hex
            terminal.command("printf 'once\\n' >> /home/cra/reconnect-count; printf 'START_%s\\n' "+token+"; sleep 12; printf 'FINISH_%s\\n' "+token,('START_'+token).encode())
        run(': > /home/cra/reconnect-count');pid,count=probe();assert count==0
        fault_command()
        terminal.ws.socket.shutdown(socket.SHUT_RDWR);terminal.close();terminal=None
        terminal=ready_terminal(client,lab)
        assert probe()==(pid,1)
        passed('socket interrupted mid-command; same live shell and exactly one input execution')
        for service in ('cra-api','cra-relay'):
            fault_command()
            control('sudo systemctl restart '+service)
            if service=='cra-relay':closed(terminal)
            terminal.close();terminal=None
            deadline=time.monotonic()+20
            while True:
                try:
                    assert client.request('/api/v1/auth/me')['user_id']==client.identity['user_id'];break
                except Exception:
                    if time.monotonic()>deadline:raise
                    time.sleep(.5)
            terminal=ready_terminal(client,lab)
            assert probe()==(pid,1)
            passed(service+' restart during command preserves login, shell PID and exactly one execution')
        before=time.monotonic();client.request('/api/v1/auth/logout',{});closed(terminal)
        report['logout_close_seconds']=round(time.monotonic()-before,3)
        terminal.close();terminal=None
        client.request('/api/v1/labs/'+lab['id']+'/terminal-grants',{},expected=401)
        passed('logout closes existing WS and denies new grant')
        client=Client(path);terminal=ready_terminal(client,lab)
        before=time.monotonic();active(path,False);closed(terminal)
        report['disable_close_seconds']=round(time.monotonic()-before,3)
        terminal.close();terminal=None
        client.request('/api/v1/auth/me',expected=401)
        passed('disabled account closes live WS and denies authenticated HTTP')
        active(path,True)
        report['status']='PASS'
    finally:
        cleanup_errors=[]
        if terminal:
            try:terminal.close()
            except Exception:cleanup_errors.append('terminal_close')
        for path in paths:
            try:
                active(path,True);client=Client(path)
                for lab in client.request('/api/v1/labs'):
                    deadline=time.monotonic()+300
                    while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                        if time.monotonic()>deadline:raise TimeoutError('Cleanup pending job')
                        time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                    if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED')
                deactivate(path)
            except Exception:cleanup_errors.append('identity_cleanup')
        if cleanup_errors:report['status']='FAIL'
        report['cleanup_errors']=cleanup_errors;report['finished_at']=time.time()
        (out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if cleanup_errors:raise RuntimeError('Scoped acceptance cleanup incomplete')
    print('M3-02 '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
