"""Approved CLASS-INCIDENT-BRIDGE-01 rollout. Journalled maintenance; no reset/destroy of original labs."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import time
from uuid import uuid4

SOURCE = Path('/home/crauser/cra-incident-bridge')
MAIN = Path('/home/crauser/cyber-range-academy-plan')
PRIVATE = Path('/home/crauser/cra-incident-bridge-deployment')
STATE_FILE = PRIVATE / 'state.json'
os.environ['CRA_NODE_HOME'] = '/home/crauser/cra-m0-02'
sys.path[:0] = [str(MAIN / 'infra/linux'), str(MAIN / 'scripts'), '/home/crauser/cra-mission-three-test']
sys.path.insert(0,str(SOURCE/'node'))
import runtime
import acceptance_client as ac
from private_client import PrivateClient
from stage_node2_runtime import SSH, TARGET
from lab_tools import upload

CONTROL_BACKUP = '/var/lib/cra-completion-backup/class-incident-bridge-01'
SERVICES = ['cra-api', 'cra-worker', 'cra-worker-node2', 'cra-reconciler', 'cra-relay']


def atomic(path, value):
    tmp = path.with_name(path.name + '.' + uuid4().hex)
    with tmp.open('x') as stream:
        json.dump(value, stream, indent=2, default=str); stream.flush(); os.fsync(stream.fileno())
    tmp.chmod(0o600); tmp.replace(path)


def save(state):
    atomic(STATE_FILE, state)


def shell(command, timeout=120, input=None):
    result = runtime.ssh('cra-control', command, timeout=timeout, input=input)
    if result.returncode:
        (PRIVATE / 'control-error.txt').write_text(result.stdout + result.stderr)
        raise RuntimeError('Control operation failed; details retained privately')
    return result.stdout


def control(code, payload=None, root=False, timeout=120):
    prefix = 'sudo ' if root else 'sudo -u craapp '
    return json.loads(shell('cd /opt/cra/backend && ' + prefix + '.venv/bin/python -c ' + shlex.quote(code),
                            timeout, json.dumps(payload)))


def remote(code, timeout=120):
    prefix = "import os,sys,json; from pathlib import Path; os.environ.update(CRA_NODE_HOME='/home/crauser/cra-m0-02',CRA_NODE_ID='2',CRA_NODE_BIND='100.64.0.2');sys.path.insert(0,'/home/crauser/cra-node2/infra/linux');import runtime\n"
    result = subprocess.run([*SSH, TARGET, 'python3 -'], input=prefix + code,
                            text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        (PRIVATE / 'node2-error.txt').write_text(result.stdout + result.stderr)
        raise RuntimeError('Node2 operation failed; details retained privately')
    return json.loads(result.stdout)


SNAPSHOT = '''import json,hashlib
from cra.db import connect
with connect() as db:
 db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 labs=[dict(r) for r in db.execute("SELECT id,organization_id,user_id,team_id,mission_id,mission_version,generation,slot,node_id,desktop_enabled,state,desired_state FROM labs WHERE state<>'DESTROYED' ORDER BY id")]
 progress={}
 for table in ['notebooks','team_notebooks','team_findings','score_ledger','team_score_ledger','lab_completions','objective_attempts','lab_flags','mission_releases','lab_participants','teams','team_members','users','memberships']:
  rows=sorted(json.dumps(dict(r),sort_keys=True,default=str) for r in db.execute('SELECT * FROM '+table))
  progress[table]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows).encode()).hexdigest(),'row_hashes':sorted(hashlib.sha256(x.encode()).hexdigest() for x in rows)}
 pending=db.execute("SELECT count(*) n FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()['n']
 print(json.dumps({'labs':labs,'progress':progress,'pending':pending},default=str))
'''

DISKS = '''import hashlib
items={}
for name in runtime.virsh('list','--all','--name').stdout.split():
 if name=='cra-control':continue
 runtime.owned_domain(name)
 assert runtime.virsh('domstate',name).stdout.strip()=='shut off'
 path=runtime.STATE/'vms'/name/'disk.qcow2'
 manifest=runtime.STATE/'vms'/name/'manifest.json'
 with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
 items[name]={'inode':path.stat().st_ino,'sha256':digest,'manifest':hashlib.sha256(manifest.read_bytes()).hexdigest()}
'''


def disks():
    scope = {'runtime': runtime}
    exec(DISKS, scope)
    return {'1': scope['items'], '2': remote(DISKS + "\nprint(json.dumps(items))", timeout=300)}


def identity(labs):
    return [{k:v for k,v in lab.items() if k not in ('state','desired_state')} for lab in labs]


def preserved(state):
    now = control(SNAPSHOT)
    assert now['pending'] == 0
    assert identity(now['labs']) == identity(state['before']['labs']), 'Lab identity changed'
    assert now['progress'] == state['before']['progress'], 'Original work checksum changed'
    return now


def teacher():
    path = Path('/home/crauser/cra-classroom-access/credentials.json')
    assert path.stat().st_mode & 0o077 == 0 and not path.is_symlink()
    ac.ORIGIN = Path('/home/crauser/cra-classroom-access/url.txt').read_text().strip()
    account = next(a for a in json.loads(path.read_text())['accounts'] if a['role'] == 'teacher')
    return PrivateClient(account)


def managed_lab(client, lab_id):
    overview=client.request('/api/v1/command-center/overview')
    assert not overview['truncated']
    return next(l for l in overview['labs'] if l['id']==lab_id)


def wait_managed(client, operation, expected, timeout):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        job=client.request('/api/v1/operations/'+operation['operation_id'])
        if job['status']=='FAILED':raise RuntimeError('Managed lifecycle failed: '+str(job.get('error_code')))
        if job['status']=='SUCCEEDED':
            lab=managed_lab(client,operation['lab_id'])
            if lab['state']==expected:return lab
        time.sleep(2)
    raise TimeoutError('Managed lifecycle did not reach '+expected)


def manifest():
    rows = json.loads((SOURCE / 'reports/local/incident-bridge/change-manifest.json').read_text())
    for row in rows:
        path = SOURCE / row['path']
        assert not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == row['after_sha256']
    return rows


def check_control_hashes(rows, installed=False):
    return control("""import hashlib,json,sys
