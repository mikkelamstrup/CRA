"""Journalled production-chain acceptance confined to a new synthetic organization."""
import json
import os
from pathlib import Path
import secrets
import shlex
import sys
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path('/home/crauser/cra-incident-bridge-deployment')
STATE = PRIVATE / 'acceptance.json'
os.umask(0o077)
os.environ['CRA_NODE_HOME'] = '/home/crauser/cra-m0-02'
sys.path[:0] = [str(ROOT/'infra/linux'), str(ROOT/'scripts'), str(ROOT/'node'), '/home/crauser/cra-mission-three-test']
import runtime
import acceptance_client as ac
from private_client import PrivateClient
import node2_acceptance as n2
from incident_bridge_recipe import STAGES
from bounded_browser import run_browser


def save(state):
    p = STATE.with_suffix('.tmp')
    assert not p.is_symlink() and not STATE.is_symlink()
    p.write_text(json.dumps(state, indent=2)); p.replace(STATE)


def control(code, payload=None):
    result = runtime.ssh('cra-control', 'cd /opt/cra/backend && sudo -u craapp .venv/bin/python -c ' + shlex.quote(code), input=json.dumps(payload), timeout=120)
    if result.returncode:
        (PRIVATE/'synthetic-error.txt').write_text(result.stderr)
        raise RuntimeError('Synthetic control failed; private diagnostic retained')
    return json.loads(result.stdout)


def provision(state):
    control('''import json,sys
from cra.auth import PASSWORDS
from cra.db import connect
b=json.load(sys.stdin)
assert b['purpose']=='cra-incident-bridge-synthetic'
with connect() as db:
 old=db.execute('SELECT id FROM organizations WHERE id=%s',(b['organization_id'],)).fetchone()
 if not old:db.execute('INSERT INTO organizations VALUES(%s,%s)',(b['organization_id'],'Synthetic incident bridge acceptance'))
 for role,u in b['users'].items():
  assert role in ('teacher','student') and u['role']==role and u['username'].startswith('cra_test_bridge_')
  old=db.execute('SELECT id,password_hash FROM users WHERE username=%s',(u['username'],)).fetchone()
  if old:
   assert str(old['id'])==u['user_id'] and PASSWORDS.verify(old['password_hash'],u['password'])
   assert db.execute('SELECT 1 FROM memberships WHERE user_id=%s AND organization_id=%s AND role=%s',(u['user_id'],b['organization_id'],role)).fetchone()
  else:
   db.execute('INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)',(u['user_id'],u['username'],PASSWORDS.hash(u['password'])))
   db.execute('INSERT INTO memberships VALUES(%s,%s,%s)',(u['user_id'],b['organization_id'],role))
print('true')
''', state)


def wait(client, job):
    deadline = time.monotonic() + 900
    while True:
        status = client.request('/api/v1/operations/' + job['operation_id'])
        if status['status'] == 'FAILED':
            raise RuntimeError('Synthetic lifecycle failed: ' + str(status.get('error_code')))
        if status['status'] == 'SUCCEEDED':
            return
        assert time.monotonic() < deadline, 'Synthetic job deadline'
        time.sleep(3)


def guest(lab, state, command):
    node = control('''import json,sys
from cra.db import connect
b=json.load(sys.stdin)
with connect() as db:
 r=db.execute('SELECT node_id,organization_id,user_id FROM labs WHERE id=%s',(b['lab_id'],)).fetchone()
 assert str(r['organization_id'])==b['organization_id'] and str(r['user_id'])==b['user_id']
 print(json.dumps(r['node_id']))
''', {'lab_id': lab['id'], 'organization_id': state['organization_id'], 'user_id': state['users']['student']['user_id']})
    name = 'cra-' + lab['id'].replace('-', '')[:10] + '-' + format(lab['generation'], 'x') + 'o'
    if node == 2:
        return n2.guest(lab['id'], name, command), node
    runtime.owned_domain(name)
    result = runtime.ssh(name, command, timeout=120)
    assert result.returncode == 0, 'Synthetic guest operation failed'
    return result.stdout, node


def cleanup_lab(client, lab, state):
    if lab['state'] == 'DESTROYED':
        return
    key = str(uuid5(NAMESPACE_URL, state['organization_id'] + ':destroy:' + lab['id']))
    job = client.request('/api/v1/labs/' + lab['id'] + '/destroy', {}, key=key, expected=202)
    wait(client, job)


def deactivate(state):
    control('''import json,sys
from cra.db import connect
b=json.load(sys.stdin)
with connect() as db:
 assert not db.execute("SELECT 1 FROM labs WHERE organization_id=%s AND state<>'DESTROYED'",(b['organization_id'],)).fetchone()
 for role,u in b['users'].items():
  assert u['username'].startswith('cra_test_bridge_')
  assert db.execute('SELECT 1 FROM memberships WHERE user_id=%s AND organization_id=%s AND role=%s',(u['user_id'],b['organization_id'],role)).fetchone()
  db.execute('UPDATE users SET active=false WHERE id=%s AND username=%s',(u['user_id'],u['username']))
  db.execute('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s',(u['user_id'],))
print('true')
''', state)
    state['completed'] = True
    save(state)


