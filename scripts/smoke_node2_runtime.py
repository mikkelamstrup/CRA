"""Node 2 actual KVM/isolation/reset gate; four fresh synthetic guests only."""
import json
import os
import sys
import traceback
from pathlib import Path
import shlex
import socket
import threading
import time
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'infra/linux'))
from runtime import ROOT,NODE,STATE,SECRETS,recover_pending,start_daemons,prepare,start,stop,destroy,wait_ready,ssh,virsh,kvm_status
from lab_tools import install

def command(name,cmd,timeout=30):
    r=ssh(name,cmd,timeout=timeout)
    if r.returncode:raise RuntimeError(f'{name}: guest command failed (exit {r.returncode})')
    return r.stdout.strip()

def names(tag):return f'cra-n2{tag}-operator',f'cra-n2{tag}-target'

def create(tag):
    op,target=names(tag);offset=0 if tag=='a' else 10;subnet=76 if tag=='a' else 77
    for name in [op,target]:
        if (STATE/'vms'/name/'manifest.json').exists():destroy(name)
    prepare(target,22302+offset,768,lab={'port':23001+offset,'role':'target','subnet':subnet})
    prepare(op,22301+offset,1024,lab={'port':23001+offset,'role':'operator','subnet':subnet})
    start(target);start(op)
    wait_ready(target);wait_ready(op)
    unit='[Unit]\nDescription=CRA synthetic isolation endpoint\nAfter=network-online.target\n[Service]\nUser=nobody\nWorkingDirectory=/srv/cra\nExecStart=/usr/bin/python3 -m http.server 8080 --bind ::\nRestart=on-failure\n[Install]\nWantedBy=multi-user.target\n'
    command(target,"sudo mkdir -p /srv/cra && printf %s "+shlex.quote('CRA_'+tag.upper()+'_BASELINE')+" | sudo tee /srv/cra/index.html >/dev/null && printf %s "+shlex.quote(unit)+" | sudo tee /etc/systemd/system/cra-target.service >/dev/null && sudo systemctl daemon-reload && sudo systemctl enable --now cra-target.service")
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        r=ssh(op,f'curl --noproxy "*" --fail --max-time 1 -s http://192.168.{subnet}.10:8080/')
        if r.returncode==0 and r.stdout=='CRA_'+tag.upper()+'_BASELINE':break
        time.sleep(0.5)
    else:raise RuntimeError('Target HTTP did not become ready')
    return op,target

def guest_probe(name,address,port,*,udp=False):
    code='''
import socket,sys
s=socket.socket(socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET,socket.SOCK_DGRAM if sys.argv[3]=='udp' else socket.SOCK_STREAM);s.settimeout(1)
try:
 s.connect((sys.argv[1],int(sys.argv[2])))
 if sys.argv[3]=='udp':s.send(b'CRA_PROBE');s.recv(32)
 print('REACHED')
except OSError:print('BLOCKED')
finally:s.close()
'''
    return command(name,'sudo python3 -c '+shlex.quote(code)+' '+shlex.quote(address)+' '+str(port)+' '+('udp' if udp else 'tcp'))

