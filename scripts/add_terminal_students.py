"""CLASS-CAPACITY-03: add only elev15–elev18; private state and preserved originals."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'infra/linux'), str(ROOT / 'scripts')]
import runtime
import node2_acceptance as n2
from acceptance_client import Client, ORIGIN
from smoke_advanced_course import command
from bounded_browser import run_browser

PRIVATE = Path('/home/crauser/cra-eighteen-access')
ACCESS = Path('/home/crauser/cra-classroom-access')
STATE = PRIVATE / 'state.json'
REPORT = ROOT / 'reports/local/class-capacity-03/new-students.json'
NAMES = ['elev15', 'elev16', 'elev17', 'elev18']


def atomic(path, value):
    assert not path.is_symlink()
    temp = path.with_name(path.name + '.pending-' + uuid4().hex)
    with temp.open('x') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temp.chmod(0o600)
    temp.replace(path)


def save(state):
    atomic(STATE, json.dumps(state, indent=2) + '\n')


def control(code, payload):
    compile(code, 'scoped-student-operation', 'exec')
    result = runtime.ssh('cra-control', 'cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c ' + shlex.quote(code), input=json.dumps(payload), timeout=120)
    if result.returncode:
        raise RuntimeError('Scoped student operation failed; private diagnostic output withheld')
    return json.loads(result.stdout)


def no_test_writer():
    assert runtime.NODE == Path('/home/crauser/cra-m0-02'), 'Set CRA_NODE_HOME to the existing runtime'
    assert not any((runtime.SECRETS / name).exists() for name in ('acceptance-solo-nodes.json', 'acceptance-teaching.json'))
    assert not list(runtime.STATE.glob('cra-acceptance-*.json'))


def preserve(mode):
    assert mode in ('before', 'after')
    code = '''import json,sys,os
from pathlib import Path
from cra.db import connect
b=json.load(sys.stdin);p=Path('/var/lib/cra/class-capacity-03-preservation.json')
with connect() as db:
 db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 assert not db.execute("SELECT id FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()
 data={}
 for table in ['notebooks','team_notebooks','team_findings','score_ledger','team_score_ledger','lab_completions','objective_attempts','lab_flags','mission_releases','lab_participants','teams','team_members','users','memberships']:
  data[table]=sorted(json.dumps(dict(r),sort_keys=True,default=str) for r in db.execute('SELECT * FROM '+table))
 data['labs']=sorted(json.dumps(dict(r),sort_keys=True,default=str) for r in db.execute("SELECT id,organization_id,user_id,team_id,mission_id,mission_version,generation,state,desired_state,slot,node_id,desktop_enabled FROM labs WHERE state<>'DESTROYED'"))
 if b['mode']=='before' and not p.exists():
  from uuid import uuid4
  temp=p.with_name(p.name+'.'+uuid4().hex)
  with os.fdopen(os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
   json.dump({'purpose':'CLASS-CAPACITY-03','operation_id':b['operation_id'],'data':data},f);f.flush();os.fsync(f.fileno())
  os.link(temp,p);temp.unlink()
 else:
  assert p.is_file() and not p.is_symlink()
  saved=json.loads(p.read_text());assert saved['purpose']=='CLASS-CAPACITY-03' and saved['operation_id']==b['operation_id'];old=saved['data']
  for table,rows in old.items():assert set(rows)<=set(data[table])
print('true')
'''
    assert control(code, {'mode': mode, 'operation_id': json.loads(STATE.read_text())['operation_id']})


def prepare():
    no_test_writer()
    assert runtime.virsh('list', '--name').stdout.split() == ['cra-control']
    assert not n2.inventory()['defined']
    source = ACCESS / 'credentials.json'
    assert not source.is_symlink() and source.stat().st_mode & 0o077 == 0
    original = json.loads(source.read_text())
    numbers = sorted(int(a['username'][4:]) for a in original['accounts'] if re.fullmatch(r'elev\d+', a['username']))
    assert numbers == list(range(1, 15)) and len(original['accounts']) == 16
    assert sum(a['role'] == 'teacher' for a in original['accounts']) == 1
    assert sum(a['username'] == 'testelev' and a['role'] == 'student' for a in original['accounts']) == 1
    names = ('credentials.json', 'url.txt', 'CRA-adgang-og-login.html', 'CRA-adgang-og-login.pdf', 'render-access.py')
    if STATE.exists():
        state = json.loads(STATE.read_text())
        assert state['purpose'] == 'CLASS-CAPACITY-03' and state['phase'] in ('preparing', 'prepared')
        assert state['original'] == original
    else:
        originals = runtime.virsh('list', '--all', '--name').stdout.split()
        disks = {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for name in originals if name != 'cra-control' for p in (runtime.STATE / 'vms' / name).glob('*.qcow2')}
        state = {'purpose': 'CLASS-CAPACITY-03', 'operation_id': uuid4().hex, 'phase': 'preparing', 'organization_id': original['organization_id'], 'original': original, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'source_files': {name: hashlib.sha256((ACCESS / name).read_bytes()).hexdigest() for name in names}, 'original_domains': originals, 'original_disks': disks,
                 'new_accounts': [{'id': str(uuid4()), 'username': name, 'password': secrets.token_urlsafe(20), 'role': 'student'} for name in NAMES], 'request_keys': {name: str(uuid4()) for name in NAMES}, 'labs': {}}
        save(state)
    for name in names:
        p = ACCESS / name
        assert p.is_file() and not p.is_symlink()
        data = p.read_bytes()
        assert hashlib.sha256(data).hexdigest() == state['source_files'][name]
        backup = PRIVATE / ('before-' + name)
        if not backup.exists():
            temp = PRIVATE / ('backup-' + uuid4().hex)
            with temp.open('xb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            temp.chmod(0o600)
            os.link(temp, backup); temp.unlink()
        assert not backup.is_symlink() and backup.read_bytes() == data
    preserve('before')
    state['phase'] = 'prepared'
    save(state)
    print('PASS: four new identities prepared privately; originals backed up; no database changes')


VERIFY_OLD = '''
for account in b['original']['accounts']:
 row=db.execute('SELECT u.password_hash,u.active,m.role FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.id=%s AND u.username=%s AND m.organization_id=%s',(account['id'],account['username'],b['organization_id'])).fetchone()
 assert row and row['active'] and row['role']==account['role']
 assert PASSWORDS.verify(row['password_hash'],account['password'])
'''


def backup_database(state):
    # A unique operation-owned directory permits recovery without replacing a dump.
    code = '''import fcntl,hashlib,json,os,subprocess,sys
from pathlib import Path
from uuid import uuid4
b=json.load(sys.stdin);assert b['purpose']=='CLASS-CAPACITY-03' and len(b['operation_id'])==32
int(b['operation_id'],16)
p=Path('/var/lib/cra-completion-backup')/('class-capacity-03-'+b['operation_id'])
p.mkdir(mode=0o700,exist_ok=True);assert p.is_dir() and not p.is_symlink() and p.stat().st_uid==0 and p.stat().st_mode&0o077==0
owner={'purpose':b['purpose'],'operation_id':b['operation_id']};marker=p/'owner.json'
if not marker.exists():
 assert all(q.name.startswith('owner-') and q.name.endswith('.tmp') and q.is_file() and not q.is_symlink() for q in p.iterdir())
 temp=p/('owner-'+uuid4().hex+'.tmp')
 with os.fdopen(os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
  json.dump(owner,f);f.flush();os.fsync(f.fileno())
 os.link(temp,marker);temp.unlink()
assert not marker.is_symlink() and json.loads(marker.read_text())==owner
lock=(p/'backup.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
final=p/'before.dump'
if not final.exists():
 temp=p/('before-'+uuid4().hex+'.partial.dump')
 with temp.open('xb') as f:
  temp.chmod(0o600)
  subprocess.run(['runuser','-u','postgres','--','pg_dump','-Fc','cra'],stdout=f,stderr=subprocess.DEVNULL,check=True)
  f.flush();os.fsync(f.fileno())
 subprocess.run(['pg_restore','--file=/dev/null',str(temp)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
 os.link(temp,final);temp.unlink()
assert not final.is_symlink()
subprocess.run(['pg_restore','--file=/dev/null',str(final)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
print(json.dumps({'directory':str(p),'sha256':hashlib.sha256(final.read_bytes()).hexdigest()}))
'''
    compile(code, 'private-backup-operation', 'exec')
    result = runtime.ssh('cra-control', 'sudo python3 -c ' + shlex.quote(code), input=json.dumps({'purpose': state['purpose'], 'operation_id': state['operation_id']}), timeout=120)
    assert result.returncode == 0, 'Private database backup not confirmed'
    return json.loads(result.stdout)


def provision():
    no_test_writer()
    state = json.loads(STATE.read_text())
    assert state['purpose'] == 'CLASS-CAPACITY-03'
    assert [a['username'] for a in state['new_accounts']] == NAMES
    assert hashlib.sha256((ACCESS / 'credentials.json').read_bytes()).hexdigest() == state['source_sha256']
    if state['phase'] == 'prepared':
        preserve('after')
        assert runtime.virsh('list', '--name').stdout.split() == ['cra-control'] and not n2.inventory()['defined']
        control("import json,sys\nfrom cra.auth import PASSWORDS\nfrom cra.db import connect\nb=json.load(sys.stdin)\nwith connect() as db:\n" + ''.join(' ' + line + '\n' for line in VERIFY_OLD.strip().splitlines()) + " assert not db.execute(\"SELECT id FROM jobs WHERE status IN ('QUEUED','RUNNING')\").fetchone()\n assert not db.execute('SELECT id FROM users WHERE username=ANY(%s)',([a['username'] for a in b['new_accounts']],)).fetchone()\nprint('true')", state)
        state['database_backup'] = backup_database(state)
        state['phase'] = 'backed-up'
        save(state)
    if state['phase'] == 'backed-up':
        code = '''import json,sys
from cra.auth import PASSWORDS
from cra.db import connect
b=json.load(sys.stdin)
assert b['purpose']=='CLASS-CAPACITY-03' and [a['username'] for a in b['new_accounts']]==['elev15','elev16','elev17','elev18']
with connect() as db:
''' + ''.join(' ' + line + '\n' for line in VERIFY_OLD.strip().splitlines()) + ''' for a in b['new_accounts']:
  row=db.execute('SELECT id,password_hash,active FROM users WHERE username=%s',(a['username'],)).fetchone()
  if row:
   assert str(row['id'])==a['id'] and row['active'] and PASSWORDS.verify(row['password_hash'],a['password'])
   assert db.execute("SELECT 1 FROM memberships WHERE user_id=%s AND organization_id=%s AND role='student'",(a['id'],b['organization_id'])).fetchone()
  else:
   db.execute('INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)',(a['id'],a['username'],PASSWORDS.hash(a['password'])))
   db.execute("INSERT INTO memberships VALUES(%s,%s,'student')",(a['id'],b['organization_id']))
print('true')
'''
        assert control(code, state)
        state['phase'] = 'accounts-created'
        save(state)
    assert state['phase'] in ('accounts-created', 'labs-ready', 'checked')
    pending = []
    for account in state['new_accounts']:
        client = Client(account)
        labs = [lab for lab in client.request('/api/v1/labs') if lab['state'] != 'DESTROYED']
        assert len(labs) <= 1
        if not labs:
            op = client.request('/api/v1/labs', {'mission_id': 'first-files', 'mission_version': 1}, key=state['request_keys'][account['username']], expected=202)
            state['labs'][account['username']] = op['lab_id']
            save(state)
            lab = client.request('/api/v1/labs/' + op['lab_id'])
        else:
            lab = labs[0]
            assert lab['id'] == state['labs'].get(account['username'], lab['id'])
        state['labs'][account['username']] = lab['id']
        save(state)
        pending.append((account, client, lab))
    # Both workers may provision their own node concurrently; never bypass admission.
    for account, client, lab in pending:
        deadline = time.monotonic() + 900
        while lab['state'] not in ('READY', 'ERROR', 'STOPPED'):
            assert time.monotonic() < deadline
            time.sleep(2)
            lab = client.request('/api/v1/labs/' + lab['id'])
        assert lab['state'] == 'READY' and lab['mission_id'] == 'first-files' and lab['generation'] == 1 and not lab['desktop_enabled']
        state['labs'][account['username']] = lab['id']
        save(state)
        print('PASS: new student terminal environment ready', flush=True)
    state['phase'] = 'labs-ready'
    save(state)


def check():
    no_test_writer()
    state = json.loads(STATE.read_text())
    assert state['phase'] in ('labs-ready', 'checked')
    checked = []
    for account in state['new_accounts']:
        client = Client(account)
        lab = client.request('/api/v1/labs/' + state['labs'][account['username']])
        assert lab['state'] == 'READY' and lab['generation'] == 1 and not lab['desktop_enabled']
        identity = n2.binding(lab['id'])
        prefix = 'cra-' + lab['id'].replace('-', '')[:10] + '-1'
        for suffix, memory in (('o', 1024), ('t', 768)):
            name = prefix + suffix
            if identity == 2:
                inspection = n2.inspect_vm(lab['id'], name)
                manifest = inspection['manifest']
                assert inspection['kvm'] == {'enabled': True, 'present': True}
            else:
                manifest = json.loads((runtime.STATE / 'vms' / name / 'manifest.json').read_text())
                assert runtime.kvm_status(name) == {'enabled': True, 'present': True}
            assert manifest['ownership']['lab_id'] == lab['id'] and manifest['memory_mib'] == memory and not manifest['egress']
            assert manifest['node_id'] == identity and manifest['ownership']['user_id'] == account['id'] and manifest['ownership']['organization_id'] == state['organization_id']
        text = "test -s /home/cra/academy/README.txt && grep -q NL-104 /home/cra/academy/journal/events.log && curl --fail --max-time 5 -s http://" + lab['target_ip'] + ":8080/ >/dev/null && printf 'NEW_STUDENT_%s\\n' VERIFIED"
        assert 'NEW_STUDENT_VERIFIED' not in text
        assert 'NEW_STUDENT_VERIFIED' in command(client, lab, text, 'NEW_STUDENT_DONE')
        assert client.request('/api/v1/labs/' + lab['id'] + '/progress')['total_points'] == 0
        checked.append({'node_id': identity, 'generation': 1, 'state': 'READY'})
    teacher = next(a for a in state['original']['accounts'] if a['role'] == 'teacher')
    roster = Client(teacher).request('/api/v1/command-center/students')
    assert not roster['truncated']
    assert sorted(int(a['username'][4:]) for a in roster['students'] if re.fullmatch(r'elev\d+', a['username'])) == list(range(1, 19))
    assert sum(a['username'] == 'testelev' for a in roster['students']) == 1
    assert {a['id'] for a in state['new_accounts']} <= {a['user_id'] for a in roster['students']}
    run_browser('browser_new_students.py', {'url': ORIGIN, 'accounts': state['new_accounts'], 'labs': state['labs']}, timeout=240)
    for name in state['original_domains']:
        assert runtime.virsh('domstate', name).stdout.strip() == ('running' if name == 'cra-control' else 'shut off')
    assert all(Path(name).exists() and [Path(name).stat().st_size, Path(name).stat().st_mtime_ns] == value for name, value in state['original_disks'].items())
    result = runtime.ssh('cra-control', 'cd /opt/cra/backend && sudo -u craapp env PYTHONPATH=. .venv/bin/python /opt/cra-completion-test/preservation.py after')
    assert result.returncode == 0
    preserve('after')
    assert hashlib.sha256((ACCESS / 'credentials.json').read_bytes()).hexdigest() == state['source_sha256']
    state['phase'] = 'checked'
    save(state)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'task': 'CLASS-CAPACITY-03', 'status': 'PASS', 'new_accounts': 4, 'normal_student_accounts': 18, 'separate_test_account': True, 'new_labs': checked, 'old_labs_still_stopped': True, 'original_data_preserved': True, 'new_progress_untouched': True}, indent=2) + '\n')
    print('PASS: four new actual terminals and browser logins; existing records and stopped disks preserved')


if __name__ == '__main__':
    os.umask(0o077)
    try:
        assert PRIVATE.is_dir() and not PRIVATE.is_symlink() and PRIVATE.stat().st_mode & 0o077 == 0
        lock = (PRIVATE / 'operation.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parser = argparse.ArgumentParser()
        parser.add_argument('phase', choices=('prepare', 'provision', 'check'))
        globals()[parser.parse_args().phase]()
    except Exception as error:
        print('FAIL:', type(error).__name__, 'private state retained; no original account reset')
        sys.exit(1)
