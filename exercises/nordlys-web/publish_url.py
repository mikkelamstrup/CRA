"""Publish the tunnel's public address to CRA's static link configuration."""
import json,os,re,shlex,sys,tempfile
from pathlib import Path
os.umask(0o077)
url=sys.stdin.read().strip()
if not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com/?',url):raise ValueError('Unexpected tunnel URL')
body=json.dumps({'url':url.rstrip('/')+'/'})+'\n'
for folder in ('/home/crauser/nordlys-web','/home/crauser/cyber-range-academy-plan/frontend/public','/home/crauser/cra-nordlys-site/frontend/public'):
 p=Path(folder);p.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(dir=p,prefix='.nordlys-')
 with os.fdopen(fd,'w') as f:f.write(body);f.flush();os.fsync(f.fileno())
 os.chmod(tmp,0o644);os.replace(tmp,p/'nordlys.json')
os.environ['CRA_NODE_HOME']='/home/crauser/cra-m0-02';sys.path.insert(0,'/home/crauser/cra-capacity/infra/linux')
import runtime
code="import os,sys,tempfile,json; from pathlib import Path; x=json.load(sys.stdin); p=Path('/opt/cra/frontend/dist/nordlys.json'); fd,tmp=tempfile.mkstemp(dir=p.parent,prefix='.nordlys-'); f=os.fdopen(fd,'w'); json.dump(x,f); f.flush(); os.fsync(f.fileno()); f.close(); os.chmod(tmp,0o644); os.replace(tmp,p)"
r=runtime.ssh('cra-control','sudo python3 -c '+shlex.quote(code),input=body,timeout=20)
if r.returncode:raise RuntimeError('CRA public link update pending')
print('CRA Nordlys link updated',flush=True)
