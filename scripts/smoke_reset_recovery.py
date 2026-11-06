"""Actual missed reset delivery: ERROR -> newer reset -> missed reset -> destroy."""
import os
from pathlib import Path
import signal
import sys
import time

from acceptance_client import Client
from acceptance_identity import provision,deactivate

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
import provision_node

def stop_node():
    pid=int((runtime.STATE/'node-agent.pid').read_text())
    proc=Path('/proc')/str(pid)
    assert str(ROOT/'node/agent.py').encode() in (proc/'cmdline').read_bytes().split(b'\0')
    os.kill(pid,signal.SIGTERM)
    deadline=time.monotonic()+10
    while proc.exists() and time.monotonic()<deadline:
        try:os.waitpid(pid,os.WNOHANG)
        except ChildProcessError:pass
        time.sleep(.1)
    assert not proc.exists()

def main():
    provision_node.start()
    identity=provision('score',resume='--resume' in sys.argv)
    client=Client(identity)
    active=[lab for lab in client.request('/api/v1/labs') if lab['state']!='DESTROYED']
    lab=active[0] if active else client.wait(client.create(),'READY')
    try:
        for recovery in ('reset','destroy'):
            stop_node()
            lab=client.wait(client.action(lab,'reset'),'ERROR',timeout=150)
            assert lab['error_code']=='node_unavailable'
            print('PASS: actual node outage exhausts reset into ERROR',flush=True)
            provision_node.start()
            lab=client.wait(client.action(lab,recovery),'READY' if recovery=='reset' else 'DESTROYED')
            print('PASS: actual undelivered reset recovered through '+recovery,flush=True)
        assert lab['generation']==4
        for generation in range(1,5):
            for suffix in ('o','t'):
                name='cra-'+lab['id'].replace('-','')[:10]+'-'+format(generation,'x')+suffix
                assert not (runtime.STATE/'vms'/name).exists() and not (runtime.SECRETS/name).exists()
                assert name not in runtime.virsh('list','--all','--name').stdout.splitlines()
        deactivate(identity)
        print('PASS: every owned generation cleaned; synthetic identity deactivated',flush=True)
    finally:
        try:
            if (Path('/proc')/(runtime.STATE/'node-agent.pid').read_text()).exists():stop_node()
        finally:provision_node.start()

if __name__=='__main__':
    try:main()
    except Exception as error:
        import traceback
        print('FAIL: '+type(error).__name__+' at '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__))+' (details withheld)',flush=True)
        raise SystemExit(1) from None
