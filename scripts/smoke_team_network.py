"""Two real teams, two operators each, one actual shared target per team."""
import json
from pathlib import Path
import shlex
import socket
import sys
import threading
import time
import traceback
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload
from smoke_m0_03 import command,guest_probe

SERVICE='''[Unit]
Description=CRA owned shared-target spike
After=network.target
[Service]
User=cra
ExecStart=/usr/bin/python3 /home/cra/shared.py
Restart=on-failure
[Install]
WantedBy=multi-user.target
'''
SERVER='''from http.server import HTTPServer,BaseHTTPRequestHandler
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  data=Path('/home/cra/shared-value').read_bytes();self.send_response(200);self.end_headers();self.wfile.write(data)
 def do_POST(self):
  size=int(self.headers.get('Content-Length','0'))
  if not 0<size<=256:self.send_response(400);self.end_headers();return
  Path('/home/cra/shared-value').write_bytes(self.rfile.read(size));self.send_response(204);self.end_headers()
HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
'''


def main():
    report={'task':'M5-01','scenario':'shared-target-network-spike','status':'FAIL','checks':[],'started_at':time.time()}
    out=ROOT/'reports/local/m5-01';out.mkdir(parents=True,exist_ok=True)
    scope={'lab_id':str(uuid4()),'organization_id':str(uuid4()),'user_id':str(uuid4()),'generation':1}
    names=[];teams=[];ports=[]
    def check(label,result=True):
        assert result,label
        report['checks'].append(label);print('PASS: '+label,flush=True)
    try:
        runtime.start_daemons()
        for index in range(2):
            prefix='cra-net'+scope['lab_id'].replace('-','')[:6]+str(index)
            links=[{'port':23640+index*10+n,'subnet':140+index*10+n} for n in range(2)]
            target=prefix+'t';ops=[prefix+'a',prefix+'b']
            for n,role,link in [(target,'target',links[0]),(ops[0],'operator',links[0]),(ops[1],'operator',links[1])]:
                port=24640+index*10+len([v for v in names if v.startswith(prefix)])
                topology={**link,'role':role}
                if role=='target':topology['peers']=[links[1]]
                names.append(n);ports.append(port)
                runtime.prepare(n,port,768 if role=='target' else 1024,lab=topology,ownership=scope)
                runtime.start(n)
            for n in [target,*ops]:runtime.wait_ready(n)
            teams.append({'target':target,'ops':ops,'links':links})
            upload(target,'/home/cra/shared.py',SERVER.encode());upload(target,'/home/cra/shared-value',b'initial')
            upload(target,'/home/cra/shared.service',SERVICE.encode())
            command(target,'sudo install -m 0644 /home/cra/shared.service /etc/systemd/system/cra-shared.service && sudo systemctl daemon-reload && sudo systemctl enable --now cra-shared')
            deadline=time.monotonic()+10
            while guest_probe(ops[0],f"192.168.{links[0]['subnet']}.10",8080)!='REACHED':
                if time.monotonic()>deadline:raise TimeoutError('Shared service')
                time.sleep(.2)
            marker='synthetic-team-'+str(index)
            command(ops[0],f"curl --noproxy '*' -fsS --max-time 5 -X POST --data {marker} http://192.168.{links[0]['subnet']}.10:8080/")
            read=command(ops[1],f"curl --noproxy '*' -fsS --max-time 5 http://192.168.{links[1]['subnet']}.10:8080/")
            check('team '+str(index)+': both operators reach same target state',read==marker)
            command(ops[0],'touch /home/cra/operator-private && sync && test -f /home/cra/operator-private')
            check('team '+str(index)+': distinct operator disks',command(ops[1],'test ! -e /home/cra/operator-private && printf DISTINCT')=='DISTINCT')
        for i,team in enumerate(teams):
            for operator,link in zip(team['ops'],team['links']):
                assert guest_probe(operator,f"192.168.{link['subnet']}.10",22)=='REACHED'
                assert guest_probe(operator,f"fd42:{link['subnet']}::10",22)=='REACHED'
            check('team '+str(i)+': own SSH endpoint reachable on every operator link over IPv4 and IPv6')
        check('six real AMD64 KVM guests',all(runtime.kvm_status(n)=={'enabled':True,'present':True} and command(n,'uname -m')=='x86_64' for n in names))
        with socket.socket() as tcp,socket.socket(type=socket.SOCK_DGRAM) as udp:
            tcp.bind(('127.0.0.1',0));tcp.listen(20);udp.bind(('127.0.0.1',0));udp.settimeout(.2)
            stop=threading.Event()
            def echo():
                while not stop.is_set():
                    try:data,peer=udp.recvfrom(64);udp.sendto(data,peer)
                    except socket.timeout:pass
            thread=threading.Thread(target=echo,daemon=True);thread.start()
            try:
                check('control positive host TCP/UDP',guest_probe('cra-control','10.0.2.2',tcp.getsockname()[1])=='REACHED' and guest_probe('cra-control','10.0.2.2',udp.getsockname()[1],udp=True)=='REACHED')
                for i,team in enumerate(teams):
                    foreign=teams[1-i]['links'][0]['subnet']
                    for name in [team['target'],*team['ops']]:
                        command(name,f'sudo ip route replace default via 10.0.2.2 dev enp0s30 && sudo ip addr replace 192.168.{foreign}.99/24 dev lab0 && sudo ip -6 addr replace fd42:{foreign}::99/64 dev lab0')
                        addresses=json.loads(command(name,'ip -j addr show dev lab0'))[0]['addr_info']
                        assert any(a['local']==f'192.168.{foreign}.99' for a in addresses)
                        assert any(a['local']==f'fd42:{foreign}::99' for a in addresses)
                        routes=json.loads(command(name,'ip -j route show default'));assert any(r.get('gateway')=='10.0.2.2' for r in routes)
                        for port in [tcp.getsockname()[1],22280,22290,28000]:assert guest_probe(name,'10.0.2.2',port)=='BLOCKED'
                        assert guest_probe(name,'10.0.2.2',udp.getsockname()[1],udp=True)=='BLOCKED'
                        assert guest_probe(name,f'192.168.{foreign}.10',22)=='BLOCKED'
                        assert guest_probe(name,f'fd42:{foreign}::10',22)=='BLOCKED'
                    check('team '+str(i)+': root in all three VMs denied host/control/node/web/other team v4+v6 after verified spoofing')
                    for n,link in zip(team['ops'],team['links']):
                        assert command(n,f"curl --noproxy '*' -fsS --max-time 5 http://192.168.{link['subnet']}.10:8080/")=='synthetic-team-'+str(i)
                    check('team '+str(i)+': own shared-target paths remain functional')
            finally:stop.set();thread.join(2)
        report['status']='PASS'
    finally:
        errors=[]
        for name in reversed(names):
            try:
                manifest=runtime.STATE/'vms'/name/'manifest.json'
                if manifest.exists():
                    assert json.loads(manifest.read_text())['ownership']==scope
                    runtime.destroy(name)
                elif (runtime.STATE/'vms'/name/'pending.json').exists():
                    assert json.loads((runtime.STATE/'vms'/name/'pending.json').read_text())['ownership']==scope
                    runtime.recover_pending(name)
            except Exception:errors.append('owned_vm_cleanup')
        try:
            remaining=runtime.virsh('list','--all','--name').stdout.splitlines()
            assert all(n not in remaining and not (runtime.STATE/'vms'/n).exists() and not (runtime.SECRETS/n).exists() for n in names)
            for port in ports+[23640,23641,23650,23651]:
                with socket.socket() as sock:sock.bind(('127.0.0.1',port))
        except Exception:errors.append('owned_resource_audit')
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        report['finished_at']=time.time();(out/'network-spike.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Network spike cleanup failed')
    print('M5-01 network spike '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
