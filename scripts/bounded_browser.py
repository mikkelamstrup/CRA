"""Owned browser cgroup with a remote deadline and explicit cleanup after SSH failure."""
import json
from pathlib import Path
import re
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload
ACTIVE={}


def control(command):
    result=runtime.ssh('cra-control',command,timeout=30)
    if result.returncode:raise RuntimeError('Owned browser unit control failed')
    return result.stdout.strip()


def stop_all():
    for unit,marker in list(ACTIVE.items()):
        loaded=control('sudo systemctl show '+unit+'.service -p LoadState --value')
        if loaded!='not-found':
            control('sudo systemctl stop '+unit+'.service')
            assert control('sudo systemctl show '+unit+'.service -p MainPID --value')=='0'
        marker.unlink();del ACTIVE[unit]


def run_browser(filename,bundle,timeout=300,*,evidence_path=None):
    assert re.fullmatch(r'browser_[a-z0-9_]+\.py',filename) and 60<=timeout<=900
    target=filename.replace('browser_','')
    upload('cra-control','/home/cra/cra-deploy/'+filename,(ROOT/'scripts'/filename).read_bytes())
    control('sudo install -m 0644 /home/cra/cra-deploy/'+filename+' /opt/cra-browser/'+target)
    unit='cra-acceptance-'+uuid4().hex
    marker=runtime.STATE/(unit+'.json');marker.write_text(json.dumps({'unit':unit,'purpose':'synthetic-node2-browser'}));marker.chmod(0o600);ACTIVE[unit]=marker
    try:
        runner='sudo systemd-run --pipe --wait --unit='+unit+' --property=User=cra-browser --property=RuntimeMaxSec='+str(timeout)+' --property=KillMode=control-group --property=TimeoutStopSec=10 /opt/cra/backend/.venv/bin/python /opt/cra-browser/'+target
        result=runtime.ssh('cra-control',runner,input=json.dumps(bundle),timeout=timeout+20)
        if evidence_path is not None:Path(evidence_path).write_text(result.stdout)
        print(result.stdout,end='',flush=True)
        if result.returncode:raise RuntimeError('Owned browser acceptance failed')
        return result.stdout
    finally:
        stop_all()
