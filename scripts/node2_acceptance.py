"""Fixed private node2-example access for synthetic acceptance, not a student API."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from stage_node2_runtime import SSH,TARGET,REMOTE_SOURCE,REMOTE_RUNTIME
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime


def remote(code,*,timeout=120):
    prefix="import os,sys,json; from pathlib import Path; os.environ.update(CRA_NODE_HOME='/home/crauser/cra-m0-02',CRA_NODE_ID='2');sys.path.insert(0,'/home/crauser/cra-node2/infra/linux');import runtime\n"
    result=subprocess.run([*SSH,TARGET,'python3 -'],input=prefix+code,text=True,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('Scoped node2 inspection failed; private output withheld')
    return json.loads(result.stdout)


def inventory(*,timeout=120):
    return remote("print(json.dumps({'running':runtime.virsh('list','--name').stdout.split(),'defined':runtime.virsh('list','--all','--name').stdout.split(),'load':list(os.getloadavg()),'free_disk_gib':__import__('shutil').disk_usage(runtime.NODE).free//1024**3,'cpu_ticks':[int(n) for n in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]],'memory':{line.split(':')[0]:int(line.split()[1])//1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.split(':')[0] in ('MemTotal','MemAvailable','SwapTotal','SwapFree')}}))",timeout=timeout)


def inspect_vm(lab_id,name):
    return remote("lab_id="+repr(lab_id)+";name="+repr(name)+"\nm=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text());assert m['ownership']['lab_id']==lab_id and m['node_id']==2\nprint(json.dumps({'manifest':m,'kvm':runtime.kvm_status(name)}))")


def guest(lab_id,name,command,*,input=None,timeout=90):
    return remote("lab_id="+repr(lab_id)+";name="+repr(name)+"\nm=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text());assert m['ownership']['lab_id']==lab_id and m['node_id']==2\nr=runtime.ssh(name,"+repr(command)+",input="+repr(input)+",timeout="+str(timeout)+")\nassert r.returncode==0;print(json.dumps(r.stdout))",timeout=timeout+20)


def absent(lab_id):
    return remote("prefix='cra-'+"+repr(lab_id)+".replace('-','')[:10]+'-'\nassert not any(n.startswith(prefix) for n in runtime.virsh('list','--all','--name').stdout.split())\nfor folder in [runtime.STATE/'vms',runtime.SECRETS]:assert not any(p.name.startswith(prefix) for p in folder.iterdir())\nprint('true')")


def binding(lab_id):
    code="from cra.db import connect\nwith connect() as db:\n row=db.execute('SELECT node_id FROM labs WHERE id=%s',("+repr(lab_id)+",)).fetchone();assert row;print(row['node_id'])"
    result=runtime.ssh('cra-control','cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c '+shlex.quote(code))
    if result.returncode:raise RuntimeError('Binding read failed')
    return int(result.stdout)


def config_mode(*,node2_only):
    # Exact known registry, only accept_new toggles; all prior bytes retained for restoration.
    path=runtime.STATE/'node2-acceptance-registry.json'
    if node2_only:
        assert not path.exists()
        r=runtime.ssh('cra-control','sudo cat /etc/cra/nodes.json');assert r.returncode==0
        values=json.loads(r.stdout)
        assert values==[{'id':1,'host':'10.0.2.2','accept_new':True},{'id':2,'host':'100.64.0.2','accept_new':True}]
        path.write_text(r.stdout);path.chmod(0o600);values[0]['accept_new']=False
        raw=json.dumps(values)
    else:
        raw=path.read_text()
    original=json.loads(path.read_text())
    test_profile=[{**item,'accept_new':False if item['id']==1 else True} for item in original]
    expected=json.dumps(test_profile) if not node2_only else path.read_text()
    code="import os,sys,json; from pathlib import Path; p=Path('/etc/cra/nodes.json'); assert p.is_file() and not p.is_symlink(); data=sys.stdin.read(); assert p.read_text() in "+repr([expected,raw])+"; tmp=p.with_suffix('.acceptance-tmp'); fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o644); f=os.fdopen(fd,'w');f.write(data);f.close();os.replace(tmp,p);assert p.read_text()==data"

    result=runtime.ssh('cra-control','sudo python3 -c '+shlex.quote(code),input=raw)
    if result.returncode:raise RuntimeError('Registry test profile update failed')
    if not node2_only:path.unlink()


def outage_recovery(client,lab):
    """Pause only the owned node2 agent, with an independent 60s resume watchdog."""
    import time
    from smoke_advanced_course import command
    assert binding(lab['id'])==2
    prefix='cra-'+lab['id'].replace('-','')[:10]+'-'
    before=inventory();assert before['defined'] and all(n.startswith(prefix) for n in before['defined'])
    assert runtime.virsh('list','--name').stdout.split()==['cra-control']
    original=(lab['id'],lab['generation'],lab['target_ip'])
    write_command="printf 'kept\\n' > /home/cra/outage-proof && printf 'OUTAGE_%s\\n' WRITTEN"
    assert 'OUTAGE_WRITTEN' not in write_command
    proof=command(client,lab,write_command,'OUTAGE_WRITE_DONE')
    assert 'OUTAGE_WRITTEN' in proof  # tmux may place ANSI codes around the output.
    pause='''import signal,subprocess,time
p=runtime.STATE/'node-agent.pid';pid=int(p.read_text());proc=Path('/proc')/str(pid)
fd=os.pidfd_open(pid)
assert proc.joinpath('cmdline').read_bytes().split(b'\\0')==[b'/home/crauser/cra-node2/backend/.venv/bin/python',b'/home/crauser/cra-node2/node/agent.py',b'']
assert set(runtime.virsh('list','--all','--name').stdout.split())==NAMES
start=proc.joinpath('stat').read_text().rsplit(') ',1)[1].split()[19]
watch="import os,signal,time;time.sleep(60);signal.pidfd_send_signal("+str(fd)+",signal.SIGCONT);os.close("+str(fd)+")"
subprocess.Popen(['/usr/bin/python3','-c',watch],pass_fds=(fd,),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
signal.pidfd_send_signal(fd,signal.SIGSTOP);os.close(fd)
print(json.dumps({'pid':pid,'start':start}))
'''.replace('NAMES',repr(set(before['defined'])))
    identity=None
    try:
        identity=remote(pause)
        deadline=time.monotonic()+40
        while True:
            current=client.request('/api/v1/labs/'+lab['id'])
            if current['state']=='ERROR' and current['error_code']=='node_unavailable':break
            if time.monotonic()>deadline:raise TimeoutError('Actual node outage was not observed')
            time.sleep(1)
        client.request('/api/v1/labs/'+lab['id']+'/terminal-grants',{},expected=409)
        overview=client.request('/api/v1/command-center/overview')
        states={item['node_id']:item for item in overview['nodes']}
        assert states[2]['stale'] and not states[2]['connected']
        assert not states[1]['stale'] and states[1]['connected']
        assert set(inventory()['running'])==set(before['running'])
    finally:
        if identity is not None:
            resume='''import signal
pid=PID;proc=Path('/proc')/str(pid);fd=os.pidfd_open(pid)
assert proc.joinpath('stat').read_text().rsplit(') ',1)[1].split()[19]==START
assert proc.joinpath('cmdline').read_bytes().split(b'\\0')==[b'/home/crauser/cra-node2/backend/.venv/bin/python',b'/home/crauser/cra-node2/node/agent.py',b'']
signal.pidfd_send_signal(fd,signal.SIGCONT);os.close(fd);print('true')
'''.replace('PID',str(identity['pid'])).replace('START',repr(identity['start']))
            assert remote(resume)
    deadline=time.monotonic()+90
    while True:
        current=client.request('/api/v1/labs/'+lab['id'])
        if current['state']=='READY':break
        if time.monotonic()>deadline:raise TimeoutError('Actual node recovery absent')
        time.sleep(1)
    assert (current['id'],current['generation'],current['target_ip'])==original and binding(lab['id'])==2
    read_command="grep -qx kept /home/cra/outage-proof && printf 'OUTAGE_%s\\n' PRESERVED"
    assert 'OUTAGE_PRESERVED' not in read_command
    proof=command(client,current,read_command,'OUTAGE_RECOVERY_DONE')
    assert 'OUTAGE_PRESERVED' in proof
    print('PASS: actual node2 agent outage, grant denial, node1 healthy, same running VMs/files/generation recovered',flush=True)
