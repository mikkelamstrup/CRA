"""M6-02 actual KVM capture/fault/isolation spike, no product telemetry fixture."""
from collections import deque
import json
from pathlib import Path
import shlex
import socket
import statistics
import sys
import threading
import time
import traceback
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from flow_proxy import launch
from lab_tools import install,upload
from smoke_m0_03 import command,guest_probe
from smoke_team_network import SERVER,SERVICE


class Capture:
    def __init__(self,link,operator):
        self.operator=operator;self.epoch=str(uuid4());self.events=deque(maxlen=2048)
        self.process=launch(link['port'],link['port']+100)
        self.stop_reader=False
        def read():
            while True:
                line=self.process.stdout.readline(4097)
                if not line:break
                if len(line)>4096 or not line.endswith(b'\n'):raise ValueError('Metadata limit')
                self.events.append(json.loads(line))
                if self.stop_reader:break
        self.thread=threading.Thread(target=read,daemon=True);self.thread.start()
    def close(self):
        if self.process.poll() is None:self.process.terminate()
        self.process.wait(timeout=5);self.thread.join(3);self.process.stdout.close()
    def ready(self):
        statuses=[e for e in self.events if e['kind']=='status']
        return self.process.poll() is None and bool(statuses) and statuses[-1]['state']=='connected' and time.time()-statuses[-1]['observed_at']<3
    def reader_crash(self):
        self.stop_reader=True;self.thread.join(3);assert not self.thread.is_alive()
        self.process.stdout.close();self.process.wait(timeout=4)
    def flow(self,dport,src=None):
        return any(e.get('kind')=='flow' and e['direction']=='operator_to_target' and e['destination_port']==dport and (src is None or e['src']==src) for e in self.events)


def eventually(predicate,timeout=8):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.1)
    raise TimeoutError('Expected spike condition')


