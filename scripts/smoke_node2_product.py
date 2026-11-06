"""M8-03 actual node2 GUI/terminal/lifecycle, scoped synthetic team only."""
import json
from pathlib import Path
import re
import sys
import time
import traceback
from uuid import uuid4
from acceptance_class import provision,deactivate
import node2_acceptance as n2
import bounded_browser
from bounded_browser import run_browser
from smoke_advanced_course import command as terminal_command
from acceptance_client import Client,ORIGIN
from acceptance_terminal import socket as terminal_socket
from smoke_reconciliation import control
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed,InvalidStatus

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload


def check_origins():
    url=ORIGIN.replace('https:','wss:').replace('http:','ws:')+'/desktop/33333333-3333-4333-8333-333333333333'
    for origin in (None,'https://foreign.invalid'):
        try:
            with connect(url,origin=origin,proxy=None,open_timeout=5):raise AssertionError('Invalid origin accepted')
        except InvalidStatus as error:assert error.response.status_code==403
    with connect(url,origin=ORIGIN,proxy=None,open_timeout=5):pass
    print('PASS: actual desktop WS rejects missing/foreign Origin; correct Origin handshake succeeds',flush=True)



def main():
    paths=[];report={'task':'M8-03','status':'FAIL'};out=ROOT/'reports/local/m8-03';out.mkdir(parents=True,exist_ok=True)
    assert not n2.inventory()['defined']
    assert runtime.virsh('list','--name').stdout.split()==['cra-control']
    try:
        n2.config_mode(node2_only=True)
        check_origins()
        path,bundle=provision(on_owned=paths.append);bundle['url']='http://127.0.0.1:8000' if ORIGIN=='http://127.0.0.1:28000' else ORIGIN
        teacher=Client(bundle['users']['teacher']);student=Client(bundle['users']['student'])
        team=teacher.request('/api/v1/teams',{'request_key':str(uuid4()),'name':'Grafisk syntetisk hold','members':[{'user_id':bundle['users'][role]['user_id'],'role':'operator'} for role in ('teacher','student')]},expected=201)
        began=time.monotonic();lab=teacher.wait(teacher.request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1,'team_id':team['id'],'desktop_enabled':True},expected=202),'READY',timeout=900)
        report['create_seconds']=round(time.monotonic()-began,2)
        assert n2.binding(lab['id'])==2
        assert lab['desktop_enabled'];prefix='cra-'+lab['id'].replace('-','')[:10]+'-1'
        bundle.update(lab_id=lab['id'],operators=[prefix+'o',prefix+'p'],target_ip=student.request('/api/v1/labs/'+lab['id'])['target_ip']+':8080')
        for name in bundle['operators']:
            inspection=n2.inspect_vm(lab['id'],name);assert inspection['kvm']=={'enabled':True,'present':True};manifest=inspection['manifest'];assert manifest['memory_mib']==2048 and manifest['desktop_profile']=='gui-v1'
            result=n2.guest(lab['id'],name,"ss -H -ltn | awk '$4 ~ /5900|6000/ {print $4}'");assert '127.0.0.1:5900' in result.splitlines() and set(result.splitlines())<={'127.0.0.1:5900','[::1]:5900'}
        upload('cra-control','/home/cra/cra-deploy/browser_teaching.py',(ROOT/'scripts/browser_teaching.py').read_bytes());control('sudo install -m 0644 /home/cra/cra-deploy/browser_teaching.py /opt/cra-browser/teaching.py')
        print('PASS: real GUI team ready with two 2048MiB operators, pinned profiles and loopback-only VNC',flush=True)
        import hashlib
        report['public_browser_attempts']=[]
        for attempt_number in range(1,2):
            assert teacher.request('/api/v1/labs/'+lab['id'])['state']=='READY' and n2.binding(lab['id'])==2
            assert n2.inventory()['memory']['MemAvailable']>=4096+2*2048+768  # Conservative full GUI growth budget.
            available=next(int(line.split()[1])//1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
            assert available>=5120
            bundle['proof_id']=uuid4().hex[:12]
            n2.guest(lab['id'],prefix+'t','sudo tee /srv/cra-target/gui-proof-'+bundle['proof_id']+'.html >/dev/null',input='<!doctype html><title>LAB_BROWSER_WORKS-'+bundle['proof_id']+'</title><style>body{background:#14252b;color:#a2ebcf;font:24px sans-serif;padding:60px}</style><h1>Virkelig webservice i dit lab</h1><p id="proof"></p><script>window.guiTargetProbe=true;document.getElementById("proof").textContent="JavaScript koerer kun inde i labbrowseren."</script>')
            evidence=out/('gui-browser-attempt-'+str(attempt_number)+'.log');assert not evidence.exists()
            attempt={'attempt':attempt_number,'proof_id':bundle['proof_id'],'evidence':evidence.name,'source_sha256':hashlib.sha256((ROOT/'scripts/browser_desktop.py').read_bytes()).hexdigest(),'status':'FAIL'}
            report['public_browser_attempts'].append(attempt)
            evidence.with_suffix('.json').write_text(json.dumps(attempt,indent=2)+'\n')
            run_browser('browser_desktop.py',bundle,timeout=360,evidence_path=evidence)
            attempt['status']='PASS';evidence.with_suffix('.json').write_text(json.dumps(attempt,indent=2)+'\n')
        result=n2.guest(lab['id'],bundle['operators'][1],'capinfos -c /home/cra/gui-proof-'+bundle['proof_id']+'.pcapng');count=re.search(r'Number of packets:\s+(\d+)',result);assert count and int(count[1])>0;report['captured_packets']=int(count[1])
        # Real relay rejects channel/purpose confusion in both directions.
        for route,purpose in [('terminal','desktop'),('desktop','terminal')]:
            token=student.request('/api/v1/labs/'+lab['id']+'/'+purpose+'-grants',{},expected=201)['grant']
            with connect(ORIGIN.replace('https:','wss:').replace('http:','ws:')+'/'+route+'/'+lab['id'],origin=ORIGIN,additional_headers={'Cookie':student.cookie()},proxy=None,max_size=65536,open_timeout=10) as ws:
                ws.send(json.dumps({'type':'auth','grant':token,'csrf':student.csrf,'cols':100,'rows':30}))
                try:ws.recv(timeout=12)
                except ConnectionClosed:pass
                else:raise AssertionError('Purpose confusion accepted')
        print('PASS: actual relay rejects terminal↔desktop purpose confusion',flush=True)
        run_browser('browser_rfb_limits.py',bundle,timeout=240)
        n2.outage_recovery(teacher,lab)
        report['actual_node2_outage_recovery']='PASS'
        teacher.wait(teacher.action(lab,'stop'),'STOPPED');teacher.wait(teacher.action(lab,'start'),'READY')
        run_browser('browser_desktop.py',{**bundle,'resume':True},timeout=150)
        # A GUI reset must replace every disk/profile and reject the retired target's flag.
        result=terminal_command(student,lab,'curl --fail -s http://'+student.request('/api/v1/labs/'+lab['id'])['target_ip']+':8080/','NODE2_FLAG_READ_DONE')
        old_flag=re.search(r'CRA\{[^}]+\}',result)
        assert old_flag
        accepted=student.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':lab['generation'],'flag':old_flag[0]});assert accepted['accepted'] and accepted['points_awarded']==100 and accepted['total_points']==100
        assert n2.binding(lab['id'])==2
        began=time.monotonic();lab=teacher.wait(teacher.action(lab,'reset'),'READY',timeout=900)
        report['reset_seconds']=round(time.monotonic()-began,2);assert lab['generation']==2 and n2.binding(lab['id'])==2
        rejected=student.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':2,'flag':old_flag[0]})
        assert not rejected['accepted']
        bundle['operators']=[prefix[:-1]+'2o',prefix[:-1]+'2p']
        run_browser('browser_desktop.py',{**bundle,'reset':True},timeout=150)
        result=terminal_command(student,lab,'curl --fail -s http://'+student.request('/api/v1/labs/'+lab['id'])['target_ip']+':8080/','NODE2_NEW_FLAG_DONE')
        new_flag=re.search(r'CRA\{[^}]+\}',result);assert new_flag and new_flag[0]!=old_flag[0]
        accepted=student.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':2,'flag':new_flag[0]});assert accepted['accepted'] and accepted['points_awarded']==0 and accepted['total_points']==100
        report['actual_terminal_flags_before_and_after_reset']='PASS'
        report['status']='PASS'
    except Exception:
        if paths and 'bundle' in locals() and 'operators' in bundle:
            try:
                diagnostics=n2.guest(bundle['lab_id'],bundle['operators'][1],"python3 -c \"import json;from pathlib import Path;p=Path('/home/cra/gui-proof-"+bundle.get('proof_id','absent')+".pcapng');l=Path('/home/cra/gui-capture-"+bundle.get('proof_id','absent')+".log');print(json.dumps({'capture_exists':p.exists(),'capture_size':p.stat().st_size if p.exists() else 0,'capture_log':l.read_text()[-2000:] if l.exists() else 'absent'}))\"")
                report['capture_diagnostics']=json.loads(diagnostics)
            except Exception:report['capture_diagnostics']='unavailable'
        raise
    finally:
        errors=[]
        try:bounded_browser.stop_all()
        except Exception:errors.append('owned_browser_cleanup_unconfirmed')
        for path in paths if not errors else []:
            b=json.loads(path.read_text());scope_errors=[];seen=set()
            for role in ('teacher','student'):
                try:client=Client(b['users'][role]);labs=client.request('/api/v1/labs')
                except Exception:scope_errors.append('scoped_'+role+'_list');continue
                for lab in labs:
                    if lab['id'] in seen or not lab['can_manage']:continue
                    seen.add(lab['id'])
                    try:
                        deadline=time.monotonic()+900
                        while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                            if time.monotonic()>deadline:raise TimeoutError('Cleanup lifecycle')
                            time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                        if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED',timeout=300)
                        assert n2.absent(lab['id'])
                    except Exception:scope_errors.append('scoped_'+role+'_lab')
            if not scope_errors:
                try:deactivate(path)
                except Exception:scope_errors.append('scoped_deactivation')
            errors.extend(scope_errors)
        try:
            if (runtime.STATE/'node2-acceptance-registry.json').exists():n2.config_mode(node2_only=False)
            assert not n2.inventory()['defined']
            assert runtime.virsh('list','--name').stdout.split()==['cra-control']
            result=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp env PYTHONPATH=. .venv/bin/python /opt/cra-completion-test/preservation.py after');assert result.returncode==0
        except Exception:errors.append('registry_or_original_preservation')
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        (out/'node2-product-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Desktop cleanup incomplete')
    print('M8-03 node2 product '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL: '+type(e).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
