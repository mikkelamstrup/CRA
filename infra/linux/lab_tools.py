"""Fetch locked Noble packages in the trusted control guest; install offline."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
import sys
from runtime import ROOT,NODE,ssh

def upload(name,path,data):
    code='import base64,sys; from pathlib import Path; p=Path('+repr(path)+'); p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(base64.b64decode(sys.stdin.read()))'
    r=ssh(name,'python3 -c '+shlex.quote(code),input=base64.b64encode(data).decode(),timeout=120)
    if r.returncode:raise RuntimeError('Guest file transfer failed')

def fetch(*,resolve=False):
    lockpath=ROOT/'infra/lab-tools.lock.json'
    if resolve:
        code='''
import subprocess,re,json
s=subprocess.run(['apt-get','--simulate','--no-install-recommends','install','nmap'],capture_output=True,text=True,check=True).stdout
packages=[]
for name,version in re.findall(r'^Inst (\\S+)(?: \\[[^\\]]+\\])? \\((\\S+)',s,re.M):
 m=subprocess.run(['apt-cache','show',name+'='+version],capture_output=True,text=True,check=True).stdout
 packages.append({'name':name,'version':version,'sha256':re.search(r'^SHA256: (\\w+)',m,re.M).group(1)})
print(json.dumps({'format':1,'guest':'ubuntu-24.04-amd64','packages':packages}))
'''
        r=ssh('cra-control','python3 -c '+shlex.quote(code))
        if r.returncode:raise RuntimeError('Failed to resolve guest package metadata')
        lock=json.loads(r.stdout)
        if not lock['packages']:raise RuntimeError('Expected packages missing from clean control guest')
        lockpath.write_text(json.dumps(lock,indent=2)+'\n')
    lock=json.loads(lockpath.read_text())
    cache=NODE/'.local/lab-tools';cache.mkdir(parents=True,exist_ok=True)
    for pkg in lock['packages']:
        dest=cache/(pkg['sha256']+'.deb')
        if dest.exists() and hashlib.file_digest(dest.open('rb'),'sha256').hexdigest()==pkg['sha256']:continue
        code='''
import subprocess,pathlib,hashlib,base64,sys
p=pathlib.Path.home()/'cra-build-tools';p.mkdir(exist_ok=True)
subprocess.run(['apt-get','download',sys.argv[1]],cwd=p,capture_output=True,check=True)
files=[f for f in p.glob('*.deb') if hashlib.file_digest(f.open('rb'),'sha256').hexdigest()==sys.argv[2]]
assert len(files)==1
print(base64.b64encode(files[0].read_bytes()).decode())
'''
        r=ssh('cra-control','python3 -c '+shlex.quote(code)+' '+shlex.quote(pkg['name']+'='+pkg['version'])+' '+pkg['sha256'],timeout=180)
        if r.returncode:raise RuntimeError('Guest package download failed')
        data=base64.b64decode(r.stdout,validate=False)
        if hashlib.sha256(data).hexdigest()!=pkg['sha256']:raise RuntimeError('Guest package checksum mismatch')
        dest.write_bytes(data)
    print(f'Verified {len(lock["packages"])} locked offline lab packages')

def install(name):
    lock=json.loads((ROOT/'infra/lab-tools.lock.json').read_text())
    for pkg in lock['packages']:
        path=NODE/'.local/lab-tools'/(pkg['sha256']+'.deb')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=pkg['sha256']:raise RuntimeError('Offline package checksum mismatch')
        upload(name,'/home/cra/cra-tools/'+pkg['sha256']+'.deb',data)
    r=ssh(name,'sudo dpkg --install /home/cra/cra-tools/*.deb >/dev/null && tmux -V && nmap --version | head -1',timeout=90)
    if r.returncode:raise RuntimeError('Offline nmap installation failed')
    return r.stdout.strip()

if __name__=='__main__':fetch(resolve='--resolve' in sys.argv)
