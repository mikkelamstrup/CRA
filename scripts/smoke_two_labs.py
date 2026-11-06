"""M3-01 actual API/WS/KVM isolation. Only synthetic project-owned resources."""
import json
from pathlib import Path
import re
import socket
import sys
import threading
import time
import traceback

from acceptance_client import Client
from acceptance_identity import provision,deactivate
from acceptance_terminal import Terminal,grant,denied

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from smoke_m0_03 import guest_probe,command

def vm(lab,role):return 'cra-'+lab['id'].replace('-','')[:10]+'-'+format(lab['generation'],'x')+role

def main():
    report={'task':'M3-01','status':'FAIL','started_at':time.time(),'checks':[]}
    out=ROOT/'reports/local/m3-01';out.mkdir(parents=True,exist_ok=True)
    def check(label,result=True):
        report['checks'].append({'check':label,'result':'PASS' if result else 'FAIL'})
        assert result,label
        print('PASS: '+label,flush=True)
    paths=[];clients=[];labs=[];terminals=[]
    try:
        for label in ('isolation-a','isolation-b'):
            path=provision(label,resume='--resume' in sys.argv,on_owned=paths.append)
            clients.append(Client(path))
        operations=[]
        for client in clients:
            active=[l for l in client.request('/api/v1/labs') if l['state']!='DESTROYED']
            if active:labs.append(active[0]);operations.append(None)
            else:
                operation=client.create();operations.append(operation)
                labs.append({'id':operation['lab_id'],'generation':1})
        for i,operation in enumerate(operations):
            if operation:labs[i]=clients[i].wait(operation,'READY',timeout=480)
        a,b=clients;la,lb=labs
        check('two actual isolated KVM operator/target pairs',all(runtime.kvm_status(vm(l,r))['enabled'] for l in labs for r in ('o','t')))
        for own,other,lab,foreign in [(a,b,la,lb),(b,a,lb,la)]:
            assert {row['id'] for row in own.request('/api/v1/labs')}=={lab['id']}
            own.request('/api/v1/labs/'+foreign['id'],expected=404)
            index=labs.index(foreign)
            if operations[index]:own.request('/api/v1/operations/'+operations[index]['operation_id'],expected=404)
            own.request('/api/v1/labs/'+foreign['id']+'/progress',expected=404)
            for action in ('reset','destroy','terminal-grants'):
                own.request('/api/v1/labs/'+foreign['id']+'/'+action,{},expected=404)
            own.request('/api/v1/labs/'+foreign['id']+'/objectives/find-service/attempts',{'generation':1,'flag':'synthetic-invalid'},expected=404)
            own.request('/api/v1/organizations/'+own.identity['organization_id']+'/members',expected=403)
            token=grant(other,foreign)
            denied(own,foreign,token)
            denied(other,lab,token)
        check('bidirectional server API/scoring/teacher and actual WS foreign-session/lab denial')
        solo=Client(runtime.NODE/'.secrets/solo-login.json')
        result=solo.request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1},expected=409)
        assert result['detail']=='capacity_unavailable'
        check('two-slot capacity rejects third lab without allocation')
        flags=[]
        for client,lab in zip(clients,labs):
            terminal=Terminal(client,lab);terminals.append(terminal)
            scan=terminal.command('nmap -n -Pn -sT -p22,8080 '+lab['target_ip']+"; printf 'SCAN_%s\\n' DONE",b'SCAN_DONE')
            assert b'22/tcp' in scan and b'8080/tcp' in scan and scan.count(b'open')>=2
            page=terminal.command('curl --fail --max-time 5 http://'+lab['target_ip']+":8080/; printf 'HTTP_%s\\n' DONE",b'HTTP_DONE')
            flags.append(re.search(rb'CRA\{[0-9a-f]{32}\}',page)[0].decode())
        assert flags[0]!=flags[1]
        for i,client in enumerate(clients):
            result=client.request('/api/v1/labs/'+labs[i]['id']+'/objectives/find-service/attempts',{'generation':1,'flag':flags[1-i]})
            assert not result['accepted']
            own=client.request('/api/v1/labs/'+labs[i]['id']+'/objectives/find-service/attempts',{'generation':1,'flag':flags[i]})
            assert own['accepted'] and own['points_awarded']==100
        check('real WS nmap/curl in both labs; distinct flags, foreign flags rejected and own flags accepted')
        for terminal in terminals:terminal.close()
        terminals=[]
        with socket.socket() as tcp,socket.socket(type=socket.SOCK_DGRAM) as udp:
            tcp.bind(('127.0.0.1',0));tcp.listen(20);udp.bind(('127.0.0.1',0));udp.settimeout(.2)
            stop=threading.Event()
            def echo():
                while not stop.is_set():
                    try:data,peer=udp.recvfrom(64);udp.sendto(data,peer)
                    except socket.timeout:pass
            thread=threading.Thread(target=echo,daemon=True);thread.start()
            try:
                assert guest_probe('cra-control','10.0.2.2',tcp.getsockname()[1])=='REACHED'
                assert guest_probe('cra-control','10.0.2.2',udp.getsockname()[1],udp=True)=='REACHED'
                for i,lab in enumerate(labs):
                    subnet=lab['target_ip'].split('.')[2];foreign=labs[1-i]['target_ip'].split('.')[2]
                    assert guest_probe(vm(lab,'o'),lab['target_ip'],22)=='REACHED'
                    assert guest_probe(vm(lab,'o'),'fd42:'+subnet+'::10',22)=='REACHED'
                    for role in ('o','t'):
                        name=vm(lab,role)
                        command(name,'sudo ip route replace default via 10.0.2.2 dev enp0s30 && sudo ip addr replace 192.168.'+foreign+'.99/24 dev lab0 && sudo ip -6 addr replace fd42:'+foreign+'::99/64 dev lab0')
                        addresses=json.loads(command(name,'ip -j addr show dev lab0'))[0]['addr_info']
                        assert any(item['local']=='192.168.'+foreign+'.99' for item in addresses)
                        assert any(item['local']=='fd42:'+foreign+'::99' for item in addresses)
                        routes=json.loads(command(name,'ip -j route show default'))
                        assert any(route.get('gateway')=='10.0.2.2' for route in routes)
                        assert guest_probe(name,'10.0.2.2',tcp.getsockname()[1])=='BLOCKED'
                        assert guest_probe(name,'10.0.2.2',udp.getsockname()[1],udp=True)=='BLOCKED'
                        for port in (22280,22290,28000):assert guest_probe(name,'10.0.2.2',port)=='BLOCKED'
                        assert guest_probe(name,'192.168.'+foreign+'.10',22)=='BLOCKED'
                        assert guest_probe(name,'fd42:'+foreign+'::10',22)=='BLOCKED'
                        check('root '+str(i)+'/'+role+' denied host TCP/UDP, control/node/web and cross-lab IPv4/IPv6 after spoofing')
                    assert guest_probe(vm(lab,'o'),lab['target_ip'],8080)=='REACHED'
                check('positive IPv4/IPv6 controls and permitted HTTP survive adversarial routes')
            finally:stop.set();thread.join(2)
        report['status']='PASS'
    finally:
        cleanup_errors=[]
        for terminal in terminals:
            try:terminal.close()
            except Exception:cleanup_errors.append('terminal_close')
        for i,path in enumerate(paths):
            try:
                if i<len(clients):
                    client=clients[i]
                    # Scope is a dedicated acceptance identity; discover even a create
                    # accepted by the server whose response was lost in transit.
                    owned=client.request('/api/v1/labs')
                    for lab in owned:
                        deadline=time.monotonic()+480
                        while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                            if time.monotonic()>deadline:raise TimeoutError('Cleanup waits for durable job')
                            time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                        if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED')
                        for role in ('o','t'):
                            name=vm(lab,role)
                            assert name not in runtime.virsh('list','--all','--name').stdout.splitlines()
                            assert not (runtime.STATE/'vms'/name).exists() and not (runtime.SECRETS/name).exists()
                deactivate(path)
            except Exception:cleanup_errors.append('identity_'+str(i)+'_cleanup')
        if cleanup_errors:report['status']='FAIL'
        report['checks'].append({'check':'both test labs/resources removed and test users deactivated','result':'FAIL' if cleanup_errors else 'PASS'})
        report['cleanup_errors']=cleanup_errors
        report['finished_at']=time.time();(out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if cleanup_errors:raise RuntimeError('Acceptance cleanup incomplete; scoped retry required')
        print('PASS: both test labs/resources removed and test users deactivated',flush=True)
    print('M3-01 '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True)
        sys.exit(1)
