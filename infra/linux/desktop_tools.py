"""Pinned offline GUI profile. Package resolution/download happens only in control."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
from runtime import ROOT,NODE,SECRETS,ssh
from lab_tools import upload

PACKAGES=['xvfb','openbox','x11vnc','wireshark','socat','xterm','dbus-x11','xauth','xdotool']


def resolve(base_guest):
    status=ssh(base_guest,'cat /var/lib/dpkg/status')
    if status.returncode:raise RuntimeError('Base package status unavailable')
    upload('cra-control','/home/cra/cra-gui-resolve/status',status.stdout.encode())
    browser=json.loads((ROOT/'infra/browser-packages.lock.json').read_text())
    requested=PACKAGES+[p['name']+'='+p['version'] for p in browser['packages']]
    code='''import subprocess,re,json,sys
requested=json.load(sys.stdin)
s=subprocess.run(['apt-get','--simulate','--no-install-recommends','-o','Dir::State::status=/home/cra/cra-gui-resolve/status','install',*requested],capture_output=True,text=True,check=True).stdout
if any(line.startswith('Remv ') for line in s.splitlines()):raise RuntimeError('Unexpected package removal')
packages=[]
for name,version in re.findall(r'^Inst (\\S+)(?: \\[[^\\]]+\\])? \\((\\S+)',s,re.M):
 m=subprocess.run(['apt-cache','show',name+'='+version],capture_output=True,text=True,check=True).stdout
 packages.append({'name':name,'version':version,'sha256':re.search(r'^SHA256: (\\w+)',m,re.M).group(1)})
print(json.dumps({'format':1,'guest':'ubuntu-24.04-amd64','profile':'gui-v1','packages':packages}))
'''
    r=ssh('cra-control','python3 -c '+shlex.quote(code),input=json.dumps(requested),timeout=120)
    if r.returncode:raise RuntimeError('Desktop dependency resolution failed')
    lock=json.loads(r.stdout)
    installed={}
    for paragraph in status.stdout.split('\n\n'):
        fields=dict(line.split(': ',1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
        if fields.get('Status')=='install ok installed':installed[fields['Package']]=fields['Version']
    missing=set(PACKAGES)-set(p['name'].split(':')[0] for p in lock['packages'])
    if not missing<=installed.keys():raise RuntimeError('GUI packages absent from locked plan')
    lock['base_packages']=[{'name':name,'version':installed[name]} for name in sorted(missing)]
    (ROOT/'infra/desktop-packages.lock.json').write_text(json.dumps(lock,indent=2)+'\n')
    print('Resolved locked GUI package plan: '+str(len(lock['packages'])),flush=True)


def copy_file(name,source,destination):
    manifest=json.loads((NODE/'.runtime/vms'/name/'manifest.json').read_text());secret=SECRETS/name
    command=['scp','-F','/dev/null','-i',str(secret/'client'),'-P',str(manifest['port']),'-o','IdentityAgent=none','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(secret/'known_hosts'),str(source),'cra@127.0.0.1:'+destination]
    result=subprocess.run(command,capture_output=True,timeout=240)
    if result.returncode:raise RuntimeError('Pinned guest transfer failed')


def fetch():
    lock=json.loads((ROOT/'infra/desktop-packages.lock.json').read_text());cache=NODE/'.local/desktop-tools';cache.mkdir(parents=True,exist_ok=True)
    for index,pkg in enumerate(lock['packages']):
        dest=cache/(pkg['sha256']+'.deb')
        if dest.exists() and hashlib.file_digest(dest.open('rb'),'sha256').hexdigest()==pkg['sha256']:continue
        code='''import subprocess,pathlib,hashlib,base64,sys,tempfile
with tempfile.TemporaryDirectory(prefix='cra-gui-download-') as folder:
 subprocess.run(['apt-get','download',sys.argv[1]],cwd=folder,capture_output=True,check=True)
 files=list(pathlib.Path(folder).glob('*.deb'))
 assert len(files)==1 and hashlib.file_digest(files[0].open('rb'),'sha256').hexdigest()==sys.argv[2]
 print(base64.b64encode(files[0].read_bytes()).decode())
'''
        r=ssh('cra-control','python3 -c '+shlex.quote(code)+' '+shlex.quote(pkg['name']+'='+pkg['version'])+' '+pkg['sha256'],timeout=180)
        if r.returncode:raise RuntimeError('Locked desktop package download failed: '+pkg['name'])
        data=base64.b64decode(r.stdout)
        if hashlib.sha256(data).hexdigest()!=pkg['sha256']:raise RuntimeError('Desktop checksum mismatch')
        dest.write_bytes(data)
        if (index+1)%20==0:print('Verified GUI packages '+str(index+1)+'/'+str(len(lock['packages'])),flush=True)
    print('Verified all locked GUI packages',flush=True)


def install(name):
    lock=json.loads((ROOT/'infra/desktop-packages.lock.json').read_text());cache=NODE/'.local/desktop-tools'
    chrome=json.loads((ROOT/'infra/browser.lock.json').read_text());archive=NODE/'.local/browser'/chrome['archive']
    if hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()!=chrome['sha256']:raise RuntimeError('Desktop browser checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='cra-gui-',dir=cache) as folder:
        bundle=Path(folder)/'packages.tar'
        with tarfile.open(bundle,'w') as tar:
            for pkg in lock['packages']:
                path=cache/(pkg['sha256']+'.deb')
                if hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=pkg['sha256']:raise RuntimeError('Desktop package checksum mismatch')
                tar.add(path,arcname=path.name)
        copy_file(name,bundle,'/home/cra/gui-packages.tar')
    copy_file(name,archive,'/home/cra/gui-browser.zip')
    upload(name,'/home/cra/gui-lock.json',json.dumps({'packages':lock['packages'],'base_packages':lock['base_packages'],'chrome':chrome}).encode())
    upload(name,'/home/cra/gui-install.py',(ROOT/'infra/desktop/install.py').read_bytes())
    r=ssh(name,'python3 /home/cra/gui-install.py',timeout=240)
    if r.returncode:raise RuntimeError('Offline desktop installation failed')
    print('Offline GUI installed in owned operator',flush=True)
