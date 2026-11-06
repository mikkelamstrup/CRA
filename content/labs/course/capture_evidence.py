"""Capture actual guest HTTP traffic; never synthesize packet bytes."""
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import pwd
import re
import select
import signal
import subprocess
import sys
import time
import urllib.request


def stop_capture(proc, timeouts=(5, 2, 2)):
    for sig, timeout in zip((signal.SIGINT, signal.SIGTERM, signal.SIGKILL), timeouts):
        if proc.poll() is not None:
            return
        proc.send_signal(sig)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            continue
    if proc.poll() is None:
        raise RuntimeError('Capture process cleanup unconfirmed')


def main():
    ip=sys.argv[1]
    # Node supplies one fixed lab IP; fail closed before any network request.
    pieces=ip.split('.')
    if len(pieces)!=4 or pieces[:2]!=['192','168'] or pieces[3]!='10' or not 70<=int(pieces[2])<=199:
        raise ValueError('Invalid lab address')
    user=pwd.getpwnam('cra');folder=Path('/home/cra/academy/capture');folder.mkdir(parents=True,exist_ok=True)
    os.chown(folder,user.pw_uid,user.pw_gid)
    path=folder/'incident.pcap'
    if path.exists():path.unlink()  # Owned provisioning artifact only.
    proc=subprocess.Popen(['tcpdump','-i','lab0','-U','-s','0','-Z','cra','-w',str(path),'tcp port 8080'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,env={**os.environ,'LC_ALL':'C'})
    events=[];expected_flag=None
    try:
        deadline=time.monotonic()+5
        while True:
            if time.monotonic()>deadline or proc.poll() is not None:raise RuntimeError('Capture not ready')
            if select.select([proc.stderr],[],[],.2)[0] and b'listening on' in proc.stderr.readline():break
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for route in ['/','/evidence/trace-505.txt','/']:
            started=datetime.now(UTC).isoformat()
            with client.open('http://'+ip+':8080'+route,timeout=5) as response:
                data=response.read();status=response.status
            if route=='/evidence/trace-505.txt':
                match=re.search(rb'CRA\{[0-9a-f]{32}\}',data)
                if match is None:raise RuntimeError('Expected capture evidence unavailable')
                expected_flag=match.group(0)
            events.append({'time_utc':started,'source':'actual HTTP request during lab preparation','path':route,'status':status,'bytes':len(data)})
        time.sleep(1)
    finally:
        try:stop_capture(proc)
        finally:proc.stderr.close()
    if path.stat().st_size<=24:raise RuntimeError('Empty real capture')
    captured=subprocess.run(['tcpdump','-nn','-A','-r',str(path)],check=True,capture_output=True,timeout=5).stdout
    if expected_flag is None or expected_flag not in captured:raise RuntimeError('Required report absent from actual capture')
    os.chown(path,user.pw_uid,user.pw_gid);path.chmod(0o640)
    (folder/'requests.json').write_text(json.dumps(events,indent=2)+'\n')
    (folder/'README.txt').write_text('Egen labcapture af faktisk HTTP under oprettelsen. Tidspunkter er UTC. Data og hændelser er syntetiske undervisningsdata. Læs med tcpdump -nn -A -r incident.pcap eller åbn i labbets Wireshark. requests.json angiver faktiske forespørgsler; den indeholder intet flag. Et packetfund beviser ikke hele hændelsens årsag.\n')


if __name__=='__main__':main()
