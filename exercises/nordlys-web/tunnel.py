"""Keep the current Quick Tunnel URL and update CRA when the hostname changes."""
import os,re,signal,subprocess,sys,threading,time
from pathlib import Path
os.umask(0o077)
root=Path('/home/crauser/nordlys-web');stop=threading.Event()
process=subprocess.Popen(['/home/crauser/.local/bin/cloudflared','tunnel','--no-autoupdate','--url','http://127.0.0.1:28110','--http-host-header','localhost:28110'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
def finish(*_):
 stop.set()
 if process.poll() is None:process.terminate()
signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
latest=None;changed=threading.Event();guard=threading.Lock()
def publish():
 while not stop.is_set():
  if not changed.wait(1):continue
  with guard:url=latest
  try:
   r=subprocess.run([sys.executable,str(root/'publish_url.py')],input=url,text=True,capture_output=True,timeout=30)
   if r.returncode==0:
    with guard:
     if latest==url:changed.clear()
    print('CRA website link synchronized',flush=True)
    continue
  except (OSError,subprocess.TimeoutExpired):pass
  print('CRA link update pending; retrying',flush=True);stop.wait(10)
threading.Thread(target=publish,daemon=True).start()
seen=None
try:
 for line in process.stdout:
  found=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',line)
  if found and found.group()!=seen:
   seen=found.group();temp=root/'.url.tmp';temp.write_text(seen+'\n');os.replace(temp,root/'url.txt')
   print('Nordlys public URL: '+seen,flush=True)
   with guard:latest=seen;changed.set()
 code=process.wait()
finally:
 stop.set()
 if process.poll() is None:process.terminate();process.wait(timeout=10)
sys.exit(code)
