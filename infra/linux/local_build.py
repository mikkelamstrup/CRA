"""Build helper: downloads/extracts APT packages under this project only."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TC = ROOT / '.local/toolchain/root'

def environment():
    e = os.environ.copy()
    e.update(PATH=f'{TC}/usr/bin:{TC}/usr/sbin:'+e['PATH'],
             LD_LIBRARY_PATH=f'{TC}/usr/lib/x86_64-linux-gnu',
             PYTHONPATH=f'{TC}/usr/lib/python3/dist-packages',
             PKG_CONFIG_PATH=f'{TC}/usr/lib/x86_64-linux-gnu/pkgconfig:{TC}/usr/share/pkgconfig',
             PKG_CONFIG_SYSROOT_DIR=str(TC))
    return e

def add_packages(names):
    lockpath=ROOT/'infra/build-toolchain.lock.json'
    lock=json.loads(lockpath.read_text())
    sim=subprocess.run(['apt-get','--simulate','--no-install-recommends','install',*names],capture_output=True,text=True,check=True)
    specs={m.group(1):m.group(2) for m in re.finditer(r'^Inst (\S+)(?: \[[^\]]+\])? \((\S+)',sim.stdout,re.M)}
    for name in names:
        if name not in specs:
            policy=subprocess.run(['apt-cache','policy',name],capture_output=True,text=True,check=True).stdout
            specs[name]=re.search(r'Candidate: (\S+)',policy).group(1)
    debs=ROOT/'.local/toolchain/debs';debs.mkdir(parents=True,exist_ok=True)
    for name,version in specs.items():
        meta=subprocess.run(['apt-cache','show',f'{name}={version}'],capture_output=True,text=True,check=True).stdout
        sha=re.search(r'^SHA256: (\w+)',meta,re.M).group(1)
        subprocess.run(['apt-get','download',f'{name}={version}'],cwd=debs,capture_output=True,check=True)
        matches=[p for p in debs.glob('*.deb') if hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==sha]
        if len(matches)!=1: raise RuntimeError('Archive digest mismatch')
        subprocess.run(['dpkg-deb','--extract',matches[0],TC],check=True,capture_output=True)
        lock['packages']=[x for x in lock['packages'] if x['name']!=name]+[{'name':name,'version':version,'sha256':sha}]
    lockpath.write_text(json.dumps(lock,indent=2)+'\n')
    print(f'Extracted {len(specs)} locked project-local build packages')

if __name__=='__main__':
    if sys.argv[1]=='add': add_packages(sys.argv[2:])
    else: raise SystemExit(subprocess.run(sys.argv[1:],env=environment(),cwd=ROOT).returncode)
