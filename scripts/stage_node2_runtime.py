"""Stage public source and immutable runtime tools on the owner-selected node 2.

Uses only the dedicated project SSH key and verified host key. Never copies
node journals, VM disks, control data or any existing runtime credentials.
"""
import hashlib,json,os,shlex,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE_RUNTIME=Path('/home/crauser/cra-m0-02')
TARGET='crauser@100.64.0.2'
REMOTE_SOURCE='/home/crauser/cra-node2'
REMOTE_RUNTIME='/home/crauser/cra-m0-02'
SSH=['ssh','-F','/dev/null','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/home/crauser/cra-node2-preflight/known_hosts','-o','IdentityAgent=none','-o','IdentitiesOnly=yes','-i','/home/crauser/cra-node2-preflight/.secrets/admin_ed25519']


def remote(code):
    result=subprocess.run([*SSH,TARGET,'python3 -'],input=code,text=True,capture_output=True,timeout=45)
    if result.returncode:raise RuntimeError('Node2 staging precondition failed')
    return result.stdout


def main():
    # Exact directories must be absent, or carry this staging operation's marker.
    remote('''import os,json,fcntl
from pathlib import Path
assert os.access('/dev/kvm',os.R_OK|os.W_OK)
fd=os.open('/dev/kvm',os.O_RDWR)
try:assert fcntl.ioctl(fd,0xAE00,0)==12
finally:os.close(fd)
for name in ['/home/crauser/cra-node2','/home/crauser/cra-m0-02']:
 p=Path(name);marker=p/'.cra-node2-stage.json'
 if p.exists():
  assert p.is_dir() and not p.is_symlink() and marker.is_file() and not marker.is_symlink()
  assert json.loads(marker.read_text())=={'purpose':'CRA-M8-03-stage','source_host_runtime_excluded':True}
 else:
  p.mkdir(mode=0o700)
  marker.write_text(json.dumps({'purpose':'CRA-M8-03-stage','source_host_runtime_excluded':True}))

runtime=Path('/home/crauser/cra-m0-02/.runtime')
assert not list((runtime/'vms').glob('*')), 'Existing VM resources require explicit recovery'
for marker in runtime.glob('*.pid'):
 try:os.kill(int(marker.read_text()),0)
 except ProcessLookupError:continue
 raise RuntimeError('Do not replace tools of running project daemons')
''')
    transport=shlex.join(SSH)
    source_names=['infra','node','content','backend/cra','backend/pyproject.toml','backend/uv.lock','scripts/smoke_node2_runtime.py']
    runtime_names=['.local/toolchain/root','.local/libvirt','.local/images','.local/lab-tools','.local/course-tools','.local/desktop-tools','.local/browser']
    # --relative keeps fixed paths; no --delete and no private/VM source is selected.
    for root,destination,names in [(ROOT,REMOTE_SOURCE,source_names),(SOURCE_RUNTIME,REMOTE_RUNTIME,runtime_names)]:
        for name in names:
            assert (root/name).exists() and not (root/name).is_symlink()
        args=['rsync','-a','--checksum','--relative','--exclude=__pycache__/','--exclude=*.pyc','--exclude=/.local/libvirt/run/***','-e',transport,*[str(root)+'/./'+name for name in names],TARGET+':'+destination+'/']
        subprocess.run(args,check=True)
        print('Staged owned source' if root==ROOT else 'Staged immutable tools/assets',flush=True)
    # Verify transferred file contents independently; symlinks are transferred as links.
    expected={}
    for root,prefix,names in [(ROOT,REMOTE_SOURCE,source_names),(SOURCE_RUNTIME,REMOTE_RUNTIME,runtime_names)]:
        for name in names:
            item=root/name
            for path in item.rglob('*') if item.is_dir() else [item]:
                rel=path.relative_to(root)
                if '__pycache__' in rel.parts or path.suffix=='.pyc' or str(rel).startswith('.local/libvirt/run/'):continue
                if path.is_file() and not path.is_symlink():
                    with path.open('rb') as stream:expected[prefix+'/'+str(rel)]=hashlib.file_digest(stream,'sha256').hexdigest()
    code='''import json,hashlib,sys
from pathlib import Path
expected=json.load(sys.stdin)
for name,wanted in expected.items():
 assert name.startswith(('/home/crauser/cra-node2/','/home/crauser/cra-m0-02/.local/'))
 path=Path(name);assert path.is_file() and not path.is_symlink()
 with path.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==wanted
print('Verified transferred files:',len(expected))
'''
    result=subprocess.run([*SSH,TARGET,'python3 -c '+shlex.quote(code)],input=json.dumps(expected),text=True,capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError('Transferred runtime checksum mismatch')
    print(result.stdout,end='',flush=True)
    out=ROOT/'reports/local/m8-03';out.mkdir(parents=True,exist_ok=True)
    (out/'staged-assets.json').write_text(json.dumps({'status':'PASS','scope':'source/immutable files only; not VM or multi-node acceptance','files':len(expected),'sha256':expected},indent=2)+'\n')


if __name__=='__main__':main()
