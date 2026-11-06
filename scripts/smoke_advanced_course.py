"""Real M7-02 solo/reset and two-operator team acceptance; synthetic identities only."""
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from acceptance_class import provision, deactivate
from acceptance_client import Client
from acceptance_terminal import Terminal
from smoke_desktop import run_browser

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime


def command(client,lab,text,marker):
    terminal=Terminal(client,lab)
    try:return terminal.command(text+"; printf '"+marker[:-4]+"%s\\n' DONE",marker.encode(),timeout=45).decode(errors='replace')
    finally:terminal.close()


def solve(client,lab,mission):
    ip=lab['target_ip']
    if mission=='packet-hunter':
        text=command(client,lab,"tcpdump -nn -A -r /home/cra/academy/capture/incident.pcap tcp port 8080 | grep -oE 'HTTP/1.0 200|NL-505|CRA[{][0-9a-f]{32}[}]' | sort -u",'ADV_CAPTURE_DONE')
        assert 'NL-505' in text and 'HTTP/1.0 200' in text
        status=command(client,lab,"test \"$(curl -s -o /dev/null -w '%{http_code}' http://"+ip+":8080/evidence/trace-505.txt)\" = 404 && printf 'HTTP_%s\\n' MISSING",'ADV_CURRENT_DONE')
        assert 'HTTP_MISSING' in status
        proof='''import json
from datetime import datetime
raw=open('/home/cra/academy/capture/requests.json').read();r=json.loads(raw)
assert 'CRA{' not in raw
assert [e['path'] for e in r]==['/','/evidence/trace-505.txt','/']
assert all(e['status']==200 and e['bytes']>0 and datetime.fromisoformat(e['time_utc']).utcoffset().total_seconds()==0 for e in r)
print('PROVENANCE_'+'OK')'''
        check=command(client,lab,'python3 -c '+shlex.quote(proof),'ADV_PROVENANCE_DONE')
        assert 'PROVENANCE_OK' in check
        prefix='cra-'+lab['id'].replace('-','')[:10]+'-'+format(lab['generation'],'x')
        r=runtime.ssh(prefix+'t','test ! -e /home/cra/cra-page.txt && test ! -e /srv/cra-target/evidence/trace-505.txt')
        assert r.returncode==0
    elif mission=='vulnerable-app':
        before=command(client,lab,"test \"$(curl -s -o /dev/null -w '%{http_code}' 'http://"+ip+":8081/api/reports?id=702')\" = 401 && printf 'HTTP_%s\\n' UNAUTHORIZED",'ADV_LOGIN_DONE')
        assert 'HTTP_UNAUTHORIZED' in before
        text=command(client,lab,"curl -fsS -c /home/cra/academy-app.cookies -d 'username=analyst&password=nordlys-training-only' http://"+ip+":8081/login >/dev/null; curl -fsS -b /home/cra/academy-app.cookies 'http://"+ip+":8081/api/me' | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r[\"allowed_reports\"]==[701]; print(\"ALLOWED_\"+\"701\")'; curl -fsS -b /home/cra/academy-app.cookies 'http://"+ip+":8081/api/reports?id=702' | python3 -c 'import json,sys,re; r=json.load(sys.stdin); assert r[\"owner\"]==\"administrator\"; print(re.search(r\"CRA[{][0-9a-f]{32}[}]\",r[\"body\"])[0])'",'ADV_APP_DONE')
        assert 'ALLOWED_701' in text
    else:
        dns=command(client,lab,'cat /home/cra/academy/shadow/journal.txt; dig +short @'+ip+' rapport.nordlys.test A; curl -fsS http://'+ip+':8080/service/','ADV_SHADOW_DONE')
        assert ip in dns.replace('\r','').splitlines() and 'NL-404' in dns
        text=command(client,lab,'ssh -F /home/cra/academy/access/ssh_config nordlys cat /srv/cra-access/reports/rapport-404.txt','ADV_REPORT_DONE')
    flags=re.findall(r'CRA\{[0-9a-f]{32}\}',text)
    assert flags
    return flags[-1]


def submit(client,lab,objective,flag):
    return client.request('/api/v1/labs/'+lab['id']+'/objectives/'+objective+'/attempts',{'generation':lab['generation'],'flag':flag})