def main():
    out=ROOT/'reports/local/m6-02';out.mkdir(parents=True,exist_ok=True)
    report={'task':'M6-02','scenario':'real-flow-spike','status':'FAIL','checks':[],'started_at':time.time()}
    scope={'lab_id':str(uuid4()),'organization_id':str(uuid4()),'user_id':str(uuid4()),'generation':1}
    names=[];teams=[];captures=[];ports=[]
    def check(label,result=True):
        assert result,label
        report['checks'].append(label);print('PASS: '+label,flush=True)
    def capture(link,operator):
        c=Capture(link,operator);captures.append(c);return c
    def curl(op,link):
        return command(op,f"curl --noproxy '*' -fsS --max-time 3 http://192.168.{link['subnet']}.10:8080/")
    def restart(team):
        for op in team['ops']:
            if runtime.virsh('domstate',op).stdout.strip()!='shut off':
                command(op,'sync');runtime.virsh('shutdown',op)
                eventually(lambda:runtime.virsh('domstate',op).stdout.strip()=='shut off',60)
        for c in team['captures']:c.close()
        team['captures']=[capture(link,op) for link,op in zip(team['links'],team['ops'])]
        for op in team['ops']:runtime.start(op)
        for op in team['ops']:runtime.wait_ready(op)
        eventually(lambda:all(c.ready() for c in team['captures']))
    try:
        runtime.start_daemons()
        for index in range(2):
            prefix='cra-flow'+scope['lab_id'].replace('-','')[:6]+str(index)
            links=[{'port':23640+index*10+n,'subnet':140+index*10+n} for n in range(2)]
            target=prefix+'t';ops=[prefix+'a',prefix+'b']
            for n,role,link in [(target,'target',links[0]),(ops[0],'operator',links[0]),(ops[1],'operator',links[1])]:
                port=24640+index*10+len([v for v in names if v.startswith(prefix)])
                topology={**link,'role':role}
                if role=='target':
                    topology['port']+=100;topology['peers']=[{**links[1],'port':links[1]['port']+100}]
                names.append(n);ports.append(port)
                runtime.prepare(n,port,768 if role=='target' else 1024,lab=topology,ownership=scope)
            runtime.start(target)
            # Target QEMU stream listeners exist before guest cloud-init is ready.
            teamcaptures=[capture(link,op) for link,op in zip(links,ops)]
            for op in ops:runtime.start(op)
            for n in [target,*ops]:runtime.wait_ready(n)
            team={'target':target,'ops':ops,'links':links,'captures':teamcaptures};teams.append(team)
            upload(target,'/home/cra/shared.py',SERVER.encode());upload(target,'/home/cra/shared-value',b'synthetic-shared-state')
            upload(target,'/home/cra/shared.service',SERVICE.encode())
            command(target,'sudo install -m 0644 /home/cra/shared.service /etc/systemd/system/cra-shared.service && sudo systemctl daemon-reload && sudo systemctl enable --now cra-shared')
            eventually(lambda:guest_probe(ops[0],f"192.168.{links[0]['subnet']}.10",8080)=='REACHED')
            for op,link in zip(ops,links):
                check('team '+str(index)+': operator reaches shared HTTP',curl(op,link)=='synthetic-shared-state')
                check('team '+str(index)+': IPv4+IPv6 positive SSH paths',guest_probe(op,f"192.168.{link['subnet']}.10",22)=='REACHED' and guest_probe(op,f"fd42:{link['subnet']}::10",22)=='REACHED')
            for c in teamcaptures:eventually(lambda:c.flow(8080) and c.flow(22))
            check('team '+str(index)+': observed HTTP/SSH ports on both actual ingress links')
        check('six real AMD64 KVM guests',all(runtime.kvm_status(n)=={'enabled':True,'present':True} and command(n,'uname -m')=='x86_64' for n in names))
        team=teams[0];op=team['ops'][0];link=team['links'][0];target=team['target'];c=team['captures'][0]
        install(op)
        scan=command(op,f"nmap -n -Pn -sT -p22,8080 192.168.{link['subnet']}.10")
        check('real nmap detects SSH and HTTP','22/tcp' in scan and '8080/tcp' in scan and scan.count('open')==2)
        # Project-specific lesson key generated inside the isolated guest.
        pub=command(op,'ssh-keygen -q -t ed25519 -N "" -f ~/.ssh/flow-client && cat ~/.ssh/flow-client.pub')
        command(target,'printf "%s\\n" '+shlex.quote(pub)+' >> ~/.ssh/authorized_keys')
        hostpub=(runtime.SECRETS/target/'host.pub').read_text().strip()
        upload(op,'/home/cra/.ssh/flow-hosts',(f"192.168.{link['subnet']}.10 "+hostpub+'\n').encode())
        secret_command='printf SYNTHETIC_ENCRYPTED_COMMAND_NOT_TELEMETRY'
        answer=command(op,f"ssh -F /dev/null -i ~/.ssh/flow-client -o IdentityAgent=none -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/cra/.ssh/flow-hosts cra@192.168.{link['subnet']}.10 "+shlex.quote(secret_command))
        check('actual encrypted SSH command executes',answer=='SYNTHETIC_ENCRYPTED_COMMAND_NOT_TELEMETRY')
        time.sleep(1.2)
        check('capture stores numeric headers only, not encrypted command or HTTP payload','SYNTHETIC_ENCRYPTED' not in json.dumps(list(c.events)) and 'synthetic-shared-state' not in json.dumps(list(c.events)))
        code='''import json,time,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
values=[]
for _ in range(50):
 t=time.monotonic()
 with opener.open(URL,timeout=4) as r:assert r.read()==b'synthetic-shared-state'
 values.append((time.monotonic()-t)*1000)
print(json.dumps(values))
'''.replace('URL',repr(f"http://192.168.{link['subnet']}.10:8080/"))
        values=json.loads(command(op,'python3 -c '+shlex.quote(code)))
        report['http_roundtrip_ms']={'samples':len(values),'p50':round(statistics.median(values),3),'p95':round(sorted(values)[47],3),'max':round(max(values),3)}
        def cpu_seconds():
            fields=Path(f'/proc/{c.process.pid}/stat').read_text().split()
            import os
            return (int(fields[13])+int(fields[14]))/os.sysconf('SC_CLK_TCK')
        before_cpu=cpu_seconds();before_time=time.monotonic()
        burst_code=code.replace('range(50)','range(500)')
        burst=json.loads(command(op,'python3 -c '+shlex.quote(burst_code),timeout=120))
        elapsed=time.monotonic()-before_time
        report['burst']={'http_requests':len(burst),'elapsed_seconds':round(elapsed,3),'proxy_cpu_seconds':round(cpu_seconds()-before_cpu,3),'http_p95_ms':round(sorted(burst)[474],3)}
        status=Path(f'/proc/{c.process.pid}/status').read_text()
        report['proxy_rss_kib']=int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:')))
        check('proxy kernel restrictions active',all(s in status for s in ['NoNewPrivs:\t1','Seccomp:\t2','CapEff:\t0000000000000000']))
        check('proxy FD set has no VM disk/key/management files',all('socket:' in str(p.readlink()) or 'pipe:' in str(p.readlink()) or str(p.readlink()) in ('/dev/null','anon_inode:[eventpoll]') for p in Path(f'/proc/{c.process.pid}/fd').iterdir()))
        command(op,f'sudo ip addr add 192.168.{link["subnet"]}.99/24 dev lab0')
        command(op,f"curl --noproxy '*' --interface 192.168.{link['subnet']}.99 -fsS --max-time 3 http://192.168.{link['subnet']}.10:8080/ >/dev/null")
        eventually(lambda:c.flow(8080,f"192.168.{link['subnet']}.99"))
        check('spoofed source remains attributed to same trusted ingress',c.operator==op)
        with socket.socket() as host:
            host.bind(('127.0.0.1',0));host.listen(20)
            check('trusted control positive host probe',guest_probe('cra-control','10.0.2.2',host.getsockname()[1])=='REACHED')
            for i,t in enumerate(teams):
                foreign=teams[1-i]['links'][0]['subnet']
                for n in [t['target'],*t['ops']]:
                    command(n,f'sudo ip route replace default via 10.0.2.2 dev enp0s30 && sudo ip addr replace 192.168.{foreign}.99/24 dev lab0 && sudo ip -6 addr replace fd42:{foreign}::99/64 dev lab0')
                    for port in [host.getsockname()[1],22280,22290,28000]:assert guest_probe(n,'10.0.2.2',port)=='BLOCKED'
                    assert guest_probe(n,f'192.168.{foreign}.10',22)=='BLOCKED'
                    assert guest_probe(n,f'fd42:{foreign}::10',22)=='BLOCKED'
                for operator,l in zip(t['ops'],t['links']):assert curl(operator,l)=='synthetic-shared-state'
                check('team '+str(i)+': root route/spoofing cannot reach host/control/other team; own network still works')
        c.process.kill();c.process.wait(timeout=5)
        check('proxy crash closes data path',guest_probe(op,f"192.168.{link['subnet']}.10",8080)=='BLOCKED')
        replacement=capture(link,op);time.sleep(.5)
        check('proxy-only restart does not pretend to repair QEMU client',guest_probe(op,f"192.168.{link['subnet']}.10",8080)=='BLOCKED' and not replacement.ready())
        replacement.close();restart(team)
        check('coordinated operator+proxy restart restores shared state',curl(op,link)=='synthetic-shared-state')
        runtime.stop(target)
        eventually(lambda:all(c.process.poll() is not None for c in team['captures']))
        check('target crash makes every observed link unavailable',guest_probe(op,f"192.168.{link['subnet']}.10",8080)=='BLOCKED')
        runtime.start(target);runtime.wait_ready(target);restart(team)
        check('target recovery preserves shared state and repairs links',all(curl(o,l)=='synthetic-shared-state' for o,l in zip(team['ops'],team['links'])))
        runtime.stop(op);eventually(lambda:team['captures'][0].process.poll() is not None)
        restart(team);check('operator crash recovery',curl(op,link)=='synthetic-shared-state')
        team['captures'][0].reader_crash()
        check('metadata consumer death closes process and link',guest_probe(op,f"192.168.{link['subnet']}.10",8080)=='BLOCKED')
        restart(team);check('consumer recovery uses fresh proxy epoch and coordinated restart',curl(op,link)=='synthetic-shared-state')
        report['status']='PASS'
    finally:
        errors=[]
        for c in captures:
            try:c.close()
            except Exception:errors.append('owned_proxy_cleanup')
        for name in reversed(names):
            try:
                manifest=runtime.STATE/'vms'/name/'manifest.json'
                if manifest.exists():
                    assert json.loads(manifest.read_text())['ownership']==scope;runtime.destroy(name)
                elif (runtime.STATE/'vms'/name/'pending.json').exists():
                    assert json.loads((runtime.STATE/'vms'/name/'pending.json').read_text())['ownership']==scope;runtime.recover_pending(name)
            except Exception:errors.append('owned_vm_cleanup')
        try:
            remaining=runtime.virsh('list','--all','--name').stdout.splitlines()
            assert all(n not in remaining and not (runtime.STATE/'vms'/n).exists() and not (runtime.SECRETS/n).exists() for n in names)
            for port in ports+[23640,23641,23650,23651,23740,23741,23750,23751]:
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);sock.bind(('127.0.0.1',port))
        except Exception:errors.append('owned_resource_audit')
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        report['finished_at']=time.time();(out/'capture-spike.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Capture spike cleanup failed')
    print('M6-02 capture spike '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
