"""Install the foundation only inside the owned trusted control VM."""
import io
import json
from pathlib import Path
import shlex
import os
import secrets
import tarfile
import sys
from runtime import ROOT,SECRETS,start_daemons,expose_control_web,ssh
from lab_tools import upload

def execute(command,timeout=120):
    result=ssh('cra-control',command,timeout=timeout)
    if result.returncode:
        # Output belongs to our synthetic control VM, but do not spill arbitrary
        # remote output into exceptions (future steps may handle credentials).
        raise RuntimeError(f'Control provisioning step failed (exit {result.returncode})')
    return result.stdout

def packages(resolve=False):
    path=ROOT/'infra/control-packages.lock.json'
    if resolve:
        execute('sudo apt-get update -qq',180)
        code='''
import subprocess,re,json
s=subprocess.run(['apt-get','--simulate','--no-install-recommends','install','postgresql-16','postgresql-client-16','python3.12-venv'],capture_output=True,text=True,check=True).stdout
packages=[]
for name,version in re.findall(r'^Inst (\\S+)(?: \\[[^\\]]+\\])? \\((\\S+)',s,re.M):
 m=subprocess.run(['apt-cache','show',name+'='+version],capture_output=True,text=True,check=True).stdout
 packages.append({'name':name,'version':version,'sha256':re.search(r'^SHA256: (\\w+)',m,re.M).group(1)})
print(json.dumps({'format':1,'guest':'ubuntu-24.04-amd64','packages':packages}))
'''
        lock=json.loads(execute('python3 -c '+shlex.quote(code)))
        if not lock['packages']:raise RuntimeError('Resolve needs a clean control package state')
        path.write_text(json.dumps(lock,indent=2)+'\n')
    upload('cra-control','/home/cra/cra-deploy/control-packages.lock.json',path.read_bytes())
    upload('cra-control','/home/cra/cra-deploy/install_packages.py',(ROOT/'infra/control/install_packages.py').read_bytes())
    execute('python3 /home/cra/cra-deploy/install_packages.py /home/cra/cra-deploy/control-packages.lock.json',300)
    print('Locked control packages installed',flush=True)

def provision_identity():
    credentials=SECRETS/'solo-login.json'
    if not credentials.exists():
        with os.fdopen(os.open(credentials,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
            json.dump({'username':'solo','password':secrets.token_urlsafe(24)},stream)
    if credentials.stat().st_mode & 0o077:raise RuntimeError('Solo credential file must be private')
    # Stream directly to the guest app's stdin: no remote staging file, argv or log.
    result=ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -m cra.bootstrap_identity --stdin',input=credentials.read_text())
    if result.returncode:raise RuntimeError('Identity provisioning failed; no credentials emitted')

def deploy():
    tool=ROOT/'.local/platform-tools/uv'
    if not tool.exists():raise RuntimeError('Bootstrap the pinned uv tool first')
    if not (ROOT/'frontend/dist/index.html').exists():raise RuntimeError('Build the frontend first')
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w:gz') as tar:
        for folder in ['backend','frontend/dist','frontend/src','content']:
            for path in (ROOT/folder).rglob('*'):
                if path.is_file() and not any(x.startswith('.') or x in ('__pycache__','node_modules') for x in path.relative_to(ROOT).parts):
                    tar.add(path,arcname=str(path.relative_to(ROOT)),recursive=False)
    upload('cra-control','/home/cra/cra-deploy/app.tar.gz',archive.getvalue())
    upload('cra-control','/home/cra/cra-deploy/uv',tool.read_bytes())
    upload('cra-control','/home/cra/cra-deploy/cra-api.service',(ROOT/'infra/control/cra-api.service').read_bytes())
    upload('cra-control','/home/cra/cra-deploy/cra-worker.service',(ROOT/'infra/control/cra-worker.service').read_bytes())
    upload('cra-control','/home/cra/cra-deploy/cra-reconciler.service',(ROOT/'infra/control/cra-reconciler.service').read_bytes())
    # Do not run old writers against a new schema (e.g. the required job owner column).
    # Only already installed project services; first installation has none to stop.
    stop_code="import subprocess; from pathlib import Path; names=[n for n in ['cra-api','cra-worker','cra-worker-node2','cra-reconciler','cra-relay'] if Path('/etc/systemd/system/'+n+'.service').is_file()]; subprocess.run(['sudo','systemctl','stop',*names],check=True) if names else None"
    execute('python3 -c '+shlex.quote(stop_code))
    execute('id craapp >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir /opt/cra --shell /usr/sbin/nologin craapp')
    execute('sudo install -d -o craapp -g craapp /opt/cra /var/lib/cra /var/cache/cra && sudo install -d /opt/cra-tools && sudo install -m 0755 /home/cra/cra-deploy/uv /opt/cra-tools/uv && sudo tar -xzf /home/cra/cra-deploy/app.tar.gz -C /opt/cra && sudo chown -R craapp:craapp /opt/cra')
    execute('sudo -u craapp env UV_CACHE_DIR=/var/cache/cra /opt/cra-tools/uv sync --project /opt/cra/backend --locked --python /usr/bin/python3 --no-python-downloads',180)
    execute("sudo -u postgres psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='craapp'\" | grep -q 1 || sudo -u postgres createuser craapp")
    for db in ['cra','cra_test']:
        execute(f"sudo -u postgres psql -tAc \"SELECT 1 FROM pg_database WHERE datname='{db}'\" | grep -q 1 || sudo -u postgres createdb -O craapp {db}")
    print(execute('cd /opt/cra/backend && sudo -u craapp .venv/bin/python -m cra.db').strip(),flush=True)
    provision_identity()
    execute('sudo install -m 0644 /home/cra/cra-deploy/cra-api.service /etc/systemd/system/cra-api.service && sudo systemctl daemon-reload && sudo systemctl enable --now cra-api && sudo systemctl restart cra-api')
    execute('sudo install -m 0644 /home/cra/cra-deploy/cra-worker.service /etc/systemd/system/cra-worker.service && sudo systemctl daemon-reload && sudo systemctl enable --now cra-worker && sudo systemctl restart cra-worker')
    node2=execute('cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote('from cra.compute_nodes import nodes; print(int(2 in nodes()))')).strip()
    if node2=='1':
        upload('cra-control','/home/cra/cra-deploy/cra-worker-node2.service',(ROOT/'infra/control/cra-worker-node2.service').read_bytes())
        execute('sudo install -m 0644 /home/cra/cra-deploy/cra-worker-node2.service /etc/systemd/system/cra-worker-node2.service && sudo systemctl daemon-reload && sudo systemctl enable --now cra-worker-node2 && sudo systemctl restart cra-worker-node2')
    execute('sudo install -m 0644 /home/cra/cra-deploy/cra-reconciler.service /etc/systemd/system/cra-reconciler.service && sudo systemctl daemon-reload && sudo systemctl enable --now cra-reconciler && sudo systemctl restart cra-reconciler')
    from provision_terminal import deploy as deploy_terminal
    deploy_terminal()
    print('Control API deployed',flush=True)

def main():
    start_daemons();expose_control_web()
    check=execute('. /etc/os-release; printf "%s %s" "$VERSION_CODENAME" "$(uname -m)"')
    if check!='noble x86_64':raise RuntimeError('Expected trusted Noble AMD64 control guest')
    if '--packages' in sys.argv:packages(resolve='--resolve' in sys.argv)
    if '--deploy' in sys.argv:deploy()

if __name__=='__main__':main()
