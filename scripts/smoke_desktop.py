"""M5-03 actual GUI team, boundary checks and resume, with owned-scope cleanup."""
import json
from pathlib import Path
import re
import sys
import time
import traceback
from uuid import uuid4
from acceptance_class import provision,deactivate
from acceptance_client import Client,ORIGIN
from acceptance_terminal import socket as terminal_socket
from smoke_reconciliation import control
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed,InvalidStatus

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload


def check_origins():
    url=ORIGIN.replace('http:','ws:')+'/desktop/33333333-3333-4333-8333-333333333333'
    for origin in (None,'https://foreign.invalid'):
        try:
            with connect(url,origin=origin,proxy=None,open_timeout=5):raise AssertionError('Invalid origin accepted')
        except InvalidStatus as error:assert error.response.status_code==403
    with connect(url,origin=ORIGIN,proxy=None,open_timeout=5):pass
    print('PASS: actual desktop WS rejects missing/foreign Origin; correct Origin handshake succeeds',flush=True)


def run_browser(filename,bundle,timeout=300):
    target=filename.replace('browser_','')
    upload('cra-control','/home/cra/cra-deploy/'+filename,(ROOT/'scripts'/filename).read_bytes());control('sudo install -m 0644 /home/cra/cra-deploy/'+filename+' /opt/cra-browser/'+target)
    result=runtime.ssh('cra-control','sudo -u cra-browser /opt/cra/backend/.venv/bin/python /opt/cra-browser/'+target,input=json.dumps(bundle),timeout=timeout)
    print(result.stdout,end='',flush=True)
    if result.returncode:raise RuntimeError('Desktop browser acceptance failed')


def main():
    paths=[];report={'task':'M5-03','status':'FAIL'};out=ROOT/'reports/local/m5-03';out.mkdir(parents=True,exist_ok=True)
    try:
        check_origins()
        path,bundle=provision(on_owned=paths.append);teacher=Client(bundle['users']['teacher']);student=Client(bundle['users']['student'])
        team=teacher.request('/api/v1/teams',{'request_key':str(uuid4()),'name':'Grafisk syntetisk hold','members':[{'user_id':bundle['users'][role]['user_id'],'role':'operator'} for role in ('teacher','student')]},expected=201)
        began=time.monotonic();lab=teacher.wait(teacher.request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1,'team_id':team['id'],'desktop_enabled':True},expected=202),'READY',timeout=900)
        report['create_seconds']=round(time.monotonic()-began,2)
        assert lab['desktop_enabled'];prefix='cra-'+lab['id'].replace('-','')[:10]+'-1'
        bundle.update(lab_id=lab['id'],operators=[prefix+'o',prefix+'p'],target_ip=student.request('/api/v1/labs/'+lab['id'])['target_ip']+':8080')
        for name in bundle['operators']:
            manifest=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text());assert manifest['memory_mib']==2048 and manifest['desktop_profile']=='gui-v1'
            r=runtime.ssh(name,"ss -H -ltn | awk '$4 ~ /5900|6000/ {print $4}'");assert r.returncode==0 and '127.0.0.1:5900' in r.stdout.splitlines() and set(r.stdout.splitlines())<={'127.0.0.1:5900','[::1]:5900'}
        upload(prefix+'t','/home/cra/gui-proof.html',b'<!doctype html><title>LAB_BROWSER_WORKS</title><style>body{background:#14252b;color:#a2ebcf;font:24px sans-serif;padding:60px}</style><h1>Virkelig webservice i dit lab</h1><p id="proof"></p><script>window.guiTargetProbe=true;document.getElementById("proof").textContent="JavaScript koerer kun inde i labbrowseren."</script>')
        r=runtime.ssh(prefix+'t','sudo install -m 0644 /home/cra/gui-proof.html /srv/cra-target/gui-proof.html');assert r.returncode==0
        upload('cra-control','/home/cra/cra-deploy/browser_teaching.py',(ROOT/'scripts/browser_teaching.py').read_bytes());control('sudo install -m 0644 /home/cra/cra-deploy/browser_teaching.py /opt/cra-browser/teaching.py')
        print('PASS: real GUI team ready with two 2048MiB operators, pinned profiles and loopback-only VNC',flush=True)
        run_browser('browser_desktop.py',bundle,timeout=360)
        r=runtime.ssh(bundle['operators'][1],'capinfos -c /home/cra/gui-proof.pcapng');count=re.search(r'Number of packets:\s+(\d+)',r.stdout);assert r.returncode==0 and count and int(count[1])>0;report['captured_packets']=int(count[1])
        # Real relay rejects channel/purpose confusion in both directions.
        for route,purpose in [('terminal','desktop'),('desktop','terminal')]:
            token=student.request('/api/v1/labs/'+lab['id']+'/'+purpose+'-grants',{},expected=201)['grant']
            with connect(ORIGIN.replace('http:','ws:')+'/'+route+'/'+lab['id'],origin=ORIGIN,additional_headers={'Cookie':student.cookie()},proxy=None,max_size=65536,open_timeout=10) as ws:
                ws.send(json.dumps({'type':'auth','grant':token,'csrf':student.csrf,'cols':100,'rows':30}))
                try:ws.recv(timeout=12)
                except ConnectionClosed:pass
                else:raise AssertionError('Purpose confusion accepted')
        print('PASS: actual relay rejects terminal↔desktop purpose confusion',flush=True)
        run_browser('browser_rfb_limits.py',bundle,timeout=240)
        teacher.wait(teacher.action(lab,'stop'),'STOPPED');teacher.wait(teacher.action(lab,'start'),'READY')
        run_browser('browser_desktop.py',{**bundle,'resume':True},timeout=150)
        # A GUI reset must replace every disk/profile and reject the retired target's flag.
        result=runtime.ssh(prefix+'t','cat /srv/cra-target/index.html')
        old_flag=re.search(r'CRA\{[^}]+\}',result.stdout)
        assert result.returncode==0 and old_flag
        began=time.monotonic();lab=teacher.wait(teacher.action(lab,'reset'),'READY',timeout=900)
        report['reset_seconds']=round(time.monotonic()-began,2);assert lab['generation']==2
        rejected=student.request('/api/v1/labs/'+lab['id']+'/objectives/find-service/attempts',{'generation':2,'flag':old_flag[0]})
        assert not rejected['accepted']
        bundle['operators']=[prefix[:-1]+'2o',prefix[:-1]+'2p']
        run_browser('browser_desktop.py',{**bundle,'reset':True},timeout=150)
        report['status']='PASS'
    finally:
        errors=[]
        for path in paths:
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
                        prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                        assert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.splitlines())
                        assert not any(p.name.startswith(prefix) for p in (runtime.STATE/'vms').iterdir())
                        assert not any(p.name.startswith(prefix) for p in runtime.SECRETS.iterdir())
                    except Exception:scope_errors.append('scoped_'+role+'_lab')
            if not scope_errors:
                try:deactivate(path)
                except Exception:scope_errors.append('scoped_deactivation')
            errors.extend(scope_errors)
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        (out/'product-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Desktop cleanup incomplete')
    print('M5-03 '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL: '+type(e).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
