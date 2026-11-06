"""Pinned browser acceptance environment inside the owned trusted control VM."""
import hashlib
import json
import shlex
import sys
import urllib.request

from runtime import ROOT,NODE,SECRETS,run
from provision_control import execute
from lab_tools import upload


def packages(resolve=False):
    path=ROOT/'infra/browser-packages.lock.json'
    if resolve:
        code='''
import subprocess,re,json
result=subprocess.run(['/opt/cra/backend/.venv/bin/python','-m','playwright','install-deps','--dry-run','chromium'],capture_output=True,text=True)
requested=re.findall(r'^  ([a-z0-9][a-z0-9+.-]+)$',result.stdout,re.M)
if not requested:raise RuntimeError('Resolve browser packages on clean control image')
s=subprocess.run(['apt-get','--simulate','--no-install-recommends','install',*requested],capture_output=True,text=True,check=True).stdout
packages=[]
for name,version in re.findall(r'^Inst (\\S+)(?: \\[[^\\]]+\\])? \\((\\S+)',s,re.M):
 m=subprocess.run(['apt-cache','show',name+'='+version],capture_output=True,text=True,check=True).stdout
 packages.append({'name':name,'version':version,'sha256':re.search(r'^SHA256: (\\w+)',m,re.M).group(1)})
print(json.dumps({'format':1,'guest':'ubuntu-24.04-amd64','packages':packages}))
'''
        path.write_text(json.dumps(json.loads(execute('python3 -c '+shlex.quote(code))),indent=2)+'\n')
    upload('cra-control','/home/cra/cra-deploy/browser-packages.lock.json',path.read_bytes())
    upload('cra-control','/home/cra/cra-deploy/install_packages.py',(ROOT/'infra/control/install_packages.py').read_bytes())
    execute('python3 /home/cra/cra-deploy/install_packages.py /home/cra/cra-deploy/browser-packages.lock.json',300)
    print('Locked browser libraries installed in control guest',flush=True)


def browser():
    lock=json.loads((ROOT/'infra/browser.lock.json').read_text())
    archive=NODE/'.local/browser'/lock['archive']
    if not archive.exists():
        archive.parent.mkdir(parents=True,exist_ok=True)
        with urllib.request.urlopen(lock['url'],timeout=120) as response:
            data=response.read()
        if hashlib.sha256(data).hexdigest()!=lock['sha256']:raise RuntimeError('Browser checksum mismatch')
        archive.write_bytes(data)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=lock['sha256']:raise RuntimeError('Browser checksum mismatch')
    manifest=json.loads((NODE/'.runtime/vms/cra-control/manifest.json').read_text())
    secret=SECRETS/'cra-control'
    run(['scp','-F','/dev/null','-i',secret/'client','-P',str(manifest['port']),'-o','IdentityAgent=none','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o',f'UserKnownHostsFile={secret/"known_hosts"}',archive,'cra@127.0.0.1:/home/cra/cra-deploy/browser.zip'],timeout=180)
    code='''
import hashlib,zipfile,pathlib,os,shutil
archive=pathlib.Path('/home/cra/cra-deploy/browser.zip')
with archive.open('rb') as stream:
 if hashlib.file_digest(stream,'sha256').hexdigest()!=EXPECTED:raise RuntimeError('Browser checksum mismatch')
root=pathlib.Path('/opt/cra-browser');root.mkdir(mode=0o755,exist_ok=True)
with zipfile.ZipFile(archive) as z:
 if any(pathlib.PurePosixPath(n).is_absolute() or '..' in pathlib.PurePosixPath(n).parts or not n.startswith('chrome-linux64/') for n in z.namelist()):raise RuntimeError('Unexpected archive paths')
 z.extractall(root)
for name in ['chrome','chrome_crashpad_handler','chrome_sandbox']:(root/'chrome-linux64'/name).chmod(0o755)
# Release Chrome expects a hyphenated helper name (the archive uses underscore).
helper=root/'chrome-linux64/chrome-sandbox'
shutil.copyfile(root/'chrome-linux64/chrome_sandbox',helper)
os.chown(helper,0,0);helper.chmod(0o4755)
'''.replace('EXPECTED',repr(lock['sha256']))
    execute('sudo python3 -c '+shlex.quote(code),120)
    execute('id cra-browser >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir /var/lib/cra-browser --shell /usr/sbin/nologin cra-browser')
    print('Pinned Chromium installed with root-owned setuid sandbox; no sandbox checks disabled',flush=True)


if __name__=='__main__':
    packages(resolve='--resolve' in sys.argv);browser()
