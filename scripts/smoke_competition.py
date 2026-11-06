"""Real two-team KVM CTF acceptance. Every resource belongs to synthetic scope."""
import json,re,sys,time,traceback
from pathlib import Path
from uuid import uuid4
from acceptance_class import provision,deactivate
from acceptance_client import Client
from smoke_advanced_course import command
from smoke_desktop import run_browser
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime


def main():
    paths=[];report={'task':'M7-03','status':'FAIL'};out=ROOT/'reports/local/m7-03';out.mkdir(parents=True,exist_ok=True)
    try:
        path,b=provision(on_owned=paths.append);teacher=Client(b['users']['teacher']);student=Client(b['users']['student'])
        teams={}
        for role in ['teacher','student']:
            teams[role]=teacher.request('/api/v1/teams',{'request_key':str(uuid4()),'name':'Synthetic '+role,'members':[{'user_id':b['users'][role]['user_id'],'role':'operator'}]},expected=201)
        c=teacher.request('/api/v1/competitions',{'request_key':str(uuid4()),'title':'Synthetic Championship','duration_seconds':1800,'team_ids':[teams[role]['id'] for role in ['teacher','student']]},expected=201)
        c=teacher.request('/api/v1/competitions/'+c['id']+'/actions',{'revision':c['revision'],'action':'start'})
        for role,client in [('teacher',teacher),('student',student)]:
            start=time.monotonic()
            lab=teacher.wait(teacher.request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1,'team_id':teams[role]['id']},expected=202),'READY',timeout=600)
            own=client.request('/api/v1/labs/'+lab['id'])
            text=command(client,own,'curl -fsS http://'+own['target_ip']+':8080','CTF_REAL_DONE')
            flags=re.findall(r'CRA\{[0-9a-f]{32}\}',text);assert flags
            b[role+'_flag']=flags[-1];b[role+'_lab']=own
            report[role+'_create_seconds']=round(time.monotonic()-start,2)
        assert b['student_flag']!=b['teacher_flag']
        # A different team's real flag must fail; regular practice points remain separate.
        own=b['student_lab']
        result=student.request('/api/v1/competitions/'+c['id']+'/attempts',{'request_key':str(uuid4()),'lab_id':own['id'],'generation':own['generation'],'flag':b['teacher_flag']})
        assert not result['accepted']
        practice=student.request('/api/v1/labs/'+own['id']+'/objectives/find-service/attempts',{'generation':own['generation'],'flag':b['student_flag']})
        assert practice['accepted'] and practice['points_awarded']==100
        before=student.request('/api/v1/competitions/'+c['id']+'/board');assert sum(r['points'] for r in before['standings'])==0
        run_browser('browser_competition.py',b,timeout=150)
        after=student.request('/api/v1/competitions/'+c['id']+'/board')
        assert after['status']=='ended' and [r['points'] for r in after['standings']]==[150,150]
        run_browser('browser_course_days.py',{**b,'course_days':list(range(1,9))},timeout=150)
        report.update(status='PASS',distinct_flags=True,practice_score_separate=True,final_points=[150,150])
    finally:
        errors=[]
        for path in paths:
            b=json.loads(path.read_text())
            try:
                teacher=Client(b['users']['teacher'])
                for c in teacher.request('/api/v1/competitions'):
                    teacher.request('/api/v1/competitions/'+c['id']+'/actions',{'revision':c['revision'],'action':'end'})
                seen=set()
                for role in ['teacher','student']:
                    client=Client(b['users'][role])
                    for lab in client.request('/api/v1/labs'):
                        if lab['id'] in seen:continue
                        seen.add(lab['id']);end=time.monotonic()+900
                        while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                            if time.monotonic()>end:raise TimeoutError('Cleanup lifecycle')
                            time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                        if lab['state']!='DESTROYED':teacher.wait(teacher.action(lab,'destroy'),'DESTROYED',timeout=300)
                        prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                        assert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.split())
                deactivate(path)
            except Exception:errors.append('synthetic_cleanup')
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        (out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Synthetic cleanup incomplete')
    print('M7-03 '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL:',type(e).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
