"""Run inside an owned lab guest; validate exact archive contents before dpkg."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def main():
    home=Path.home()
    lock=json.loads((home/'course-lock.json').read_text())
    for package in lock['base_packages']:
        actual=subprocess.run(['dpkg-query','-W','-f=${Version}',package['name']],capture_output=True,text=True,check=True).stdout
        if actual!=package['version']:raise RuntimeError('Base course dependency mismatch')
    expected={p['sha256']+'.deb':p['sha256'] for p in lock['packages']}
    folder=home/'course-packages';folder.mkdir(exist_ok=True)
    with tarfile.open(home/'course-packages.tar') as archive:
        if len(archive.getmembers())!=len(expected) or set(archive.getnames())!=set(expected) or any(not m.isfile() for m in archive.getmembers()):
            raise ValueError('Unexpected course package archive')
        for name,digest in expected.items():
            data=archive.extractfile(name).read()
            if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Course package digest mismatch')
            (folder/name).write_bytes(data)
    if expected:
        subprocess.run(['sudo','env','DEBIAN_FRONTEND=noninteractive','dpkg','--install',*[str(folder/name) for name in expected]],check=True,capture_output=True,timeout=150)
    audit=subprocess.run(['dpkg','--audit'],check=True,capture_output=True,text=True)
    if audit.stdout.strip():raise RuntimeError('Incomplete course packages')


if __name__=='__main__':main()
