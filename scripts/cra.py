#!/usr/bin/env python3
"""Project commands. Services run in the trusted control VM."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'.local/platform-tools'

def environment():
    env=os.environ.copy()
    env.update(UV_CACHE_DIR=str(ROOT/'.local/uv-cache'),NPM_CONFIG_USERCONFIG='/dev/null',
               XDG_CONFIG_HOME=str(ROOT/'.local/pnpm-config'))
    return env

def run(command,cwd=ROOT):
    subprocess.run(list(map(str,command)),cwd=cwd,env=environment(),check=True)

def bootstrap():
    lock=json.loads((ROOT/'infra/platform-tools.lock.json').read_text())
    node=subprocess.run(['node','--version'],capture_output=True,text=True,check=True).stdout.strip()
    if node!='v'+lock['node']:raise RuntimeError('Install the Node version from .node-version first')
    TOOLS.mkdir(parents=True,exist_ok=True)
    for name,filename in [('uv','uv'),('pnpm_native','pnpm-native')]:
        pkg=lock[name];dest=TOOLS/filename
        if dest.exists() and hashlib.file_digest(dest.open('rb'),'sha256').hexdigest()==pkg['binary_sha256']:continue
        with urllib.request.urlopen(pkg['url'],timeout=60) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=pkg['sha256']:raise RuntimeError('Tool archive checksum mismatch')
        if name=='uv':
            with zipfile.ZipFile(io.BytesIO(data)) as archive:binary=archive.read(pkg['binary_entry'])
        else:
            with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
                member=archive.extractfile(pkg['binary_entry'])
                if member is None:raise RuntimeError('Missing package-manager binary')
                binary=member.read()
        if hashlib.sha256(binary).hexdigest()!=pkg['binary_sha256']:raise RuntimeError('Tool binary checksum mismatch')
        dest.write_bytes(binary);dest.chmod(0o755)

def setup():
    bootstrap()
    run([TOOLS/'uv','sync','--locked','--python',sys.executable,'--no-python-downloads'],ROOT/'backend')
    run([TOOLS/'pnpm-native','install','--frozen-lockfile'],ROOT/'frontend')

def build():run([TOOLS/'pnpm-native','build'],ROOT/'frontend')

def test_fast():
    run([sys.executable,'-B','-m','unittest','discover','-s','scripts','-p','test_*.py','-v'])
    run([sys.executable,'-B','scripts/check_pack.py'])
    for tool,args in [('ruff',['check','cra','tests']),('mypy',['cra']),('pytest',['-q'])]:
        run([ROOT/'backend/.venv/bin'/tool,*args],ROOT/'backend')
    run([ROOT/'backend/.venv/bin/pytest','-q','node'])
    run([TOOLS/'pnpm-native','typecheck'],ROOT/'frontend')

def control(command):
    sys.path.insert(0,str(ROOT/'infra/linux'))
    from runtime import ssh
    result=ssh('cra-control',command,timeout=120)
    print(result.stdout,end='')
    if result.returncode:raise RuntimeError('Control command failed; inspect only project logs')

def control_services(action):
    """Include the installed second worker when starting/stopping the control plane."""
    import shlex
    assert action in ('start','stop','is-active')
    code="""from pathlib import Path
import subprocess
names=['cra-api','cra-worker','cra-reconciler','cra-relay','cra-edge']
if Path('/etc/systemd/system/cra-worker-node2.service').is_file():names.append('cra-worker-node2')
action=ACTION
if action!='stop':names.insert(0,'postgresql@16-main')
if action=='is-active':
 for name in names:subprocess.run(['systemctl','is-active',name],check=True)
else:subprocess.run(['sudo','systemctl',action,*names],check=True)
""".replace('ACTION',repr(action))
    control('python3 -c '+shlex.quote(code))

def integration():
    control("cd /opt/cra/backend && sudo -u craapp env CRA_TEST_DATABASE_DSN='dbname=cra_test user=craapp host=/var/run/postgresql' .venv/bin/python -m pytest -q")
    for path in ['/api/v1/health','/api/v1/ready','/']:
        with urllib.request.urlopen('http://127.0.0.1:28000'+path,timeout=5) as response:
            if response.status!=200:raise RuntimeError('Control HTTP probe failed')
            if path=='/api/v1/ready' and not json.load(response)['ready']:raise RuntimeError('Database not ready')
        print(path+' PASS')

def up():
    sys.path.insert(0,str(ROOT/'infra/linux'))
    import runtime
    import provision_node
    if not (runtime.STATE/'vms/cra-control/manifest.json').exists():
        raise RuntimeError('Existing owned control VM required; follow the installation runbook')
    runtime.start_daemons()
    if runtime.virsh('domstate','cra-control').stdout.strip()!='running':runtime.start('cra-control')
    runtime.wait_ready('cra-control')
    provision_node.start(restart=False)
    control_services('start')
    deadline=time.monotonic()+30
    while True:
        try:
            with urllib.request.urlopen('http://127.0.0.1:28000/api/v1/ready',timeout=3) as response:
                if json.load(response).get('ready'):break
        except (OSError,ValueError):pass
        if time.monotonic()>deadline:raise RuntimeError('Control readiness timed out')
        time.sleep(.25)
    status()


def status():
    control_services('is-active')
    for path in ['/api/v1/health','/api/v1/ready']:
        with urllib.request.urlopen('http://127.0.0.1:28000'+path,timeout=5) as response:
            value=json.load(response)
            if path.endswith('/ready') and not value.get('ready'):raise RuntimeError('Control database not ready')
        print(path+' PASS')
    code="""from cra.db import connect
from cra.node_client import request
from cra.node_contracts import NodeInventory
from cra.compute_nodes import nodes
for identity in nodes():
 inventory=NodeInventory.model_validate(request('/v1/inventory',node_id=identity))
 print('Node',identity,'connected; active reservations:',len(inventory.labs))
with connect() as db:
 for row in db.execute("SELECT state,count(*) AS n FROM labs WHERE state<>'DESTROYED' GROUP BY state"):print('Lab state:',row['state'],row['n'])
 for row in db.execute("SELECT status,count(*) AS n FROM node_orphans WHERE status<>'RESOLVED' GROUP BY status"):print('Orphan state:',row['status'],row['n'])
"""
    import shlex
    control('cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['doctor','setup','build','dev','test-fast','test-integration','up','status','down']);args=parser.parse_args()
    if args.command=='up':up()
    elif args.command=='status':status()
    elif args.command=='doctor':run([sys.executable,'scripts/doctor.py','--json'])
    elif args.command=='setup':setup()
    elif args.command=='build':build()
    elif args.command=='test-fast':test_fast()
    elif args.command=='test-integration':integration()
    elif args.command=='dev':
        build();run([sys.executable,'infra/linux/provision_control.py','--deploy'])
    elif args.command=='down':control_services('stop')

if __name__=='__main__':main()
