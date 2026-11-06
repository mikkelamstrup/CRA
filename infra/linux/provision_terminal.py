"""Install the private control-VM HTTP/terminal boundary, never host services."""
import json
import shlex
import sys
from runtime import ROOT
from provision_control import execute
from lab_tools import upload


def packages(resolve=False):
    path=ROOT/'infra/terminal-packages.lock.json'
    if resolve:
        code='''
import subprocess,re,json
s=subprocess.run(['apt-get','--simulate','--no-install-recommends','install','nginx'],capture_output=True,text=True,check=True).stdout
packages=[]
for name,version in re.findall(r'^Inst (\\S+)(?: \\[[^\\]]+\\])? \\((\\S+)',s,re.M):
 m=subprocess.run(['apt-cache','show',name+'='+version],capture_output=True,text=True,check=True).stdout
 packages.append({'name':name,'version':version,'sha256':re.search(r'^SHA256: (\\w+)',m,re.M).group(1)})
print(json.dumps({'format':1,'guest':'ubuntu-24.04-amd64','packages':packages}))
'''
        lock=json.loads(execute('python3 -c '+shlex.quote(code)))
        if not lock['packages']:raise RuntimeError('Resolve requires clean edge package state')
        path.write_text(json.dumps(lock,indent=2)+'\n')
    upload('cra-control','/home/cra/cra-deploy/terminal-packages.lock.json',path.read_bytes())
    upload('cra-control','/home/cra/cra-deploy/install_packages.py',(ROOT/'infra/control/install_packages.py').read_bytes())
    execute('sudo systemctl mask nginx.service')
    execute('python3 /home/cra/cra-deploy/install_packages.py /home/cra/cra-deploy/terminal-packages.lock.json',180)
    print('Locked edge packages installed only in control VM')


def deploy():
    execute('id crarelay >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir /var/lib/cra-relay --shell /usr/sbin/nologin crarelay')
    execute('sudo chmod 0700 /var/lib/cra /var/lib/cra-relay')
    for file in ['cra-relay.service','cra-edge.service','cra-nginx.conf']:
        upload('cra-control','/home/cra/cra-deploy/'+file,(ROOT/'infra/control'/file).read_bytes())
        target='/etc/nginx/'+file if file.endswith('.conf') else '/etc/systemd/system/'+file
        # Public hostname configuration is deployment state. Preserve an existing
        # reviewed edge instead of silently resetting it to the local template.
        command='sudo install -m 0644 /home/cra/cra-deploy/'+file+' '+target
        if file=='cra-nginx.conf':command='test -f '+target+' || '+command
        execute(command)
    execute('sudo nginx -t -c /etc/nginx/cra-nginx.conf')
    execute('sudo systemctl daemon-reload && sudo systemctl enable --now cra-relay cra-edge && sudo systemctl restart cra-relay cra-edge')
    print('Separate terminal relay and HTTP edge deployed')


if __name__=='__main__':
    if '--packages' in sys.argv:packages(resolve='--resolve' in sys.argv)
    if '--deploy' in sys.argv:deploy()
