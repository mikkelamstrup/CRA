"""CLASS-CAPACITY-04: preserve existing solos; provision only four missing labs on node2."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import threading
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'infra/linux'), str(ROOT / 'scripts')]
import runtime
import node2_acceptance as n2
from acceptance_client import Client, ORIGIN

PRIVATE = Path('/home/crauser/cra-classroom-preparation')
STATE = PRIVATE / 'state.json'
ACCESS = Path('/home/crauser/cra-classroom-access/credentials.json')
REPORT = ROOT / 'reports/local/class-capacity-04'


def atomic(path, data):
    assert not path.is_symlink()
    temp = path.with_name(path.name + '.' + uuid4().hex)
    with temp.open('x') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    temp.chmod(0o600); temp.replace(path)


def save(state):
    atomic(STATE, json.dumps(state, indent=2))


def control(code, payload, *, root=False, timeout=120):
    compile(code, 'classroom-scoped-operation', 'exec')
    prefix = 'sudo ' if root else 'sudo -u craapp '
    result = runtime.ssh('cra-control', 'cd /opt/cra/backend && ' + prefix + '.venv/bin/python -c ' + shlex.quote(code), input=json.dumps(payload), timeout=timeout)
    if result.returncode:
        raise RuntimeError('Scoped classroom operation failed; private output withheld')
    return json.loads(result.stdout)


def accounts():
    assert ACCESS.is_file() and not ACCESS.is_symlink() and ACCESS.stat().st_mode & 0o077 == 0
    data = json.loads(ACCESS.read_text())
    selected = [a for a in data['accounts'] if re.fullmatch(r'elev[0-9]+', a['username'])]
    assert sorted(int(a['username'][4:]) for a in selected) == list(range(1, 19))
    assert all(a['role'] == 'student' for a in selected)
    return data, selected


def inventory(data, selected, *, allow_pending=False):
    return control('''import json,sys
from cra.db import connect
b=json.load(sys.stdin)
with connect() as db:
 db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 if not b['allow_pending']:assert not db.execute("SELECT id FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()
 rows=db.execute("SELECT id,user_id,organization_id,mission_id,mission_version,generation,node_id,slot,state,desired_state,desktop_enabled,team_id FROM labs WHERE state<>'DESTROYED' ORDER BY id").fetchall()
 ids=set(b['ids']);solos=[r for r in rows if str(r['user_id']) in ids and r['team_id'] is None]
 assert all(str(r['organization_id'])==b['org'] and not r['desktop_enabled'] and r['state'] in (('READY','STOPPED','QUEUED','STARTING','PROVISIONING') if b['allow_pending'] else ('READY','STOPPED')) for r in solos)
 assert not db.execute("SELECT 1 FROM lab_participants WHERE lab_id=ANY(%s)",([r['id'] for r in solos],)).fetchone()
 assert len({r['user_id'] for r in solos})==len(solos)
 print(json.dumps({'solos':solos,'others':[r for r in rows if r not in solos]},default=str))
''', {'org': data['organization_id'], 'ids': [a['id'] for a in selected], 'allow_pending': allow_pending})


PRESERVE = '''import json,sys,os
from pathlib import Path
from cra.db import connect
b=json.load(sys.stdin);p=Path('/var/lib/cra/class-capacity-04-preservation.json')
with connect() as db:
 db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 values={}
 for table in ('users','memberships','notebooks','team_notebooks','team_findings','score_ledger','team_score_ledger','lab_completions','objective_attempts','lab_flags','mission_releases','lab_participants','teams','team_members'):
  values[table]=sorted(json.dumps(dict(r),sort_keys=True,default=str) for r in db.execute('SELECT * FROM '+table))
 values['labs']=sorted(json.dumps(dict(r),sort_keys=True,default=str) for r in db.execute("SELECT id,organization_id,user_id,team_id,mission_id,mission_version,generation,slot,node_id,desktop_enabled FROM labs WHERE state<>'DESTROYED'"))
 if b['mode']=='before' and not p.exists():
  temp=p.with_name(p.name+'.'+__import__('uuid').uuid4().hex)
  with os.fdopen(os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
   json.dump({'operation_id':b['operation_id'],'values':values},stream);stream.flush();os.fsync(stream.fileno())
  os.link(temp,p);temp.unlink()
 else:
  assert not p.is_symlink() and p.stat().st_mode&0o077==0
  original=json.loads(p.read_text());assert original['operation_id']==b['operation_id']
  for table,rows in original['values'].items():
   assert set(rows)<=set(values[table])
   if table not in ('labs','lab_flags'):assert rows==values[table]
print('true')
'''


def preserve(state, mode):
    assert control(PRESERVE, {'mode': mode, 'operation_id': state['operation_id']})
    assert hashlib.sha256(ACCESS.read_bytes()).hexdigest() == state['credential_hash']


def prepare():
    data, selected = accounts()
    if STATE.exists():
        state = json.loads(STATE.read_text()); assert state['purpose'] == 'CLASS-CAPACITY-04'
        preserve(state, 'before' if state['phase'] == 'prepared' else 'after')
        print('PASS: existing preparation journal and preservation verified'); return
    current = inventory(data, selected)
    present = {lab['user_id'] for lab in current['solos']}
    missing = [a['id'] for a in selected if a['id'] not in present]
    assert len(missing) == 4 and len(current['solos']) == 14
    assert {i: sum(l['node_id'] == i for l in current['solos']) for i in (1, 2)} == {1: 12, 2: 2}
    assert all(l['state'] == 'STOPPED' for l in current['others'])
    state = {'purpose': 'CLASS-CAPACITY-04', 'operation_id': uuid4().hex, 'phase': 'prepared', 'credential_hash': hashlib.sha256(ACCESS.read_bytes()).hexdigest(), 'original': current, 'missing': missing, 'request_keys': {identity: str(uuid4()) for identity in missing}, 'created': {}}
    save(state); preserve(state, 'before')
    print('PASS: 18 student identities; four missing labs; original metadata privately preserved')


def backup(state):
    assert control('''import json,sys,os,subprocess
from pathlib import Path
b=json.load(sys.stdin);assert len(b['operation_id'])==32;int(b['operation_id'],16)
p=Path('/var/lib/cra-completion-backup')/('class-capacity-04-'+b['operation_id'])
p.mkdir(mode=0o700,exist_ok=True);assert not p.is_symlink() and p.stat().st_mode&0o077==0
final=p/'before.dump'
if not final.exists():
 temp=p/('partial-'+__import__('uuid').uuid4().hex)
 with temp.open('xb') as stream:
  temp.chmod(0o600);subprocess.run(['runuser','-u','postgres','--','pg_dump','-Fc','cra'],stdout=stream,stderr=subprocess.DEVNULL,check=True);stream.flush();os.fsync(stream.fileno())
 subprocess.run(['pg_restore','--file=/dev/null',str(temp)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
 os.link(temp,final);temp.unlink()
assert not final.is_symlink() and final.stat().st_mode&0o077==0
subprocess.run(['pg_restore','--file=/dev/null',str(final)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
print('true')
''', state, root=True)


def registry(state, restore=False):
    # Durable old/new values; a concurrent owner's configuration is never replaced.
    if 'registry' not in state:
        value = control("import json;from pathlib import Path;print(json.dumps(Path('/etc/cra/nodes.json').read_text()))", {})
        assert json.loads(value) == [{'id': 1, 'host': '10.0.2.2', 'accept_new': True}, {'id': 2, 'host': '100.64.0.2', 'accept_new': True}]
        state['registry'] = value; save(state)
    old = state['registry']; restricted = json.dumps([{**r, 'accept_new': r['id'] == 2} for r in json.loads(old)])
    assert control('''import json,sys,os
from pathlib import Path
b=json.load(sys.stdin);p=Path('/etc/cra/nodes.json');assert p.is_file() and not p.is_symlink()
assert p.read_text() in (b['old'],b['restricted'])
temp=p.with_name('nodes.class-capacity-04-'+__import__('uuid').uuid4().hex)
with os.fdopen(os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644),'w') as stream:
 stream.write(b['desired']);stream.flush();os.fsync(stream.fileno())
os.replace(temp,p);print('true')
''', {'old': old, 'restricted': restricted, 'desired': old if restore else restricted}, root=True)


def wait_ready(client, lab_id, timeout=1200):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lab = client.request('/api/v1/labs/' + lab_id)
        if lab['state'] == 'READY': return lab
        if lab['state'] in ('ERROR', 'STOPPED'): raise RuntimeError('Lab not ready; retained without reset')
        time.sleep(2)
    raise TimeoutError('Readiness deadline; operation and files retained')


def provision():
    state = json.loads(STATE.read_text()); preserve(state, 'after'); backup(state)
    _, selected = accounts(); pending = []
    try:
        registry(state)
        for account in selected:
            identity = account['id']
            if identity not in state['missing']: continue
            client = Client(account)
            # Reuse the same request key even after loss of an HTTP response.
            op = client.request('/api/v1/labs', {'mission_id': 'first-files', 'mission_version': 1, 'desktop_enabled': False}, key=state['request_keys'][identity], expected=202)
            assert state['created'].get(identity, op['lab_id']) == op['lab_id']
            state['created'][identity] = op['lab_id']; save(state); pending.append((client, op['lab_id']))
    finally:
        if 'registry' in state: registry(state, restore=True)
    for client, lab_id in pending:
        lab = wait_ready(client, lab_id)
        assert n2.binding(lab_id) == 2 and lab['generation'] == 1 and not lab['desktop_enabled']
        print('PASS: one missing student environment READY on node2-example', flush=True)
    preserve(state, 'after'); state['phase'] = 'four-created'; save(state)
    data, selected = accounts(); current = inventory(data, selected)
    assert len(current['solos']) == 18
    assert {i: sum(l['node_id'] == i for l in current['solos']) for i in (1, 2)} == {1: 12, 2: 6}
    print('PASS: 18 normal student environments exist; placement 12+6; original labs preserved')


def start():
    state = json.loads(STATE.read_text()); assert state['phase'] in ('four-created', 'starting', 'all-ready')
    preserve(state, 'after'); data, selected = accounts(); current = inventory(data, selected, allow_pending=state['phase'] == 'starting')
    assert len(current['solos']) == 18
    state['phase'] = 'starting'; save(state)
    by_user = {l['user_id']: l for l in current['solos']}; pending = []
    for account in selected:
        client = Client(account); lab = by_user[account['id']]
        if lab['state'] == 'STOPPED':
            operation = client.action(lab, 'start')
            assert operation['lab_id'] == lab['id']
        pending.append((client, lab['id']))
    for index, (client, lab_id) in enumerate(pending, 1):
        wait_ready(client, lab_id)
        print('READY:', index, 'of 18', flush=True)
    preserve(state, 'after'); state['phase'] = 'all-ready'; save(state)
    print('PASS: all 18 normal student environments READY; no reset or migration')


def check():
    from bounded_browser import run_browser
    state = json.loads(STATE.read_text()); assert state['phase'] == 'all-ready'
    data, selected = accounts(); current = inventory(data, selected)
    assert len(current['solos']) == 18 and all(l['state'] == 'READY' and l['desired_state'] == 'READY' for l in current['solos'])
    assert current['others'] == state['original']['others']
    assert all(l['state'] == 'STOPPED' for l in current['others'])
    placement = {str(i): sum(l['node_id'] == i for l in current['solos']) for i in (1, 2)}
    assert placement == {'1': 12, '2': 6}
    expected = {1: {'cra-control'}, 2: set()}
    for lab in current['solos']:
        prefix = 'cra-' + lab['id'].replace('-', '')[:10] + '-' + format(lab['generation'], 'x')
        for suffix, memory in [('o', 1024), ('t', 768)]:
            name = prefix + suffix; expected[lab['node_id']].add(name)
            if lab['node_id'] == 1:
                manifest = json.loads((runtime.STATE / 'vms' / name / 'manifest.json').read_text())
                assert runtime.kvm_status(name) == {'enabled': True, 'present': True}
                result = runtime.ssh(name, '/bin/true'); assert result.returncode == 0
            else:
                inspected = n2.inspect_vm(lab['id'], name); manifest = inspected['manifest']
                assert inspected['kvm'] == {'enabled': True, 'present': True}
                assert n2.guest(lab['id'], name, '/bin/true') == ''
            assert manifest.get('node_id', 1) == lab['node_id'] and manifest['memory_mib'] == memory and not manifest['egress']
            assert manifest['ownership'] == {k: lab[k] for k in ('organization_id', 'user_id', 'generation')} | {'lab_id': lab['id']}
    assert set(runtime.virsh('list', '--name').stdout.split()) == expected[1]
    assert set(n2.inventory()['running']) == expected[2]
    node_budget = control('''import json
from cra.node_client import request
from cra.node_contracts import NodeInventory
result={}
for identity,count in [(1,12),(2,6)]:
 value=NodeInventory.model_validate(request('/v1/inventory',node_id=identity))
 assert value.node_id==identity and all(l.ownership_valid for l in value.labs)
 assert sum(l.state=='READY' for l in value.labs)==count
 declared=count*1792+(4096 if identity==1 else 0)
 assert value.running_declared_memory_mib==declared
 assert value.physical_memory_mib>=declared+4096 and value.available_memory_mib>=4096
 result[str(identity)]={'physical_mib':value.physical_memory_mib,'declared_mib':declared,'required_reserve_mib':4096,'available_mib':value.available_memory_mib,'free_disk_gib':value.free_disk_gib}
print(json.dumps(result))
''', {})
    run_browser('browser_classroom_ready.py', {'url': ORIGIN, 'accounts': selected, 'labs': {l['user_id']: l['id'] for l in current['solos']}}, timeout=600, evidence_path=REPORT / 'browser-ready.log')
    final = inventory(data, selected)
    assert all(l['state'] == 'READY' for l in final['solos']) and final['others'] == current['others']
    preserve(state, 'after')
    raw_registry = control("import json;from pathlib import Path;print(json.dumps(Path('/etc/cra/nodes.json').read_text()))", {})
    assert raw_registry == state['registry']
    result = {'task': 'CLASS-CAPACITY-04', 'status': 'PENDING_RESOURCE_CHECK', 'normal_students_ready': 18, 'placement': placement, 'running_vms': {'1': len(expected[1]), '2': len(expected[2])}, 'node_budget': node_budget, 'kvm_and_guest_ssh': 'PASS', 'browser_connections_and_reconnect': 'PASS', 'old_accounts_notes_scores_flags_and_bindings_preserved': True, 'non_student_labs_still_stopped': len(current['others']), 'registry_restored_byte_exact': True, 'load_test_12_6': 'NOT RUN', 'migration': 'NOT PERFORMED'}
    atomic(REPORT / 'readiness.json', json.dumps(result, indent=2)); print('PASS: all 18 actual student labs READY simultaneously; 18 browser connections; original work preserved')


def monitored(phase):
    samples = []; errors = []; stop = threading.Event()
    def poll():
        while not stop.is_set():
            try:
                remote = n2.inventory(timeout=10)
                local = {line.split(':')[0]: int(line.split()[1]) // 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.split(':')[0] in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree')}
                samples.append({'at': time.time(), 'nodes': {'1': local, '2': remote['memory']}})
            except Exception:
                errors.append('resource_sample_failed')
            stop.wait(3)
    thread = threading.Thread(target=poll); thread.start()
    try:
        globals()[phase]()
    finally:
        stop.set(); thread.join(timeout=15)
        atomic(REPORT / (phase + '-memory.json'), json.dumps({'samples': samples, 'errors': errors}, indent=2))
    assert samples and not errors and not thread.is_alive()
    assert all(node['MemAvailable'] >= 4096 and node['SwapTotal'] == node['SwapFree'] for s in samples for node in s['nodes'].values())
    if phase == 'check':
        report = json.loads((REPORT / 'readiness.json').read_text()); report['status'] = 'PASS'
        report['minimum_available_mib'] = {i: min(s['nodes'][i]['MemAvailable'] for s in samples) for i in ('1', '2')}
        report['observed_swap_used_mib'] = {'1': 0, '2': 0}
        atomic(REPORT / 'readiness.json', json.dumps(report, indent=2))
    print('PASS: observed RAM reserve >=4096 MiB on both hosts; no swap', flush=True)


def summarize():
    """Keep a failed resource criterion distinct from completed functional checks."""
    path = REPORT / 'readiness.json'; report = json.loads(path.read_text())
    assert report['status'] in ('PENDING_RESOURCE_CHECK', 'PASS', 'FAIL_SWAP_CRITERION')
    assert report['normal_students_ready'] == 18 and report['placement'] == {'1': 12, '2': 6}
    assert report['kvm_and_guest_ssh'] == report['browser_connections_and_reconnect'] == 'PASS'
    assert report['old_accounts_notes_scores_flags_and_bindings_preserved'] and report['non_student_labs_still_stopped'] == 2
    assert report['registry_restored_byte_exact']
    value = json.loads((REPORT / 'check-memory.json').read_text())
    assert value['samples'] and not value['errors']
    minimum = {i: min(s['nodes'][i]['MemAvailable'] for s in value['samples']) for i in ('1', '2')}
    swap = {i: max(s['nodes'][i]['SwapTotal'] - s['nodes'][i]['SwapFree'] for s in value['samples']) for i in ('1', '2')}
    assert all(n >= 4096 for n in minimum.values()), 'RAM reserve criterion failed'
    raw = REPORT / 'readiness-before-summary.json'
    if not raw.exists():
        with raw.open('x') as stream: stream.write(path.read_text())
    report.update(functional_status='PASS', ram_reserve='PASS', monitor_status='PASS', minimum_available_mib=minimum, observed_swap_used_mib=swap, zero_swap_criterion='PASS' if not any(swap.values()) else 'FAIL', status='PASS' if not any(swap.values()) else 'FAIL_SWAP_CRITERION')
    atomic(path, json.dumps(report, indent=2))
    print('Functional:', report['functional_status'], 'RAM reserve:', report['ram_reserve'], 'zero swap:', report['zero_swap_criterion'], 'overall:', report['status'])


if __name__ == '__main__':
    os.umask(0o077)
    try:
        assert runtime.NODE == Path('/home/crauser/cra-m0-02')
        PRIVATE.mkdir(mode=0o700, exist_ok=True)
        REPORT.mkdir(parents=True, exist_ok=True)
        assert not PRIVATE.is_symlink() and PRIVATE.stat().st_mode & 0o077 == 0
        lock = (PRIVATE / 'operation.lock').open('a'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not any((runtime.SECRETS / n).exists() for n in ('acceptance-solo-nodes.json', 'acceptance-teaching.json'))
        assert not list(runtime.STATE.glob('cra-acceptance-*.json'))
        parser = argparse.ArgumentParser(); parser.add_argument('phase', choices=('prepare', 'provision', 'start', 'check', 'summarize', 'restore-registry'))
        phase = parser.parse_args().phase
        if phase == 'restore-registry': registry(json.loads(STATE.read_text()), restore=True)
        elif phase in ('start', 'check'): monitored(phase)
        else: globals()[phase]()
    except Exception as error:
        print('FAIL:', type(error).__name__, 'private journal retained; no reset/deletion performed')
        sys.exit(1)
