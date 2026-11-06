"""Reproduce project-local toolchain. Never installs host packages/services.

Ubuntu 26.04 amd64 host prerequisites are recorded in the preflight. This
installer fails closed if locked archives have disappeared from configured APT.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request
from local_build import ROOT, TC, environment


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def fetch(url, path, sha=None):
    if path.exists() and sha and digest(path)==sha:
        return
    part=path.with_suffix(path.suffix+'.part')
    with urllib.request.urlopen(url,timeout=60) as src, part.open('wb') as dst:
        while chunk:=src.read(1024*1024): dst.write(chunk)
    if sha and digest(part)!=sha: raise ValueError('Download checksum mismatch')
    part.replace(path)


def main():
    os.umask(0o077)
    if platform.machine()!='x86_64': raise RuntimeError('AMD64 required')
    for pidfile in (ROOT/'.runtime').glob('*.pid'):
        try: os.kill(int(pidfile.read_text()),0)
        except ProcessLookupError: continue
        raise RuntimeError('Stop the project runtime before replacing its toolchain')
    debs=ROOT/'.local/toolchain/debs';debs.mkdir(parents=True,exist_ok=True)
    indexed={digest(p):p for p in debs.glob('*.deb')}
    for filename in ['toolchain.lock.json','build-toolchain.lock.json']:
        lock=json.loads((ROOT/'infra'/filename).read_text())
        for pkg in lock['packages']:
            if pkg['sha256'] not in indexed:
                subprocess.run(['apt-get','download',pkg['name']+'='+pkg['version']],cwd=debs,
                               capture_output=True,check=True)
                indexed={digest(p):p for p in debs.glob('*.deb')}
            archive=indexed.get(pkg['sha256'])
            if not archive: raise ValueError('APT archive digest mismatch')
            subprocess.run(['dpkg-deb','--extract',archive,TC],capture_output=True,check=True)
    src=ROOT/'.local/src';src.mkdir(parents=True,exist_ok=True)
    lock=json.loads((ROOT/'infra/libvirt.lock.json').read_text())
    archive=src/f'libvirt-{lock["version"]}.tar.xz'
    fetch(lock['url'],archive,lock['sha256'])
    source=src/f'libvirt-{lock["version"]}'
    if not source.exists():
        with tarfile.open(archive) as t: t.extractall(src,filter='data')
    build=src/'build-libvirt'; env=environment()
    if not (build/'build.ninja').exists():
        subprocess.run(['meson','setup',build,source,'--prefix='+str(ROOT/'.local/libvirt'),
                        '--libdir=lib64','-Drunstatedir='+str(ROOT/'.local/libvirt/run'),
                        *lock['meson_options']],env=env,check=True)
    subprocess.run(['ninja','-C',build,'-j2'],env=env,check=True)
    subprocess.run(['ninja','-C',build,'install'],env=env,check=True)
    lock=json.loads((ROOT/'infra/image.lock.json').read_text())
    images=ROOT/'.local/images';images.mkdir(parents=True,exist_ok=True)
    fetch(lock['url'],images/lock['filename'],lock['sha256'])
    (images/lock['filename']).chmod(0o444)
    print('Project-local toolchain and locked base ready; no VMs started')


if __name__=='__main__': main()
