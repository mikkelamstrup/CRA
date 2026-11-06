"""Real M7-01 solo/reset and two-operator team acceptance; synthetic identities only."""
import json
from pathlib import Path
import re
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
    if mission=='web-trail-dns':
        text=command(client,lab,'dig +short @'+ip+' rapport.nordlys.test A; dig +tcp +short @'+ip+' rapport.nordlys.test A; dig @'+ip+' mangler.nordlys.test A +noall +comments; curl --fail --resolve rapport.nordlys.test:8080:'+ip+' http://rapport.nordlys.test:8080/rapporter/rapport-308.txt','COURSE_DNS_DONE')
        assert text.replace('\r','').splitlines().count(ip)>=2 and 'NXDOMAIN' in text
    else:
        text=command(client,lab,'ssh -F /home/cra/academy/access/ssh_config nordlys '+__import__('shlex').quote('id; cat /srv/cra-access/reports/rapport-404.txt; if cat /srv/cra-access/admin/internal.txt; then echo UNEXPECTED_ADMIN; else echo EXPECTED_DENIAL; fi; if sudo -n true; then echo UNEXPECTED_SUDO; else echo EXPECTED_NO_SUDO; fi'),'COURSE_SSH_DONE')
        assert 'EXPECTED_DENIAL' in text and 'EXPECTED_NO_SUDO' in text
        # Echoed command contains the names too; require no standalone success marker.
        assert '\r\nUNEXPECTED_ADMIN\r\n' not in text and '\r\nUNEXPECTED_SUDO\r\n' not in text
        assert 'fieldops' in text
    flags=re.findall(r'CRA\{[0-9a-f]{32}\}',text)
    assert flags
    return flags[-1]


def submit(client,lab,objective,flag):
    return client.request('/api/v1/labs/'+lab['id']+'/objectives/'+objective+'/attempts',{'generation':lab['generation'],'flag':flag})


def main():
    paths=[];report={'task':'M7-01','status':'FAIL','missions':[]}
    out=ROOT/'reports/local/m7-01';out.mkdir(parents=True,exist_ok=True)
    try:
        path,b=provision(on_owned=paths.append)
        student=Client(b['users']['student']);teacher=Client(b['users']['teacher'])
        for mission,objective in [('web-trail-dns','find-dns-report'),('access-granted','read-authorized-report')]:
            began=time.monotonic()
            lab=student.wait(student.request('/api/v1/labs',{'mission_id':mission,'mission_version':1},expected=202),'READY',timeout=600)
            print('READY:',mission,round(time.monotonic()-began,2),'seconds',flush=True)
            flag=solve(student,lab,mission)
            assert not submit(student,lab,objective,'wrong-synthetic-flag')['accepted']
            assert submit(student,lab,objective,flag)['points_awarded']==150
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
        for mission,objective in [('web-trail-dns','find-dns-report'),('access-granted','read-authorized-report')]:
            lab=teacher.wait(teacher.request('/api/v1/labs',{'mission_id':mission,'mission_version':1,'team_id':team['id']},expected=202),'READY',timeout=600)
            student_lab=student.request('/api/v1/labs/'+lab['id'])
            assert student_lab['target_ip']!=lab['target_ip']
            first=solve(teacher,lab,mission);second=solve(student,student_lab,mission);assert first==second
            if mission=='access-granted':
                prefix='cra-'+lab['id'].replace('-','')[:10]+'-1'
                keys=[]
                for suffix in ['o','p']:
                    r=runtime.ssh(prefix+suffix,'cat /home/cra/academy/access/id_ed25519.pub')
                    assert r.returncode==0;keys.append(r.stdout.strip())
                assert keys[0]!=keys[1]
            with ThreadPoolExecutor(max_workers=2) as pool:
                scores=list(pool.map(lambda pair:submit(pair[0],pair[1],objective,first)['points_awarded'],[(teacher,lab),(student,student_lab)]))
            assert sorted(scores)==[0,150]
            report[mission+'_two_operators_distinct_subnets_shared_score']='PASS'
            teacher.wait(teacher.action(lab,'destroy'),'DESTROYED')
            print('PASS two-operator team:',mission,flush=True)
        b['course_days']=[1,2,3,4]
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
    print('M7-01 '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL:',type(e).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