def main():
    paths=[];report={'task':'M7-02','status':'FAIL','missions':[]}
    out=ROOT/'reports/local/m7-02';out.mkdir(parents=True,exist_ok=True)
    try:
        selected=set(os.environ.get('CRA_ACCEPT_SOLOS','packet-hunter,vulnerable-app,operation-shadow').split(','))
        assert selected and selected<={'packet-hunter','vulnerable-app','operation-shadow'}
        report['solo_selection']=sorted(selected)
        resume=os.environ.get('CRA_ACCEPT_RESUME')=='owned-advanced-app'
        if resume:
            path=runtime.SECRETS/'acceptance-teaching.json';b=json.loads(path.read_text())
            assert b['purpose']=='cra-acceptance-teaching' and set(b['users'])=={'teacher','student'}
            assert all(u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id'] for u in b['users'].values())
            paths.append(path)
        else:path,b=provision(on_owned=paths.append)
        student=Client(b['users']['student']);teacher=Client(b['users']['teacher'])
        existing=None
        if resume:
            labs=[l for l in student.request('/api/v1/labs') if l['state']!='DESTROYED']
            assert len(labs)==1 and labs[0]['mission_id']=='vulnerable-app' and labs[0]['generation']==1 and labs[0]['state']=='READY' and labs[0]['team_id'] is None
            assert 'vulnerable-app' in selected
            existing=labs[0];report['resumed_owned_gui_lab']=True
        for mission,objective in [('packet-hunter','find-captured-report'),('vulnerable-app','find-authorization-flaw'),('operation-shadow','complete-investigation')]:
            if mission not in selected:continue
            points={'packet-hunter':250,'vulnerable-app':200,'operation-shadow':300}[mission]
            began=time.monotonic()
            if existing is not None and mission=='vulnerable-app':lab=existing;elapsed=None
            else:
                lab=student.wait(student.request('/api/v1/labs',{'mission_id':mission,'mission_version':1,'desktop_enabled':mission in ('packet-hunter','vulnerable-app')},expected=202),'READY',timeout=600)
                elapsed=round(time.monotonic()-began,2)
            print('READY:',mission,elapsed,'seconds',flush=True)
            prefix='cra-'+lab['id'].replace('-','')[:10]+'-1'
            manifest=json.loads((runtime.STATE/'vms'/(prefix+'o')/'manifest.json').read_text())
            profile={'mission':mission,'create_seconds':elapsed,'operator_memory_mib':manifest['memory_mib'],'desktop_profile':manifest.get('desktop_profile')}
            if mission in ('packet-hunter','vulnerable-app'):assert profile['operator_memory_mib']==2048 and profile['desktop_profile']=='gui-v1'
            report.setdefault('profiles',[]).append(profile)
            if mission in ('packet-hunter','vulnerable-app'):
                try:run_browser('browser_advanced_course.py',{**b,'mission':mission,'lab_id':lab['id'],'target_ip':lab['target_ip']},timeout=150)
                except Exception:
                    r=runtime.ssh(prefix+'o',"python3 -c \"import pathlib; p=pathlib.Path('/home/cra/academy-gui-report.html'); s=p.read_text() if p.exists() else ''; print({'saved':p.exists(),'admin':'administrator' in s,'flag':'CRA{' in s,'unauthorized':'login_required' in s})\"")
                    print('GUI diagnostic (booleans only):',r.stdout,flush=True)
                    raise
            flag=solve(student,lab,mission)
            assert not submit(student,lab,objective,'wrong-synthetic-flag')['accepted']
            assert submit(student,lab,objective,flag)['points_awarded']==points
            assert submit(student,lab,objective,flag)['points_awarded']==0
            lab=student.wait(student.action(lab,'reset'),'READY',timeout=600)
            assert lab['generation']==2 and not submit(student,lab,objective,flag)['accepted']
            new=solve(student,lab,mission);assert new!=flag
            confirmed=submit(student,lab,objective,new)
            assert confirmed['accepted'] and confirmed['points_awarded']==0
            assert objective in student.request('/api/v1/labs/'+lab['id']+'/progress')['completed_objectives']
            report['missions'].append({'id':mission,'solution_reset_score':'PASS'})
            student.wait(student.action(lab,'destroy'),'DESTROYED')
            print('PASS solution/reset/scoring:',mission,flush=True)
        team=teacher.request('/api/v1/teams',{'request_key':str(uuid4()),'name':'Synthetic Service Desk','members':[{'user_id':b['users'][role]['user_id'],'role':'operator'} for role in ('teacher','student')]},expected=201)
        for mission,objective in [('packet-hunter','find-captured-report'),('vulnerable-app','find-authorization-flaw'),('operation-shadow','complete-investigation')]:
            points={'packet-hunter':250,'vulnerable-app':200,'operation-shadow':300}[mission]
            lab=teacher.wait(teacher.request('/api/v1/labs',{'mission_id':mission,'mission_version':1,'team_id':team['id']},expected=202),'READY',timeout=600)
            student_lab=student.request('/api/v1/labs/'+lab['id'])
            assert student_lab['target_ip']!=lab['target_ip']
            first=solve(teacher,lab,mission);second=solve(student,student_lab,mission);assert first==second
            if mission=='operation-shadow':
                prefix='cra-'+lab['id'].replace('-','')[:10]+'-1'
                keys=[]
                for suffix in ['o','p']:
                    r=runtime.ssh(prefix+suffix,'cat /home/cra/academy/access/id_ed25519.pub')
                    assert r.returncode==0;keys.append(r.stdout.strip())
                assert keys[0]!=keys[1]
            with ThreadPoolExecutor(max_workers=2) as pool:
                scores=list(pool.map(lambda pair:submit(pair[0],pair[1],objective,first)['points_awarded'],[(teacher,lab),(student,student_lab)]))
            assert sorted(scores)==[0,points]
            report[mission+'_two_operators_distinct_subnets_shared_score']='PASS'
            teacher.wait(teacher.action(lab,'destroy'),'DESTROYED')
            print('PASS two-operator team:',mission,flush=True)
        b['course_days']=[1,2,3,4,5,6,7]
        run_browser('browser_course_days.py',b,timeout=120)
        report['status']='PASS'
    except Exception as error:
        report['failure']={'type':type(error).__name__,'lines':[f.lineno for f in traceback.extract_tb(error.__traceback__)]}
        raise
    finally:
        errors=[]
        for path in paths:
            bundle=json.loads(path.read_text())
            try:
                seen=set()
                for role in ('teacher','student'):
                    client=Client(bundle['users'][role])
                    for lab in client.request('/api/v1/labs'):
                        if lab['id'] in seen:continue
                        seen.add(lab['id'])
                        end=time.monotonic()+900
                        while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                            if time.monotonic()>end:raise TimeoutError('Cleanup lifecycle wait')
                            time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                        if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED',timeout=300)
                        prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                        assert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.split())
                        assert not any(p.name.startswith(prefix) for p in (runtime.STATE/'vms').iterdir())
                deactivate(path)
            except Exception:errors.append('synthetic_scope_cleanup')
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        (out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Synthetic cleanup incomplete')
    print('M7-02 '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL:',type(e).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
