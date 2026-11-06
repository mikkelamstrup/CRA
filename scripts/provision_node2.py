"""Prepare/start the approved node2-example node with its own keys and project-local tools.

Does not change host SSH, Tailscale, firewall, services, packages or saved node 1 labs.
Credentials travel only through private SSH stdin/stdout buffers, never command lines/logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from stage_node2_runtime import ROOT, SSH, TARGET, REMOTE_SOURCE, REMOTE_RUNTIME


def remote(code, *, timeout=60):
    result=subprocess.run([*SSH,TARGET,'python3 -'],input=code,text=True,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('Node 2 operation failed (private output withheld)')
    return result.stdout


def verify_owned():
    remote('''import os,json
from pathlib import Path
for name in ['/home/crauser/cra-node2','/home/crauser/cra-m0-02']:
 p=Path(name);assert not p.is_symlink()
 assert json.loads((p/'.cra-node2-stage.json').read_text())=={'purpose':'CRA-M8-03-stage','source_host_runtime_excluded':True}
assert os.access('/dev/kvm',os.R_OK|os.W_OK)
''')


def prepare():
    verify_owned()
    remote('''from pathlib import Path
import os
root=Path('/home/crauser/cra-m0-02/.runtime')
assert not list((root/'vms').glob('*')), 'Prepare only an empty node'
p=root/'node-agent.pid'
if p.exists():
 try:os.kill(int(p.read_text()),0)
 except ProcessLookupError:pass
 else:raise RuntimeError('Node is running')
''')
    tool=ROOT/'.local/platform-tools/uv'
    lock=json.loads((ROOT/'infra/platform-tools.lock.json').read_text())
    assert hashlib.sha256(tool.read_bytes()).hexdigest()==lock['uv']['binary_sha256']
    transport=shlex.join(SSH)
    names=['infra','node','content','backend/cra','backend/pyproject.toml','backend/uv.lock']
    subprocess.run(['rsync','-a','--checksum','--relative','--exclude=__pycache__/','--exclude=*.pyc','-e',transport,
        *[str(ROOT)+'/./'+name for name in names],TARGET+':'+REMOTE_SOURCE+'/'],check=True)
    remote("from pathlib import Path; Path('/home/crauser/cra-node2/.local/platform-tools').mkdir(parents=True,exist_ok=True)")
    subprocess.run(['rsync','-a','--checksum','-e',transport,str(tool),TARGET+':'+REMOTE_SOURCE+'/.local/platform-tools/uv'],check=True)
    code='''import hashlib,os,subprocess,sys
from pathlib import Path
root=Path('/home/crauser/cra-node2')
assert hashlib.sha256((root/'.local/platform-tools/uv').read_bytes()).hexdigest()==EXPECTED
os.environ.update(CRA_NODE_HOME='/home/crauser/cra-m0-02',CRA_NODE_ID='2',CRA_NODE_BIND='100.64.0.2',UV_CACHE_DIR=str(root/'.local/uv-cache'))
subprocess.run([root/'.local/platform-tools/uv','sync','--project',root/'backend','--locked','--no-dev','--python','/usr/bin/python3','--no-python-downloads'],check=True,capture_output=True)
sys.path.insert(0,str(root/'infra/linux'))
import provision_node
provision_node.certificates(server_addresses=('100.64.0.2',))
provision_node.provision_flags()
# Fresh node2 signing key, never copied from node1.
import secrets
key=Path('/home/crauser/cra-m0-02/.secrets/terminal-signing.key')
if not key.exists():
 with os.fdopen(os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as stream:stream.write(secrets.token_bytes(32))
assert key.stat().st_size==32 and not key.stat().st_mode&0o077
'''.replace('EXPECTED',repr(lock['uv']['binary_sha256']))
    remote(code,timeout=240)
    print('PASS: node2 project source, locked dependencies, separate CA/signing/flag keys prepared')


def credentials():
    verify_owned()
    # Only node2 client/relay material and its own signing key leave node2-example.
    raw=remote('''import base64,json
from pathlib import Path
root=Path('/home/crauser/cra-m0-02/.secrets')
names=['node-tls/ca.crt','node-tls/client.crt','node-tls/client.key','node-tls/relay.crt','node-tls/relay.key','terminal-signing.key']
print(json.dumps({name:base64.b64encode((root/name).read_bytes()).decode() for name in names}))
''')
    payload=json.loads(raw)
    os.environ['CRA_NODE_HOME']='/home/crauser/cra-m0-02'
    sys.path.insert(0,str(ROOT/'infra/linux'))
    import runtime
    for user,folder,files in [
        ('craapp','/var/lib/cra/node-tls/2',{'ca.crt':'node-tls/ca.crt','client.crt':'node-tls/client.crt','client.key':'node-tls/client.key'}),
        ('crarelay','/var/lib/cra-relay/node-tls/2',{'ca.crt':'node-tls/ca.crt','relay.crt':'node-tls/relay.crt','relay.key':'node-tls/relay.key'}),
        ('craapp','/var/lib/cra/node-signing',{'2.key':'terminal-signing.key'}),
    ]:
        result=runtime.ssh('cra-control','sudo install -d -m 0700 -o '+user+' -g '+user+' '+folder)
        if result.returncode:raise RuntimeError('Cannot prepare private node2 scope')
        code='''import os,sys,json,base64
from pathlib import Path
folder=Path(FOLDER);assert not folder.is_symlink()
for name,encoded in json.load(sys.stdin).items():
 path=folder/name;data=base64.b64decode(encoded,validate=True)
 if path.exists():
  assert not path.is_symlink() and path.read_bytes()==data, 'Refuse to replace node identity'
 else:
  with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as stream:stream.write(data)
 assert not path.stat().st_mode&0o077
'''.replace('FOLDER',repr(folder))
        result=runtime.ssh('cra-control','sudo -u '+user+' python3 -c '+shlex.quote(code),input=json.dumps({name:payload[source] for name,source in files.items()}))
        if result.returncode:raise RuntimeError('Private node2 credential transfer failed')
    print('PASS: separate node2 control and relay credentials installed privately')


def start():
    verify_owned()
    remote('''import os,sys,json
from pathlib import Path
os.environ.update(CRA_NODE_HOME='/home/crauser/cra-m0-02',CRA_NODE_ID='2',CRA_NODE_BIND='100.64.0.2')
sys.path.insert(0,'/home/crauser/cra-node2/infra/linux')
import provision_node
provision_node.start(restart=False)
''')
    print('PASS: project node2 process started; application acceptance still required')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','credentials','start'])
    args=parser.parse_args()
    {'prepare':prepare,'credentials':credentials,'start':start}[args.action]()


if __name__=='__main__':main()
