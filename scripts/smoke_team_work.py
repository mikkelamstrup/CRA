"""M5-02 real VM solo/team/resume and two-browser collaboration acceptance."""
import json
from pathlib import Path
import re
import sys
import time
import traceback
from uuid import uuid4
from acceptance_client import Client
from acceptance_class import provision,deactivate
from acceptance_terminal import Terminal
from smoke_reconciliation import control

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload


def main():
    paths=[];report={'task':'M5-02','status':'FAIL'};out=ROOT/'reports/local/m5-02';out.mkdir(parents=True,exist_ok=True)
    try:
        path,bundle=provision(on_owned=paths.append)
        clients=[Client(bundle['users'][role]) for role in ('teacher','student')]
        solos=[];names=[]
        for client in clients:
            lab=client.wait(client.request('/api/v1/labs',{'mission_id':'first-files','mission_version':1},expected=202),'READY')
            solos.append(lab);name='cra-'+lab['id'].replace('-','')[:10]+'-1o';names.append(name)
            terminal=Terminal(client,lab)
            try:output=terminal.command("touch /home/cra/solo-preserved; cat /home/cra/academy/arkiv/2026/rapport-104.txt; sync; printf 'SOLO_%s\\n' SAVED",b'SOLO_SAVED')
            finally:terminal.close()
            flag=re.search(rb'CRA\{[0-9a-f]{32}\}',output)[0].decode()
            assert client.request('/api/v1/labs/'+lab['id']+'/objectives/find-evidence/attempts',{'generation':1,'flag':flag})['points_awarded']==100
            client.request('/api/v1/course/days/1/notebook',{'revision':0,'body':'Mit bevarede soloarbejde','reflection':'Jeg fandt evidens i filen.','theory_read':True},method='PUT')
        team=clients[0].request('/api/v1/teams',{'request_key':str(uuid4()),'name':'Syntetisk samarbejde','members':[{'user_id':c.identity['user_id'],'role':'operator'} for c in clients]},expected=201)
        bundle['team_id']=team['id']
        assert clients[0].request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1,'team_id':team['id']},expected=409)['detail']=='stop_solo_first'
        print('PASS: two real solo shells/files, personal notebooks and 100 points each; active-solo transition rejected',flush=True)
        for source,target in [('browser_teaching.py','teaching.py'),('browser_team_work.py','team_work.py')]:
            upload('cra-control','/home/cra/cra-deploy/'+source,(ROOT/'scripts'/source).read_bytes());control('sudo install -m 0644 /home/cra/cra-deploy/'+source+' /opt/cra-browser/'+target)
        result=runtime.ssh('cra-control','sudo -u cra-browser /opt/cra/backend/.venv/bin/python /opt/cra-browser/team_work.py',input=json.dumps(bundle),timeout=800)
        print(result.stdout,end='',flush=True)
        if result.returncode:raise RuntimeError('Browser collaboration failed')
        hold=next(l for l in clients[0].request('/api/v1/labs') if l['team_id']==team['id'] and l['state']!='DESTROYED')
        assert hold['state']=='STOPPED'
        all_names=runtime.virsh('list','--all','--name').stdout.splitlines()
        running=runtime.virsh('list','--name').stdout.splitlines()
        prefixes=['cra-'+l['id'].replace('-','')[:10]+'-' for l in [*solos,hold]]
        owned=[n for n in all_names if any(n.startswith(p) for p in prefixes)]
        assert len(owned)==7 and not set(owned)&set(running)
        for name in owned:assert (runtime.STATE/'vms'/name/'manifest.json').exists()
        report['retained_stopped_vms']=7;report['active_before_resume']=0
        print('PASS: all seven real domains stopped, QEMU RAM released, manifests/disks retained',flush=True)
        for client,lab,name in zip(clients,solos,names):
            resumed=client.wait(client.action(lab,'start'),'READY');assert resumed['generation']==1
            terminal=Terminal(client,resumed)
            try:terminal.command("test -f /home/cra/solo-preserved && printf 'SOLO_%s\\n' RESUMED",b'SOLO_RESUMED')
            finally:terminal.close()
            note=client.request('/api/v1/course/days/1/notebook');assert note['body']=='Mit bevarede soloarbejde' and note['revision']==1
            assert client.request('/api/v1/labs/'+lab['id']+'/progress')['total_points']==100
        note=clients[1].request('/api/v1/teams/'+team['id']+'/notebook');assert note['revision']==2 and note['body']=='Vi har sammenlignet kilde og observation.'
        assert len(clients[1].request('/api/v1/teams/'+team['id']+'/findings')['items'])==1
        print('PASS: both solo generations/files/notebooks/personal score restored; team notes/findings retained',flush=True)
        report['status']='PASS'
    finally:
        errors=[]
        for path in paths:
            b=json.loads(path.read_text());seen=set();scope_errors=[]
            for role in ('teacher','student'):
                try:
                    client=Client(b['users'][role]);labs=client.request('/api/v1/labs')
                except Exception:
                    scope_errors.append('scoped_'+role+'_list');continue
                for lab in labs:
                    if lab['id'] in seen or not lab['can_manage']:continue
                    seen.add(lab['id'])
                    try:
                        deadline=time.monotonic()+360
                        while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                            if time.monotonic()>deadline:raise TimeoutError('Cleanup lifecycle')
                            time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                        if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED')
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
        (out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Team work cleanup incomplete')
    print('M5-02 '+report['status'],flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL: '+type(e).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
