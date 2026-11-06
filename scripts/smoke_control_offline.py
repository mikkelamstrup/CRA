"""Exercise the real local classroom flow with control-VM external routes removed.

Only the owned control guest is changed. A guest timer restores routes if this
process is interrupted; host networking and other applications are untouched.
"""
import json,os,shlex,signal,subprocess,sys,time,traceback
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime
from lab_tools import upload


def remote(command,timeout=30):
    result=runtime.ssh('cra-control',command,timeout=timeout)
    if result.returncode:raise RuntimeError('Owned control network check failed')
    return result.stdout


def routes():
    return {family:json.loads(remote('ip -'+family+' -j route show default')) for family in ['4','6']}


def stop_process_group(process):
    """Only a child started with start_new_session=True may be supplied."""
    for sig in [signal.SIGTERM,signal.SIGKILL]:
        try:os.killpg(process.pid,sig)
        except ProcessLookupError:pass
        try:
            process.communicate(timeout=5)
            if sig==signal.SIGKILL:return
        except subprocess.TimeoutExpired:continue
    raise RuntimeError('Owned rehearsal process group did not exit')


def main():
    out=ROOT/'reports/local/m7-04';out.mkdir(parents=True,exist_ok=True)
    assert runtime.virsh('list','--name').stdout.split()==['cra-control']
    assert not (runtime.SECRETS/'acceptance-teaching.json').exists()
    before=routes();assert before['4'] and len(before['4'])<=2 and len(before['6'])<=2
    restore=[];remove=[]
    for family,items in before.items():
        for item in items:
            assert item['dst']=='default' and item.get('dev')
            assert not item.get('flags'), 'Unsupported route flags; no mutation performed'
            assert not set(item)-{'dst','gateway','dev','protocol','prefsrc','metric','flags','pref','table'}
            args=['default']
            for key,option in [('gateway','via'),('dev','dev'),('protocol','proto'),('prefsrc','src'),('metric','metric'),('pref','pref'),('table','table')]:
                if key in item:args.extend([option,str(item[key])])
            restore.append(['/usr/sbin/ip','-'+family,'route','replace',*args])
            remove.append(['/usr/sbin/ip','-'+family,'route','del',*args])
    # Real positive transport control before changing any route; no request payload.
    check="import socket; s=socket.create_connection(('1.1.1.1',443),timeout=4); s.close()"
    remote('python3 -c '+shlex.quote(check))
    identity=uuid4().hex;unit='cra-offline-restore-'+identity
    browser_unit='cra-acceptance-'+identity
    staged='/home/cra/cra-deploy/'+unit+'.py';script='/var/lib/cra-completion-backup/'+unit+'.py'
    code='import subprocess\ncommands='+repr(restore)+'\nfor command in commands:subprocess.run(command,check=True,capture_output=True)\n'
    upload('cra-control',staged,code.encode())
    remote('sudo install -m 0700 '+staged+' '+script+' && rm -- '+staged)
    report={'task':'M7-04','status':'FAIL','scope':'control VM without external default routes; local browser and real lab'}
    armed=False;process=None;communicated=False
    try:
        remote('sudo systemd-run --unit='+unit+' --on-active=300 /usr/bin/python3 '+script)
        armed=True
        for command in remove:remote('sudo '+shlex.join(command))
        assert not any(routes().values())
        negative="import socket; s=socket.socket(); s.settimeout(3); code=s.connect_ex(('1.1.1.1',443)); s.close(); assert code!=0"
        remote('python3 -c '+shlex.quote(negative))
        process=subprocess.Popen([str(ROOT/'backend/.venv/bin/python'),str(ROOT/'scripts/smoke_teaching.py')],cwd=ROOT,env={**os.environ,'CRA_BROWSER_UNIT':browser_unit},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:stdout,_=process.communicate(timeout=240);communicated=True
        except subprocess.TimeoutExpired:
            raise RuntimeError('Owned rehearsal process timed out')
        print(stdout,end='',flush=True)
        assert process.returncode==0
        assert not any(routes().values()), 'Restoration timer expired before offline proof ended'
        evidence=json.loads((ROOT/'reports/local/m4-02/acceptance.json').read_text())
        assert evidence['status']=='PASS' and evidence['cleanup_errors']==[]
        report.update(status='PASS',real_lab_teaching=evidence,external_transport_rejected=True)
    finally:
        errors=[]
        # Runs for timeout, KeyboardInterrupt and any other interrupted wait.
        # A normally completed communicate has already observed pipe EOF and reaped.
        if process is not None and not communicated:
            try:stop_process_group(process)
            except Exception:errors.append('owned_process_group_unconfirmed')
        try:
            remote('sudo python3 '+script)
            assert routes()==before
            remote('python3 -c '+shlex.quote(check))
            report['original_routes_restored']=True
            if armed:remote('sudo systemctl stop '+unit+'.timer')
            remote('sudo rm -- '+script)
        except Exception:errors.append('route_restore_unconfirmed_watchdog_retained')
        try:
            if 'owned_process_group_unconfirmed' in errors:raise RuntimeError('Avoid racing live local test writer')
            loaded=remote('sudo systemctl show '+browser_unit+'.service -p LoadState --value').strip()
            if loaded!='not-found':
                remote('sudo systemctl stop '+browser_unit+'.service')
                assert remote('sudo systemctl show '+browser_unit+'.service -p MainPID --value').strip()=='0'
            # This scope did not exist before this test. Remote browser cgroup is now stopped.
            path=runtime.SECRETS/'acceptance-teaching.json'
            if path.exists():
                from acceptance_class import deactivate
                from acceptance_client import Client
                b=json.loads(path.read_text());assert b['purpose']=='cra-acceptance-teaching'
                assert all(u['username'].startswith('cra_test_') and u['organization_id']==b['organization_id'] for u in b['users'].values())
                seen=set()
                for account in b['users'].values():
                    client=Client(account)
                    for lab in client.request('/api/v1/labs'):
                        if lab['id'] in seen:continue
                        seen.add(lab['id'])
                        try:
                            end=time.monotonic()+900
                            while lab['state'] not in ('READY','STOPPED','ERROR','DESTROYED'):
                                if time.monotonic()>end:raise TimeoutError('Owned lifecycle cleanup')
                                time.sleep(1);lab=client.request('/api/v1/labs/'+lab['id'])
                            if lab['state']!='DESTROYED':client.wait(client.action(lab,'destroy'),'DESTROYED',timeout=300)
                        except Exception:errors.append('owned_lab_cleanup')
                if not errors:deactivate(path)
        except Exception:errors.append('owned_browser_scope_cleanup')
        if runtime.virsh('list','--name').stdout.split()!=['cra-control']:errors.append('synthetic_cleanup_unconfirmed')
        report['cleanup_errors']=errors
        if errors:report['status']='FAIL'
        (out/'offline-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        if errors:raise RuntimeError('Control restore or owned lab cleanup needs recovery')
    print('M7-04 offline classroom '+report['status'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL:',type(error).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
