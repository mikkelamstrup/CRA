"""Real simultaneous solo labs across two hosts; synthetic scopes only."""
import argparse,hashlib,json,os,re,secrets,shlex,sys,threading,time,traceback
from pathlib import Path
from uuid import uuid4
from acceptance_client import Client,ORIGIN
from bounded_browser import run_browser,stop_all
from smoke_advanced_course import command
import node2_acceptance as n2
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'infra/linux'),str(ROOT/'node')]
import runtime


def control(code,bundle):
    result=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code),input=json.dumps(bundle),timeout=90)
    if result.returncode:raise RuntimeError('Scoped synthetic control operation failed')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--students',type=int,choices=(15,18),default=15);parser.add_argument('--interactive-browser-retry',action='store_true');args=parser.parse_args()
    count=args.students
    out=ROOT/('reports/local/class-capacity-03' if count==18 else 'reports/local/m8-03');out.mkdir(parents=True,exist_ok=True)
    scope=runtime.SECRETS/'acceptance-solo-nodes.json'
    if '--interactive-browser-retry' in sys.argv:assert sys.stdin.isatty(), 'Bounded retry requires an owned interactive session'
    assert not scope.exists() and not (runtime.STATE/'node2-acceptance-registry.json').exists()
    assert runtime.virsh('list','--name').stdout.split()==['cra-control'] and not n2.inventory()['defined']
    originals=set(runtime.virsh('list','--all','--name').stdout.split())
    diskstats={p:(p.stat().st_size,p.stat().st_mtime_ns) for name in originals-{'cra-control'} for p in (runtime.STATE/'vms'/name).glob('*.qcow2')}
    org=str(uuid4());accounts=[{'user_id':str(uuid4()),'organization_id':org,'username':'cra_test_'+uuid4().hex[:12],'password':secrets.token_urlsafe(24),'role':'teacher' if i==0 else 'student'} for i in range(count+1)]
    bundle={'purpose':'cra-acceptance-solo-nodes','organization_id':org,'accounts':accounts,'students':count,'url':'http://127.0.0.1:8000' if ORIGIN=='http://127.0.0.1:28000' else ORIGIN}
    with os.fdopen(os.open(scope,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as stream:json.dump(bundle,stream)
    report={'task':'CLASS-CAPACITY-03' if count==18 else 'M8-03','students':count,'profile':f'{count} simultaneous CLI solos / {count} operators / {count} targets / two physical hosts','status':'FAIL','browser_load':'NOT RUN'}
    samples=[];monitor_errors=[];stop=threading.Event();created=[];provisioned=False
    def monitor():
        try:
            while not stop.is_set():
                sample_started=time.time();remote_sample=n2.inventory(timeout=5);second=remote_sample['memory']
                first={line.split(':')[0]:int(line.split()[1])//1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.split(':')[0] in ('MemTotal','MemAvailable','SwapTotal','SwapFree')}
                samples.append({'time':time.time(),'started':sample_started,'duration':time.time()-sample_started,'nodes':{'1':first,'2':second},'node1_load':list(os.getloadavg()),'node2_load':remote_sample['load'],'free_disk_gib':{'1':__import__('shutil').disk_usage(runtime.NODE).free//1024**3,'2':remote_sample['free_disk_gib']},'cpu_ticks':{'1':[int(n) for n in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]],'2':remote_sample['cpu_ticks']}})
                stop.wait(2)
        except Exception:monitor_errors.append('host_monitor_failed')
    monitor_thread=threading.Thread(target=monitor);monitor_thread.start()
    try:
        control('''import json,sys
from cra.auth import PASSWORDS
from cra.db import connect
b=json.load(sys.stdin);assert b['purpose']=='cra-acceptance-solo-nodes' and b['students'] in (15,18) and len(b['accounts'])==b['students']+1
with connect() as db:
 db.execute('INSERT INTO organizations VALUES(%s,%s)',(b['organization_id'],'Synthetic two-node solos'))
 for u in b['accounts']:
  assert u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id'] and u['role'] in ('teacher','student')
  db.execute('INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)',(u['user_id'],u['username'],PASSWORDS.hash(u['password'])))
  db.execute('INSERT INTO memberships VALUES(%s,%s,%s)',(u['user_id'],u['organization_id'],u['role']))
''',bundle)
        provisioned=True
        for account in accounts[1:]:
            client=Client(account);began=time.monotonic()
            operation=client.request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1},expected=202)
            created.append({'client':client,'account':account,'operation':operation,'began':began})
        # All requests are accepted before waiting: exercises actual durable queued reservations.
        deadline=time.monotonic()+2400;pending=list(created)
        while pending:
            assert not monitor_errors and all(x['MemAvailable']>=4096 for sample in samples for x in sample['nodes'].values())
            for entry in list(pending):
                job=entry['client'].request('/api/v1/operations/'+entry['operation']['operation_id'])
                if job['status']=='FAILED':raise RuntimeError('Synthetic solo provisioning failed: '+str(job['error_code']))
                if job['status']=='SUCCEEDED':
                    lab=entry['client'].request('/api/v1/labs/'+entry['operation']['lab_id']);assert lab['state']=='READY'
                    entry.update(lab=lab,node_id=n2.binding(lab['id']),ready_seconds=round(time.monotonic()-entry['began'],2))
                    prefix='cra-'+lab['id'].replace('-','')[:10]+'-1';entry['account']['expected_operator']=prefix+'o'
                    for suffix,memory in [('o',1024),('t',768)]:
                        name=prefix+suffix
                        if entry['node_id']==2:
                            inspected=n2.inspect_vm(lab['id'],name);manifest=inspected['manifest'];assert inspected['kvm']=={'enabled':True,'present':True}
                        else:
                            manifest=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text());assert runtime.kvm_status(name)=={'enabled':True,'present':True}
                        assert manifest['memory_mib']==memory and manifest['node_id']==entry['node_id'] and not manifest['egress']
                    pending.remove(entry);print('READY: synthetic solo',len(created)-len(pending),'of',count,'node',entry['node_id'],flush=True)
            if time.monotonic()>deadline:raise TimeoutError('Class creation deadline')
            if pending:time.sleep(2)
        report['placement']={str(i):sum(e['node_id']==i for e in created) for i in (1,2)}
        report['queued_to_ready_seconds']=[e['ready_seconds'] for e in created]
        assert all(report['placement'].values())
        for entry in created:
            result=command(entry['client'],entry['lab'],'curl --fail -s http://'+entry['lab']['target_ip']+':8080/','SOLO_FLAG_DONE')
            flag=re.search(r'CRA\{[0-9a-f]{32}\}',result);assert flag
            result=entry['client'].request('/api/v1/labs/'+entry['lab']['id']+'/objectives/find-service/attempts',{'generation':1,'flag':flag[0]})
            assert result['accepted'] and result['points_awarded']==100
        report['actual_terminal_flags']='PASS'
        from types import SimpleNamespace
        import memory_budget
        probe=SimpleNamespace(lab_id=uuid4(),organization_id=uuid4(),user_id=uuid4(),generation=1)
        before_one=set(runtime.virsh('list','--name').stdout.split());before_two=set(n2.inventory()['running'])
        try:memory_budget.require_capacity(probe,memory_budget.total_memory_mib())
        except ValueError as error:assert 'physical node capacity' in str(error)
        else:raise AssertionError('Node1 oversized memory accepted')
        assert n2.remote("sys.path.insert(0,'/home/crauser/cra-node2/node');import memory_budget;from types import SimpleNamespace;from uuid import uuid4\np=SimpleNamespace(lab_id=uuid4(),organization_id=uuid4(),user_id=uuid4(),generation=1)\ntry:memory_budget.require_capacity(p,memory_budget.total_memory_mib())\nexcept ValueError as error:assert 'physical node capacity' in str(error)\nelse:raise AssertionError('Oversized memory accepted')\nprint('true')")
        assert set(runtime.virsh('list','--name').stdout.split())==before_one and set(n2.inventory()['running'])==before_two
        report['both_live_nodes_oversized_ram_denied_without_mutation']='PASS'
        assert samples and time.time()-samples[-1]['time']<=5 and samples[-1]['duration']<=3
        latest=samples[-1]['nodes']
        for identity in (1,2):
            required=4096+report['placement'][str(identity)]*256+(1024 if identity==1 else 0)
            assert latest[str(identity)]['MemAvailable']>=required
        import select,shutil
        report['browser_attempts']=[]
        for browser_attempt in range(1,4):
            bundle['browser_attempt']=browser_attempt
            assert not monitor_errors and samples and time.time()-samples[-1]['time']<=5 and samples[-1]['duration']<=3
            for identity in (1,2):
                required=4096+report['placement'][str(identity)]*256+(1024 if identity==1 else 0)
                assert samples[-1]['nodes'][str(identity)]['MemAvailable']>=required
            evidence=out/('solo-browser-attempt-'+str(browser_attempt)+'.log')
            assert not evidence.exists(), 'Preserve earlier attempts before a new run'
            source=ROOT/'scripts/browser_solo_nodes.py'
            evidence.with_suffix('.sha256').write_text(__import__('hashlib').sha256(source.read_bytes()).hexdigest()+'\n')
            attempt={'attempt':browser_attempt,'evidence':evidence.name,'source_sha256':evidence.with_suffix('.sha256').read_text().strip(),'status':'FAIL'}
            report['browser_attempts'].append(attempt)
            try:
                output=run_browser('browser_solo_nodes.py',bundle,timeout=600,evidence_path=evidence)
                attempt['status']='FUNCTIONAL_PASS_PERFORMANCE_SEPARATE'
                shutil.copy2(evidence,out/'solo-browser-output.log')
                break
            except RuntimeError:
                if evidence.exists():shutil.copy2(evidence,out/'solo-browser-output.log')
                if '--interactive-browser-retry' not in sys.argv or browser_attempt==3 or monitor_errors:raise
                from bounded_browser import ACTIVE
                if ACTIVE or list(runtime.STATE.glob('cra-acceptance-*.json')):raise
                print('BROWSER FAILED: owned synthetic labs retained for at most 300 seconds; type retry to rerun reviewed browser source, anything else cleans up',flush=True)
                if not select.select([sys.stdin],[],[],300)[0] or sys.stdin.readline().strip()!='retry':raise
                assert not monitor_errors and all(x['MemAvailable']>=4096 for sample in samples for x in sample['nodes'].values())
                assert all(e['client'].request('/api/v1/labs/'+e['lab']['id'])['state']=='READY' and n2.binding(e['lab']['id'])==e['node_id'] for e in created)
        else:raise RuntimeError('No browser result')
        metrics=json.loads(next(line.removeprefix('BROWSER_METRICS ') for line in output.splitlines() if line.startswith('BROWSER_METRICS ')))
        report['browser_metrics']=metrics
        phase_start=metrics['load_release_epoch'];phase_end=metrics['load_finished_epoch']
        coverage=[sample for sample in samples if phase_start-5<=sample['time']<=phase_end+5]
        assert coverage and coverage[0]['time']<=phase_start and coverage[-1]['time']>=phase_end
        assert max(sample['duration'] for sample in coverage)<=3
        assert max(b['time']-a['time'] for a,b in zip(coverage,coverage[1:]))<=5
        report['load_memory_sample_count']=len(coverage)
        report['load_memory_max_gap_seconds']=max(b['time']-a['time'] for a,b in zip(coverage,coverage[1:]))
        report['browser_load']='PASS'
        for identity in (1,2):
            entry=next(e for e in created if e['node_id']==identity);client=entry['client'];lab=entry['lab']
            proof_text="printf 'saved\\n' > /home/cra/solo-node-proof && printf 'NODE_FILE_%s\\n' WRITTEN";assert 'NODE_FILE_WRITTEN' not in proof_text;proof=command(client,lab,proof_text,'SOLO_WRITE_DONE');assert 'NODE_FILE_WRITTEN' in proof
            lab=client.wait(client.action(lab,'stop'),'STOPPED',timeout=300)
            lab=client.wait(client.action(lab,'start'),'READY',timeout=600);assert lab['generation']==1 and n2.binding(lab['id'])==identity
            proof_text="grep -qx saved /home/cra/solo-node-proof && printf 'NODE_FILE_%s\\n' PRESERVED";assert 'NODE_FILE_PRESERVED' not in proof_text;proof=command(client,lab,proof_text,'SOLO_START_DONE');assert 'NODE_FILE_PRESERVED' in proof
            lab=client.wait(client.action(lab,'reset'),'READY',timeout=900);assert lab['generation']==2 and n2.binding(lab['id'])==identity
            proof_text="test ! -e /home/cra/solo-node-proof && printf 'NODE_RESET_%s\\n' VERIFIED";assert 'NODE_RESET_VERIFIED' not in proof_text;proof=command(client,lab,proof_text,'SOLO_RESET_DONE');assert 'NODE_RESET_VERIFIED' in proof
            assert client.request('/api/v1/labs/'+lab['id']+'/progress')['total_points']==100
            entry['lab']=lab
        report.update(status='PASS' if metrics['performance_pass'] else 'FAIL',both_nodes_stop_start_reset='PASS',performance='PASS' if metrics['performance_pass'] else 'FAIL')
    finally:
        errors=[]
        try:stop_all()
        except Exception:errors.append('browser_cleanup_unconfirmed')
        for account in accounts[1:] if provisioned and not errors else []:
            try:
                client=Client(account)
                for lab in client.request('/api/v1/labs'):
                    deadline=time.monotonic()+1200
                    while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                        if time.monotonic()>deadline:raise TimeoutError('Owned cleanup lifecycle')
                        time.sleep(2);lab=client.request('/api/v1/labs/'+lab['id'])
                    identity=n2.binding(lab['id'])
                    if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED',timeout=300)
                    if identity==2:assert n2.absent(lab['id'])
                    else:
                        prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
                        assert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.split())
                        for folder in (runtime.STATE/'vms',runtime.SECRETS):assert not any(p.name.startswith(prefix) for p in folder.iterdir())
            except Exception:errors.append('synthetic_solo_cleanup')
        stop.set();monitor_thread.join(timeout=15)
        if monitor_thread.is_alive() or not samples:errors.append('monitor_unconfirmed')
        errors.extend(monitor_errors)
        if samples and any(x['MemAvailable']<4096 for sample in samples for x in sample['nodes'].values()):errors.append('host_reserve_breached')
        try:
            assert runtime.virsh('list','--name').stdout.split()==['cra-control']
            assert set(runtime.virsh('list','--all','--name').stdout.split())==originals
            assert all(p.exists() and (p.stat().st_size,p.stat().st_mtime_ns)==value for p,value in diskstats.items())
            assert not n2.inventory()['defined']
            result=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp env PYTHONPATH=. .venv/bin/python /opt/cra-completion-test/preservation.py after');assert result.returncode==0
            report['original_data_preserved']='PASS'
        except Exception:errors.append('original_preservation_unconfirmed')
        if not provisioned:errors.append('identity_provisioning_unconfirmed')
        if not errors:
            try:
                control('''import json,sys
from cra.db import connect
b=json.load(sys.stdin);assert b['purpose']=='cra-acceptance-solo-nodes'
with connect() as db:
 assert not db.execute("SELECT id FROM labs WHERE organization_id=%s AND state<>'DESTROYED'",(b['organization_id'],)).fetchone()
 for u in b['accounts']:
  assert u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id']
  db.execute('UPDATE users SET active=false WHERE id=%s AND username=%s',(u['user_id'],u['username']))
  db.execute('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s',(u['user_id'],))
''',bundle)
                scope.rename(scope.with_name('acceptance-solo-nodes-'+uuid4().hex+'.completed.json'))
            except Exception:errors.append('scope_deactivation')
        report['cleanup_errors']=errors
        report['minimum_available_mib']={str(i):min((s['nodes'][str(i)]['MemAvailable'] for s in samples),default=0) for i in (1,2)}
        report['max_swap_used_mib']={str(i):max((s['nodes'][str(i)]['SwapTotal']-s['nodes'][str(i)]['SwapFree'] for s in samples),default=0) for i in (1,2)}
        if errors:report['status']='FAIL'
        (out/'solo-nodes-memory.json').write_text(json.dumps(samples)+'\n')
        (out/'solo-nodes-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Synthetic solo cleanup incomplete')
    print(report['task']+' '+str(count)+'-solo acceptance '+report['status'],flush=True)
    if report['status']!='PASS':raise AssertionError('Measured performance thresholds failed')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL:',type(error).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