def main():
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    ac.ORIGIN = Path('/home/crauser/cra-classroom-access/url.txt').read_text().strip()
    if STATE.exists():
        state = json.loads(STATE.read_text())
        assert state['purpose'] == 'cra-incident-bridge-synthetic'
    else:
        org = str(uuid4())
        state = {'purpose': 'cra-incident-bridge-synthetic', 'organization_id': org,
                 'users': {role: {'user_id': str(uuid4()), 'username': 'cra_test_bridge_' + uuid4().hex[:12],
                           'password': secrets.token_urlsafe(24), 'role': role} for role in ('teacher', 'student')}, 'stages': {}}
        save(state)
    if state.get('completed'):
        print('PASS: all four installed mission chains already verified; synthetic scope deactivated')
        return
    if len(state['stages']) == 4 and all(s.get('completed') for s in state['stages'].values()) and state.get('browser_passed'):
        remaining = control('''import json,sys
from cra.db import connect
org=json.load(sys.stdin)
with connect() as db:print(json.dumps(db.execute("SELECT count(*) AS n FROM labs WHERE organization_id=%s AND state<>'DESTROYED'",(org,)).fetchone()['n']))
''', state['organization_id'])
        if remaining == 0:
            deactivate(state)
            print('PASS: completed synthetic cleanup finalized without reactivating accounts')
            return
    provision(state)
    teacher = PrivateClient(state['users']['teacher'])
    student = PrivateClient(state['users']['student'])
    assert student.request('/api/v1/auth/me')['organization_id'] == state['organization_id']
    assert len(student.request('/api/v1/assignments')) == 14
    for mission, stage in STAGES.items():
        current = state['stages'].setdefault(mission, {})
        if current.get('completed'):
            for lab in student.request('/api/v1/labs'):
                if lab['id'] == current['lab_id']:
                    cleanup_lab(student, lab, state)
            continue
        teacher.request('/api/v1/command-center/students/' + state['users']['student']['user_id'] + '/releases', {'mission_id': mission, 'mission_version': 1})
        key = str(uuid5(NAMESPACE_URL, state['organization_id'] + ':create:' + mission))
        job = student.request('/api/v1/labs', {'mission_id': mission, 'mission_version': 1}, key=key, expected=202)
        current['lab_id'] = job['lab_id']; save(state)
        wait(student, job)
        lab = student.request('/api/v1/labs/' + job['lab_id'])
        assert lab['state'] == 'READY'
        solver = (ROOT/'scripts/solve_incident_bridge.py').read_text()
        assert '\nCRA_BRIDGE_SOLVER_EOF\n' not in solver
        guest(lab, state, "cat > /home/cra/bridge-accept.py <<'CRA_BRIDGE_SOLVER_EOF'\n" + solver + '\nCRA_BRIDGE_SOLVER_EOF')
        proven, node = guest(lab, state, 'ssh -F /home/cra/academy/incident-bridge/ssh_config nordtele bridge-check || true')
        flags = [line for line in proven.splitlines() if line.startswith('CRA{')]
        if flags:
            found = {'flag': flags[-1]}
        else:
            # A partially completed test may have changed its own disposable policy.
            # Restore only that synthetic lab's initial policy, then repeat real work.
            if stage in (11, 12):
                initial = {'emergency_lockdown': False, 'enforce_report_ownership': False,
                           'expose_support_bundle': stage == 11,
                           'revoked_sessions': [] if stage == 11 else ['nt-support-legacy']}
                restore = "import subprocess;subprocess.run(['ssh','-F','/home/cra/academy/incident-bridge/ssh_config','nordtele','cat > /etc/cybercon/policy.json'],input=" + repr(json.dumps(initial)) + ",text=True,check=True)"
                guest(lab, state, 'python3 -c ' + shlex.quote(restore))
            raw, node = guest(lab, state, 'python3 /home/cra/bridge-accept.py ' + str(stage) + ' http://' + lab['target_ip'] + ':8081')
            found = json.loads(raw)
        if 'solved_flag' in current:
            assert current['solved_flag'] == found['flag'], 'Unexpected synthetic generation change'
        current['solved_flag'] = found['flag']; save(state)
        objective = next(m for m in student.request('/api/v1/missions') if m['id'] == mission)['objectives'][0]
        endpoint = '/api/v1/labs/' + lab['id'] + '/objectives/' + objective['id'] + '/attempts'
        payload = {'generation': lab['generation'], 'flag': found['flag']}
        result = student.request(endpoint, payload)
        assert result['accepted'] and result['points_awarded'] in (0, objective['points'])
        duplicate = student.request(endpoint, payload)
        assert duplicate['accepted'] and duplicate['points_awarded'] == 0
        assert student.request('/api/v1/labs/' + lab['id'] + '/progress')['total_points'] == objective['points']
        if stage == 12 and not state.get('browser_passed'):
            run_browser('browser_incident_bridge_live.py', {**state, 'url': ac.ORIGIN}, timeout=180, evidence_path=PRIVATE/'browser-live.log')
            state['browser_passed'] = True; save(state)
        current.update({'completed': True, 'node_id': node, 'points': objective['points']}); save(state)
        cleanup_lab(student, lab, state)
        print('PASS: installed assignment', stage, 'real VM/HTTP/SSH/flag/points/duplicate and synthetic cleanup', flush=True)
    deactivate(state)
    print('PASS: all four production mission chains and public UI; synthetic VMs removed and accounts deactivated')


if __name__ == '__main__':
    import fcntl
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    lock = (PRIVATE/'acceptance.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    main()
