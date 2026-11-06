"""Pinned DNS and capture tools, resolved in control and installed offline in labs."""
import base64
import hashlib
import json
import shlex
import tarfile
import tempfile
from pathlib import Path

from runtime import ROOT, NODE, ssh
from lab_tools import upload
from desktop_tools import copy_file

PACKAGES = ['dnsmasq-base', 'bind9-dnsutils', 'tcpdump']
LOCK = ROOT / 'infra/course-packages.lock.json'
CACHE = NODE / '.local/course-tools'


def resolve(base_guest):
    status = ssh(base_guest, 'cat /var/lib/dpkg/status')
    if status.returncode:
        raise RuntimeError('Base guest package status unavailable')
    upload('cra-control', '/home/cra/cra-course-resolve/status', status.stdout.encode())
    code = '''import subprocess,re,json,sys
requested=json.load(sys.stdin)
s=subprocess.run(['apt-get','--simulate','--no-install-recommends','-o','Dir::State::status=/home/cra/cra-course-resolve/status','install',*requested],capture_output=True,text=True,check=True).stdout
if any(line.startswith('Remv ') for line in s.splitlines()):raise RuntimeError('Unexpected removal')
packages=[]
for name,version in re.findall(r'^Inst (\\S+)(?: \\[[^\\]]+\\])? \\((\\S+)',s,re.M):
 m=subprocess.run(['apt-cache','show',name+'='+version],capture_output=True,text=True,check=True).stdout
 packages.append({'name':name,'version':version,'sha256':re.search(r'^SHA256: (\\w+)',m,re.M).group(1)})
print(json.dumps({'format':1,'guest':'ubuntu-24.04-amd64','profile':'course-v1','packages':packages}))
'''
    result = ssh('cra-control', 'python3 -c '+shlex.quote(code), input=json.dumps(PACKAGES))
    if result.returncode:
        raise RuntimeError('Course tool resolution failed')
    lock = json.loads(result.stdout)
    installed = {}
    for paragraph in status.stdout.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
        if fields.get('Status') == 'install ok installed':
            installed[fields['Package']] = fields['Version']
    missing = set(PACKAGES) - {p['name'].split(':')[0] for p in lock['packages']}
    if not missing <= installed.keys():
        raise RuntimeError('Requested tool absent from plan')
    lock['base_packages'] = [{'name': name, 'version': installed[name]} for name in sorted(missing)]
    LOCK.write_text(json.dumps(lock, indent=2)+'\n')
    print('Resolved course tools:', len(lock['packages']))


def fetch():
    CACHE.mkdir(parents=True, exist_ok=True)
    for pkg in json.loads(LOCK.read_text())['packages']:
        dest = CACHE / (pkg['sha256']+'.deb')
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == pkg['sha256']:
            continue
        code = '''import subprocess,pathlib,hashlib,base64,sys,tempfile
with tempfile.TemporaryDirectory(prefix='cra-course-download-') as folder:
 subprocess.run(['apt-get','download',sys.argv[1]],cwd=folder,capture_output=True,check=True)
 files=list(pathlib.Path(folder).glob('*.deb'))
 assert len(files)==1 and hashlib.sha256(files[0].read_bytes()).hexdigest()==sys.argv[2]
 print(base64.b64encode(files[0].read_bytes()).decode())
'''
        result = ssh('cra-control', 'python3 -c '+shlex.quote(code)+' '+shlex.quote(pkg['name']+'='+pkg['version'])+' '+pkg['sha256'], timeout=180)
        if result.returncode:
            raise RuntimeError('Pinned course package download failed: '+pkg['name'])
        data = base64.b64decode(result.stdout)
        if hashlib.sha256(data).hexdigest() != pkg['sha256']:
            raise RuntimeError('Course package checksum mismatch')
        dest.write_bytes(data)
    print('Course tool archives verified')


def install(name):
    lock = json.loads(LOCK.read_text())
    with tempfile.TemporaryDirectory(prefix='cra-course-', dir=CACHE) as folder:
        bundle = Path(folder)/'packages.tar'
        with tarfile.open(bundle, 'w') as archive:
            for pkg in lock['packages']:
                path = CACHE/(pkg['sha256']+'.deb')
                if hashlib.sha256(path.read_bytes()).hexdigest() != pkg['sha256']:
                    raise RuntimeError('Course package checksum mismatch')
                archive.add(path, arcname=path.name)
        copy_file(name, bundle, '/home/cra/course-packages.tar')
    upload(name, '/home/cra/course-lock.json', json.dumps(lock).encode())
    upload(name, '/home/cra/course-install.py', (ROOT/'infra/course/install.py').read_bytes())
    result = ssh(name, 'python3 /home/cra/course-install.py', timeout=180)
    if result.returncode:
        raise RuntimeError('Offline course tool installation failed')