from pathlib import Path
b=json.load(sys.stdin)
for row in b['rows']:
 p=Path('/opt/cra')/row['path']
 assert not p.is_symlink()
 actual=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
 assert actual in ([row['after_sha256']] if b['installed'] else [row.get('control_before_sha256',row['before_sha256']),row['after_sha256']]), row['path']
print('true')
""", {'rows':rows,'installed':installed})


def prepare():
    assert not STATE_FILE.exists(), 'Resume existing journal instead'
    rows = manifest()
    for row in rows:
        p = MAIN / row['path']
        assert (hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None) == row['before_sha256']
    # Verify the runtime source files that will actually be replaced.
    node_paths = [r for r in rows if r['path'].startswith(('node/', 'content/', 'backend/cra/'))
                  and not r['path'].startswith('node/test_')]
    # Unchanged rows may legitimately differ on a node that was not shipped them before.
    changed = [r for r in node_paths if r['before_sha256'] != r['after_sha256']]
    checks = remote("import hashlib\nrows=" + repr(changed) + "\nfor row in rows:\n p=Path('/home/crauser/cra-node2')/row['path']\n assert (hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None) in (row.get('node2_before_sha256',row['before_sha256']),row['after_sha256']), row['path']\nprint('true')")
    assert checks
    assert check_control_hashes([r for r in rows if r['path'].startswith(('backend/cra/','content/'))])
    before = control(SNAPSHOT)
    assert all(l['state'] in ('READY','STOPPED') for l in before['labs']) and not before['pending']
    state = {'purpose':'CLASS-INCIDENT-BRIDGE-01', 'phase':'prepared','manifest':rows,'node_paths':node_paths,
             'stop_keys':{},'start_keys':{},'jobs':{}}
    save(state)
    print('PASS: reviewed source hashes and both-node source baseline; stable original labs', flush=True)


def pause(state):
    assert state['phase'] in ('prepared','edge-closed','stopping','paused')
    if state['phase'] == 'paused':
        preserved(state);return
    shell('sudo systemctl stop cra-edge', timeout=120)
    if state['phase'] == 'prepared':state['phase']='edge-closed';save(state)
    if 'before' not in state:
        deadline=time.monotonic()+600
        while True:
            before=control(SNAPSHOT)
            if not before['pending']:break
            if time.monotonic()>deadline:raise TimeoutError('Original jobs did not drain')
            time.sleep(3)
        assert all(l['state'] in ('READY','STOPPED') for l in before['labs'])
        state['before']=before
        state['restore']=[l for l in before['labs'] if l['state']=='READY']
        state['stop_keys']={l['id']:str(uuid4()) for l in state['restore']}
        state['start_keys']={l['id']:str(uuid4()) for l in state['restore']}
        state['phase']='stopping';save(state)
    if not state.get('backup_ready'):
        # Durable backup before any lifecycle mutation. Keep database material in the guest.
        shell('sudo install -d -m 0700 '+CONTROL_BACKUP+' && sudo sh -c '+shlex.quote(
            'umask 077; runuser -u postgres -- pg_dump -Fc cra > '+CONTROL_BACKUP+'/before.dump')+
            ' && sudo pg_restore --file=/dev/null '+CONTROL_BACKUP+'/before.dump && sudo tar -czf '+
            CONTROL_BACKUP+'/code.tar.gz -C /opt/cra backend/cra content frontend/dist', timeout=240)
        state['backup_ready']=True;save(state)
    assert state.get('backup_ready'), 'Backup must be verified before stopping labs'
    client=teacher();org=client.request('/api/v1/auth/me')['organization_id']
    assert all(l['organization_id']==org for l in state['restore'])
    for lab in state['restore']:
        current=managed_lab(client,lab['id'])
        if current['state']!='STOPPED' and 'stop:'+lab['id'] not in state['jobs']:
            job=client.request('/api/v1/command-center/labs/'+lab['id']+'/stop',
                               {'generation':lab['generation']},key=state['stop_keys'][lab['id']],expected=202)
            state['jobs']['stop:'+lab['id']]=job;save(state)
    for i,lab in enumerate(state['restore'],1):
        if managed_lab(client,lab['id'])['state']!='STOPPED':
            wait_managed(client,state['jobs']['stop:'+lab['id']], 'STOPPED', timeout=900)
        print('Stopped original lab',i,'of',len(state['restore']),flush=True)
    current=preserved(state)
    assert all(l['state']=='STOPPED' for l in current['labs'])
    state['disks']=disks();state['phase']='paused';save(state)
    client.request('/api/v1/auth/logout',{})
    print('PASS: all original disks privately hashed; identities, notes, scores and flags unchanged',flush=True)


def stop_node_local():
    path=runtime.STATE/'node-agent.pid'
    if not path.exists():return
    pid=int(path.read_text());proc=Path('/proc')/str(pid)
    if proc.exists() and ') Z ' not in (proc/'stat').read_text():
        handle=os.pidfd_open(pid)
        assert str(MAIN/'node/agent.py').encode() in (proc/'cmdline').read_bytes().split(b'\0')
        signal.pidfd_send_signal(handle,signal.SIGTERM);os.close(handle)
        deadline=time.monotonic()+20
        while proc.exists() and ') Z ' not in (proc/'stat').read_text():
            if time.monotonic()>deadline:raise TimeoutError('Node1 shutdown')
            time.sleep(.1)
    path.unlink()


STOP_REMOTE = '''import signal,time,sqlite3
p=runtime.STATE/'node-agent.pid'
if p.exists():
 pid=int(p.read_text());proc=Path('/proc')/str(pid)
 if proc.exists() and ') Z ' not in (proc/'stat').read_text():
  fd=os.pidfd_open(pid)
  assert b'/home/crauser/cra-node2/node/agent.py' in proc.joinpath('cmdline').read_bytes().split(b'\\0')
  signal.pidfd_send_signal(fd,signal.SIGTERM);os.close(fd)
  deadline=time.monotonic()+20
  while proc.exists() and ') Z ' not in (proc/'stat').read_text():
   if time.monotonic()>deadline:raise TimeoutError('Node2 shutdown')
   time.sleep(.1)
 p.unlink()
backup=runtime.STATE/'incident-bridge-backup';backup.mkdir(mode=0o700,exist_ok=True)
if not (backup/'before.sqlite').exists():
 with sqlite3.connect(runtime.STATE/'node/state.sqlite') as src,sqlite3.connect(backup/'before.sqlite') as dst:src.backup(dst)
print('true')
'''


def install(state):
    assert state['phase'] in ('paused','installing','installed')
    if state['phase']=='installed':return
    assert manifest()==state['manifest'], 'Reviewed source changed since prepare'
    runtime_rows=[r for r in state['manifest'] if r['path'].startswith(('backend/cra/','content/'))]
    assert check_control_hashes(runtime_rows)
    build=[{'path':str(p.relative_to(SOURCE)),'before_sha256':None,'after_sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((SOURCE/'frontend/dist').rglob('*')) if p.is_file() and (p.name=='index.html' or 'assets' in p.parts)]
    if 'build' in state:assert build==state['build']
    else:state['build']=build;save(state)
    assert all(l['state']=='STOPPED' for l in preserved(state)['labs'])
    shell('sudo systemctl stop '+' '.join(SERVICES),timeout=120)
    stop_node_local();assert remote(STOP_REMOTE)
    state['phase']='installing';save(state)
    if not (PRIVATE/'node1-before.sqlite').exists():
        with sqlite3.connect(runtime.STATE/'node/state.sqlite') as src,sqlite3.connect(PRIVATE/'node1-before.sqlite') as dst:src.backup(dst)
    # Per-file before/after check makes interrupted source installation resumable.
    for row in state['manifest']:
        dest=MAIN/row['path'];current=hashlib.sha256(dest.read_bytes()).hexdigest() if dest.exists() else None
        assert current in (row['before_sha256'],row['after_sha256']),row['path']
        if current==row['after_sha256']:continue
        if dest.exists():
            backup=PRIVATE/'source-before'/row['path'];backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,backup)
        dest.parent.mkdir(parents=True,exist_ok=True)
        temp=dest.with_name(dest.name+'.incident-bridge-tmp');shutil.copy2(SOURCE/row['path'],temp);temp.replace(dest)
    # Only reviewed runtime files; never transfer secrets, journals, images or VM disks.
    paths=[r['path'] for r in state['node_paths']]
    backup_code="import tarfile\nfiles="+repr(paths)+"\np=runtime.STATE/'incident-bridge-backup/source.tar.gz'\nif not p.exists():\n with tarfile.open(p,'w:gz') as t:\n  for name in files:\n   f=Path('/home/crauser/cra-node2')/name\n   if f.exists():t.add(f,arcname=name)\nprint('true')"
    assert remote(backup_code)
    subprocess.run(['rsync','-a','--checksum','--relative','-e',shlex.join(SSH),
                    *[str(SOURCE)+'/./'+p for p in paths],TARGET+':/home/crauser/cra-node2/'],check=True)
    assert remote("import hashlib\nrows="+repr(state['node_paths'])+"\nassert all(hashlib.sha256((Path('/home/crauser/cra-node2')/r['path']).read_bytes()).hexdigest()==r['after_sha256'] for r in rows)\nprint('true')")
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w:gz') as tar:
        for row in state['manifest']:
            if row['path'].startswith(('backend/cra/','content/')):tar.add(SOURCE/row['path'],arcname=row['path'])
        for path in (SOURCE/'frontend/dist').rglob('*'):
            if path.is_file() and (path.name=='index.html' or 'assets' in path.parts):tar.add(path,arcname=str(path.relative_to(SOURCE)))
    upload('cra-control','/home/cra/cra-deploy/incident-bridge-release.tar.gz',archive.getvalue())
    shell('sudo tar -xzf /home/cra/cra-deploy/incident-bridge-release.tar.gz -C /opt/cra && '
          'sudo chown -R craapp:craapp /opt/cra/backend/cra /opt/cra/content /opt/cra/frontend/dist',timeout=120)
    assert check_control_hashes(runtime_rows+state['build'], installed=True)
    assert disks()==state['disks'], 'Stopped disk contents changed during installation'
    # Start existing node processes; no key generation or host service installation.
    import provision_node
    provision_node.start(restart=False)
    assert remote("import contextlib,io\nimport provision_node\nwith contextlib.redirect_stdout(io.StringIO()):provision_node.start(restart=False)\nprint('true')")
    shell('sudo systemctl start '+' '.join(SERVICES)+' && sudo systemctl is-active '+' '.join(SERVICES),timeout=120)
    state['phase']='installed';save(state)
    print('PASS: reviewed code installed on control and both nodes; original disk hashes unchanged',flush=True)


def restore(state):
    assert state['phase'] in ('installed','restoring','restored')
    client=teacher();state['phase']='restoring';save(state)
    for lab in state['restore']:
        current=managed_lab(client,lab['id'])
        if current['state']!='READY' and 'start:'+lab['id'] not in state['jobs']:
            job=client.request('/api/v1/command-center/labs/'+lab['id']+'/start',
                               {'generation':lab['generation']},key=state['start_keys'][lab['id']],expected=202)
            state['jobs']['start:'+lab['id']]=job;save(state)
    for i,lab in enumerate(state['restore'],1):
        if managed_lab(client,lab['id'])['state']!='READY':
            wait_managed(client,state['jobs']['start:'+lab['id']], 'READY',timeout=1200)
        print('Restarted original lab',i,'of',len(state['restore']),flush=True)
    now=preserved(state)
    assert [l['state'] for l in now['labs']]==[l['state'] for l in state['before']['labs']]
    client.request('/api/v1/auth/logout',{})
    state['phase']='restored';save(state)
    print('PASS: all previously running labs READY again; previously stopped labs still STOPPED',flush=True)


def verify(state):
    now=preserved(state)
    assert [l['state'] for l in now['labs']]==[l['state'] for l in state['before']['labs']]
    expected={1:{'cra-control'},2:set()}
    for lab in state['restore']:
        prefix='cra-'+lab['id'].replace('-','')[:10]+'-'+format(lab['generation'],'x')
        # All original live environments are individual pairs; refuse to guess team topology.
        assert lab['team_id'] is None
        for suffix in ('o','t'):
            name=prefix+suffix;expected[lab['node_id']].add(name)
            if lab['node_id']==1:
                m=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text())
                assert m['ownership']=={k:lab[k] for k in ('organization_id','user_id','generation')}|{'lab_id':lab['id']}
                assert runtime.kvm_status(name)['enabled'] and not m['egress']
                assert runtime.ssh(name,'/bin/true').returncode==0
            else:
                assert remote("name="+repr(name)+";owner="+repr({k:lab[k] for k in ('organization_id','user_id','generation')}|{'lab_id':lab['id']})+"\nm=json.loads((runtime.STATE/'vms'/name/'manifest.json').read_text());assert m['ownership']==owner and not m['egress'];assert runtime.kvm_status(name)['enabled'];assert runtime.ssh(name,'/bin/true').returncode==0;print('true')")
    assert set(runtime.virsh('list','--name').stdout.split())==expected[1]
    assert set(remote("print(json.dumps(runtime.virsh('list','--name').stdout.split()))"))==expected[2]
    health=control('''import json
from cra.node_client import request
from cra.node_contracts import NodeInventory
out=[]
for i in (1,2):
 v=NodeInventory.model_validate(request('/v1/inventory',node_id=i))
 assert v.connected and all(l.ownership_valid for l in v.labs)
 assert v.available_memory_mib>=4096 and v.physical_memory_mib>=v.running_declared_memory_mib+4096
 out.append({'node_id':i,'ready':sum(l.state=='READY' for l in v.labs),'available_mib':v.available_memory_mib})
print(json.dumps(out))
''')
    shell('sudo systemctl is-active postgresql@16-main '+' '.join(SERVICES))
    state['verified']={'running_restored':len(state['restore']),'stopped_preserved':len(now['labs'])-len(state['restore']),
                       'nodes':health,'kvm_and_guest_ssh':'PASS','progress_and_disk_preservation':'PASS'}
    save(state);print(json.dumps(state['verified']),flush=True)


def guest_on(node_id, name, command, timeout=120):
    if node_id == 1:
        result = runtime.ssh(name, command, timeout=timeout)
        if result.returncode:
            raise RuntimeError('Guest command failed: ' + result.stderr[-500:])
        return result.stdout.strip()
    return remote("name=" + repr(name) + "\nr=runtime.ssh(name," + repr(command) +
                  ",timeout=" + str(timeout) + ")\nassert r.returncode==0,r.stderr\nprint(json.dumps(r.stdout))")


def open_for_acceptance(state):
    assert state.get('verified') and state['phase'] in ('restored','open-testing')
    shell('sudo systemctl start cra-edge && sudo systemctl is-active cra-edge')
    state['phase']='open-testing';save(state)
    print('PASS: original class access reopened before disposable new-mission acceptance',flush=True)


def accept(state):
    assert state.get('verified') and state['phase'] in ('open-testing','accepting','accepted')
    if state['phase']=='accepted':return
    state['phase']='accepting';save(state)
    subprocess.run([MAIN/'backend/.venv/bin/python',SOURCE/'scripts/accept_incident_bridge_live.py'],check=True)
    evidence=json.loads((PRIVATE/'acceptance.json').read_text())
    assert evidence.get('completed') and evidence.get('browser_passed')
    assert len(evidence['stages'])==4 and all(x.get('completed') for x in evidence['stages'].values())
    state['accept']={'completed_all_four':True,'synthetic_only':True,'public_browser':'PASS'}
    state['phase']='accepted';save(state)


def reopen(state):
    assert state['phase'] == 'accepted' and state.get('verified')
    shell('sudo systemctl start cra-edge && sudo systemctl is-active cra-edge')
    state['phase'] = 'open'; save(state)
    print('PASS: CRA web access reopened; no mission released to real students', flush=True)


if __name__=='__main__':
    os.umask(0o077);PRIVATE.mkdir(mode=0o700,exist_ok=True)
    import fcntl
    handle=(PRIVATE/'operation.lock').open('a');fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','pause','install','restore','verify','open_for_acceptance','accept','reopen'])
    phase=parser.parse_args().phase
    if phase=='prepare':prepare()
    else:
        state=json.loads(STATE_FILE.read_text());assert state['purpose']=='CLASS-INCIDENT-BRIDGE-01'
        globals()[phase](state)
