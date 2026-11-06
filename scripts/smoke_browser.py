#!/usr/bin/env python3
"""Real UI acceptance in a sandboxed, pinned control-VM browser."""
import base64
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
from runtime import NODE,ssh
from lab_tools import upload


def main():
    scenario='browser_scoring.py' if '--scoring' in sys.argv else 'browser_terminal.py' if '--terminal' in sys.argv else 'browser_lifecycle.py'
    script='/home/cra/cra-deploy/'+scenario
    upload('cra-control',script,(ROOT/'scripts'/scenario).read_bytes())
    installed=ssh('cra-control','sudo install -m 0644 '+script+' /opt/cra-browser/acceptance.py')
    if installed.returncode:raise RuntimeError('Could not install browser scenario')
    script='/opt/cra-browser/acceptance.py'
    credentials=NODE/'.secrets/solo-login.json'
    if '--scoring' in sys.argv:
        from acceptance_identity import provision
        credentials=provision('score',resume='--resume' in sys.argv)
    if credentials.stat().st_mode & 0o077:raise RuntimeError('Credential file is not private')
    result=ssh('cra-control','sudo -H -u cra-browser /opt/cra/backend/.venv/bin/python '+script+(' --resume' if '--resume' in sys.argv else ''),
               input=credentials.read_text(),timeout=600)
    print(result.stdout,end='')
    folder=ROOT/('reports/local/m2-03' if '--scoring' in sys.argv else 'reports/local/m2-02' if '--terminal' in sys.argv else 'reports/local/m2-01');folder.mkdir(parents=True,exist_ok=True)
    names=['scoring-desktop.png','scoring-mobile.png'] if '--scoring' in sys.argv else ['terminal-desktop.png','terminal-mobile.png'] if '--terminal' in sys.argv else ['dashboard.png','lab-desktop.png','lab-mobile.png']
    for name in names:
        image=ssh('cra-control','sudo -u cra-browser base64 -w0 /var/lib/cra-browser/acceptance/'+name)
        if image.returncode==0:(folder/name).write_bytes(base64.b64decode(image.stdout))
    if result.returncode:raise RuntimeError('Browser acceptance failed; raw stderr intentionally withheld')
    if '--scoring' in sys.argv:
        from acceptance_identity import deactivate
        deactivate(credentials)
        print('PASS: synthetic acceptance account deactivated; solo account unchanged')


if __name__=='__main__':main()