def main():
    assert str(NODE)=='/home/crauser/cra-m0-02' and str(ROOT)=='/home/crauser/cra-node2'
    assert json.loads((NODE/'.cra-node2-stage.json').read_text())['purpose']=='CRA-M8-03-stage'
    assert 'kvm' in __import__('subprocess').check_output(['id','-nG'],text=True).split()
    assert not any((STATE/'vms'/name).exists() or (SECRETS/name).exists() for tag in ['a','b'] for name in names(tag)), 'Recover registered scope first'
    report={'task':'M8-03-runtime','status':'FAIL','tests':[]};out=ROOT/'reports/local/m8-03';out.mkdir(parents=True,exist_ok=True)
    def check(label,condition):
        report['tests'].append({'test':label,'status':'PASS' if condition else 'FAIL'})
        print(label+': '+('PASS' if condition else 'FAIL'),flush=True)
        if not condition:raise AssertionError(label)
    start_daemons()
    assert not virsh('list','--all','--name').stdout.split(), 'Fresh node runtime required'
    scope=STATE/'node2-runtime-probe.json'
    assert not scope.exists()
    scope.write_text(json.dumps({'purpose':'M8-03-runtime-probe','names':[name for tag in ['a','b'] for name in names(tag)]}))
    try:
        op,target=create('a');other,other_target=create('b')
        check('all four real guests use KVM',all(kvm_status(n)['enabled'] for n in [op,target,other,other_target]))
        report['nmap_version']=install(op)
        scan=command(op,'nmap -n -Pn -sT -p22,8080 192.168.76.10')
        check('real nmap sees target SSH and HTTP','22/tcp' in scan and '8080/tcp' in scan and scan.count('open')==2)
        check('IPv4 target A curl',command(op,'curl --noproxy "*" -fsS http://192.168.76.10:8080/')=='CRA_A_BASELINE')
        check('IPv6 target A curl',command(op,'curl --noproxy "*" -gfsS http://[fd42:76::10]:8080/')=='CRA_A_BASELINE')
        check('positive target B control',command(other,'curl --noproxy "*" -fsS http://192.168.77.10:8080/')=='CRA_B_BASELINE')
        check('positive IPv6 target B control',command(other,'curl --noproxy "*" -gfsS http://[fd42:77::10]:8080/')=='CRA_B_BASELINE')
        pub=command(op,'ssh-keygen -q -t ed25519 -N "" -f ~/.ssh/lab-client && cat ~/.ssh/lab-client.pub')
        command(target,'printf "%s\\n" '+shlex.quote(pub)+' >> ~/.ssh/authorized_keys')
        hostpub=(SECRETS/target/'host.pub').read_text().strip()
        command(op,'printf "%s\\n" '+shlex.quote('192.168.76.10 '+hostpub)+' > ~/.ssh/lab-known-hosts')
        check('real guest to guest pinned SSH',command(op,'ssh -F /dev/null -i ~/.ssh/lab-client -o IdentityAgent=none -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=~/.ssh/lab-known-hosts cra@192.168.76.10 uname -m')=='x86_64')
        # Controlled listeners on host, not arbitrary home/school network scans.
        with socket.socket() as tcp,socket.socket(type=socket.SOCK_DGRAM) as udp:
            tcp.bind(('127.0.0.1',0));tcp.listen(20);udp.bind(('127.0.0.1',0));udp.settimeout(0.2)
            stopping=threading.Event()
            def echo():
                while not stopping.is_set():
                    try:data,peer=udp.recvfrom(64);udp.sendto(data,peer)
                    except socket.timeout:pass
            worker=threading.Thread(target=echo,daemon=True);worker.start()
            try:
                with socket.create_connection(('127.0.0.1',tcp.getsockname()[1]),timeout=3):pass
                with socket.socket(type=socket.SOCK_DGRAM) as positive:
                    positive.settimeout(3);positive.sendto(b'CRA_PROBE',udp.getsockname());assert positive.recv(32)==b'CRA_PROBE'
                with socket.create_connection(('100.64.0.2',22),timeout=3):pass
                with socket.create_connection(('1.1.1.1',443),timeout=4):pass
                check('positive host TCP/UDP, node2 SSH and external TCP controls',True)
                for guest,foreign_subnet,suffix in [(op,77,91),(target,77,92),(other,76,91),(other_target,76,92)]:
                    command(guest,f'sudo ip route replace default via 10.0.2.2 dev enp0s30 && sudo ip addr add 192.168.{foreign_subnet}.{suffix}/24 dev lab0 && sudo ip -6 addr add fd42:{foreign_subnet}::{suffix}/64 dev lab0')
                    routes=json.loads(command(guest,'ip -j route show default'))
                    deadline=time.monotonic()+6
                    while True:
                        addresses=json.loads(command(guest,'ip -j address show dev lab0'))
                        entries=[a for device in addresses for a in device['addr_info'] if a['local']==f'fd42:{foreign_subnet}::{suffix}']
                        if entries and not any(a.get('tentative') or a.get('dadfailed') or {'tentative','dadfailed'}&set(a.get('flags',[])) for a in entries):break
                        if time.monotonic()>deadline:raise RuntimeError('Spoof address did not become usable')
                        time.sleep(.2)
                    check(guest+' adversarial route installed',any(r.get('gateway')=='10.0.2.2' and r.get('dev')=='enp0s30' for r in routes))
                    locals={a['local'] for device in addresses for a in device['addr_info']}
                    check(guest+' spoofed IPv4/IPv6 installed',{f'192.168.{foreign_subnet}.{suffix}',f'fd42:{foreign_subnet}::{suffix}'}<=locals)
                    check(guest+' root cannot reach host TCP',guest_probe(guest,'10.0.2.2',tcp.getsockname()[1])=='BLOCKED')
                    check(guest+' root cannot reach host UDP',guest_probe(guest,'10.0.2.2',udp.getsockname()[1],udp=True)=='BLOCKED')
                    check(guest+' root cannot reach node2 management SSH',guest_probe(guest,'100.64.0.2',22)=='BLOCKED')
                    check(guest+' root cannot reach external TCP',guest_probe(guest,'1.1.1.1',443)=='BLOCKED')
                    check(guest+' spoofed IPv4 cannot reach foreign lab',guest_probe(guest,f'192.168.{foreign_subnet}.10',8080)=='BLOCKED')
                    check(guest+' spoofed IPv6 cannot reach foreign lab',guest_probe(guest,f'fd42:{foreign_subnet}::10',8080)=='BLOCKED')
            finally:stopping.set();worker.join(2)
        check('own lab still works after adversarial guest changes',command(op,'curl --noproxy "*" -fsS http://192.168.76.10:8080/')=='CRA_A_BASELINE')
        check('lab B still works after adversarial guest changes',command(other,'curl --noproxy "*" -fsS http://192.168.77.10:8080/')=='CRA_B_BASELINE')
        for generation in range(1,4):
            old=ET.fromstring(virsh('dumpxml',target).stdout).findtext('uuid')
            command(target,'sudo touch /root/cra-mutation && printf MUTATED | sudo tee /srv/cra/index.html >/dev/null && sudo systemctl stop cra-target')
            command(op,'touch ~/cra-mutation && test -f ~/cra-mutation')
            check(f'reset {generation}: mutations established',command(target,'sudo test -f /root/cra-mutation && test "$(cat /srv/cra/index.html)" = MUTATED && ! systemctl is-active --quiet cra-target && printf MUTATION_CONFIRMED')=='MUTATION_CONFIRMED')
            before=time.monotonic();op,target=create('a')
            check(f'reset {generation}: new VM identity',ET.fromstring(virsh('dumpxml',target).stdout).findtext('uuid')!=old)
            check(f'reset {generation}: target file removed',command(target,'sudo test ! -e /root/cra-mutation && printf CLEAN')=='CLEAN')
            check(f'reset {generation}: operator file removed',command(op,'test ! -e ~/cra-mutation && printf CLEAN')=='CLEAN')
            check(f'reset {generation}: target service and content restored',command(op,'curl --noproxy "*" -fsS http://192.168.76.10:8080/')=='CRA_A_BASELINE')
            report.setdefault('reset_seconds',[]).append(round(time.monotonic()-before,2))
        report['status']='PASS'
    finally:
        for tag in ['a','b']:
            for name in names(tag):
                try:
                    if (STATE/'vms'/name/'manifest.json').exists():destroy(name)
                    elif (STATE/'vms'/name/'pending.json').exists():recover_pending(name)
                except Exception:
                    report['status']='FAIL';report['tests'].append({'test':name+' cleanup','status':'FAIL'})
        clean=True
        try:
            remaining=virsh('list','--all','--name').stdout.split()
            clean=not remaining and not any(name in remaining or (STATE/'vms'/name).exists() or (SECRETS/name).exists() for tag in ['a','b'] for name in names(tag))
            for port in [22301,22302,22311,22312,23001,23011]:
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    probe.bind(('127.0.0.1',port));probe.listen(1)
        except Exception:clean=False
        report['tests'].append({'test':'no orphan lab domains, files or listeners','status':'PASS' if clean else 'FAIL'})
        if not clean:report['status']='FAIL'
        if clean:scope.rename(scope.with_suffix('.completed.json'))
        (out/'node2-runtime.json').write_text(json.dumps(report,indent=2)+'\n')
        print('Overall: '+report['status'],flush=True)
    if report['status']!='PASS':raise RuntimeError('Isolation acceptance failed')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL:',type(error).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
