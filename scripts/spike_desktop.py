"""Explicit owned GUI spike scope; no production lab/host service modifications."""
import json
from pathlib import Path
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
import desktop_tools


def setup():
    report=ROOT/'reports/local/m5-03';report.mkdir(parents=True,exist_ok=True)
    path=report/'spike-scope.json'
    if path.exists():raise RuntimeError('Existing GUI spike scope requires explicit resume or cleanup')
    scope={'lab_id':str(uuid4()),'organization_id':str(uuid4()),'user_id':str(uuid4()),'generation':1}
    prefix='cra-gui'+scope['lab_id'].replace('-','')[:6];names=[prefix+'t',prefix+'o']
    path.write_text(json.dumps({'ownership':scope,'names':names},indent=2))
    for index,name in enumerate(names):
        runtime.prepare(name,24780+index,768 if index==0 else 2048,lab={'role':'target' if index==0 else 'operator','port':23780,'subnet':178},ownership=scope)
        runtime.start(name)
    for name in names:runtime.wait_ready(name)
    if '--resolve' in sys.argv:desktop_tools.resolve(names[1])
    desktop_tools.fetch();desktop_tools.install(names[1])
    from lab_tools import upload
    upload(names[1],'/home/cra/rfb-probe.py',(ROOT/'scripts/guest_rfb_probe.py').read_bytes())
    print('GUI spike guests and package cache ready',flush=True)


def verify():
    import base64,time
    from concurrent.futures import ThreadPoolExecutor
    from lab_tools import upload
    report=ROOT/'reports/local/m5-03';scope=json.loads((report/'spike-scope.json').read_text());target,operator=scope['names']
    for name in scope['names']:
        manifest=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text())
        assert manifest['ownership']==scope['ownership'] and runtime.kvm_status(name)=={'enabled':True,'present':True}
    r=runtime.ssh(operator,"ss -H -ltn | awk '$4 ~ /5900|6000/ {print $4}'")
    assert r.returncode==0 and '127.0.0.1:5900' in r.stdout.splitlines() and set(r.stdout.splitlines())<={'127.0.0.1:5900','[::1]:5900'}
    code="""import socket
for host in ['192.168.178.20','fd42:178::20']:
 for port in [5900,6000]:
  try:s=socket.create_connection((host,port),timeout=2)
  except OSError:continue
  s.close();raise RuntimeError('Unexpected GUI network exposure')
for host in ['192.168.178.20','fd42:178::20']:
 with socket.create_connection((host,22),timeout=2):pass
print('PASS: GUI ports denied on both lab address families; SSH positive controls')
"""
    upload(target,'/home/cra/gui-boundary.py',code.encode());r=runtime.ssh(target,'python3 /home/cra/gui-boundary.py');assert r.returncode==0;print(r.stdout,end='',flush=True)
    upload(target,'/home/cra/gui-target/index.html',b'<!doctype html><title>Isolated GUI proof</title><h1>Virkelig webservice i maal-VM</h1><script>document.body.dataset.executed="inside-lab"</script>')
    r=runtime.ssh(target,'sudo systemd-run --unit cra-gui-http --uid cra --working-directory=/home/cra/gui-target /usr/bin/python3 -m http.server 8080 --bind 0.0.0.0');assert r.returncode==0
    with ThreadPoolExecutor(max_workers=1) as pool:
        capture=pool.submit(runtime.ssh,operator,'dumpcap -i lab0 -a duration:5 -w /home/cra/desktop-proof.pcapng -q')
        time.sleep(1)
        r=runtime.ssh(operator,"DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool key ctrl+l && DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool type --clearmodifiers 'http://192.168.178.10:8080/' && DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool key Return");assert r.returncode==0
        assert capture.result().returncode==0
    r=runtime.ssh(operator,"capinfos -c /home/cra/desktop-proof.pcapng");assert r.returncode==0
    import re
    count=re.search(r'Number of packets:\s+(\d+)',r.stdout);assert count and int(count[1])>0
    r=runtime.ssh(operator,'DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority nohup wireshark -r /home/cra/desktop-proof.pcapng >/home/cra/gui-wireshark.log 2>&1 </dev/null &')
    assert r.returncode==0;time.sleep(3)
    r=runtime.ssh(operator,'python3 /home/cra/rfb-probe.py');assert r.returncode==0
    (report/'gui-wireshark.png').write_bytes(base64.b64decode(r.stdout))
    r=runtime.ssh(operator,"ps -eo user,comm | awk '$2 == \"wireshark\" {print $1}'");assert r.stdout.splitlines()==['cra']
    print('PASS: actual browser network capture opened in Wireshark as ordinary guest user ('+count[1]+' packets)',flush=True)
    (report/'spike.json').write_text(json.dumps({'status':'PASS','packets':int(count[1]),'graphical_sandbox':'see gui-sandbox.png; manual visual review required','vnc_bind':'loopback IPv4/IPv6 only','architecture':'x86_64','memory_mib':2048},indent=2))


def cleanup():
    import importlib.util
    from types import SimpleNamespace
    path=ROOT/'reports/local/m5-03/spike-scope.json'
    scope=json.loads(path.read_text())
    spec=importlib.util.spec_from_file_location('cra_spike_cleanup',ROOT/'node/agent.py')
    agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)
    errors=[]
    for name in scope['names']:
        try:agent.cleanup_vm(name,SimpleNamespace(**scope['ownership']))
        except Exception:errors.append(name)
    if errors:raise RuntimeError('Owned GUI spike cleanup requires recovery')
    path.rename(path.with_name('spike-scope.completed.json'))
    print('PASS: exact GUI spike cleanup',flush=True)


if __name__=='__main__':
    if len(sys.argv)<2 or sys.argv[1] not in ('setup','verify','cleanup'):
        raise SystemExit('Use setup [--resolve], verify, or cleanup; only one runtime writer')
    {'setup':setup,'verify':verify,'cleanup':cleanup}[sys.argv[1]]()
