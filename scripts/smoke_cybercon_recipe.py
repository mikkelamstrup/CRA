"""Real KVM recipe acceptance in a separate runtime, with no live service/source changes."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import socket
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TEST_HOME = Path(os.environ.get('CRA_CYBERCON_TEST_HOME', '/home/crauser/cra-cc-v3'))
ORIGINAL_HOME = Path('/home/crauser/cra-m0-02')
os.environ['CRA_NODE_HOME'] = str(TEST_HOME)
sys.path[:0] = [str(ROOT / 'infra/linux'), str(ROOT / 'node')]
import runtime
import cybercon_recipe
import course_recipes


def guest(name, command):
    result = runtime.ssh(name, command, timeout=90)
    if result.returncode:
        raise RuntimeError('Synthetic guest command failed: ' + command + '\n' + result.stderr)
    return result.stdout.strip()


def main():
    assert TEST_HOME.parent == Path('/home/crauser') and TEST_HOME.name.startswith('cra-cc-v')
    assert 'kvm' in subprocess.check_output(['id', '-nG'], text=True).split()
    assert not TEST_HOME.exists(), 'Do not overwrite a previous validation runtime'
    # Check live host declarations before reserving the isolated pair.
    check = subprocess.run([sys.executable, '-c',
        'from memory_budget import running_declarations,total_memory_mib; '
        'assert sum(r["memory_mib"] for r in running_declarations())+1792+4096<=total_memory_mib()'],
        env={**os.environ, 'CRA_NODE_HOME': str(ORIGINAL_HOME),
             'PYTHONPATH': str(ROOT / 'infra/linux') + ':' + str(ROOT / 'node')}, check=False)
    assert check.returncode == 0, 'Preserve live host memory reserve'
    for port in (29401, 29402, 23990):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    TEST_HOME.mkdir(mode=0o700)
    (TEST_HOME / '.local').mkdir()
    for name in ('toolchain', 'libvirt', 'images', 'lab-tools', 'course-tools'):
        (TEST_HOME / '.local' / name).symlink_to(ORIGINAL_HOME / '.local' / name, target_is_directory=True)
    owner = {key: str(uuid4()) for key in ('lab_id', 'organization_id', 'user_id')}
    owner['generation'] = 1
    (TEST_HOME / 'scope.json').write_text(json.dumps({'purpose': 'synthetic-cybercon-recipe', 'owner': owner}))
    result = {'status': 'FAIL', 'checks': [], 'cleanup_errors': []}
    output = ROOT / 'reports/local/cybercon/recipe.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    created = []

    def passed(label):
        result['checks'].append(label)
        print('PASS: ' + label, flush=True)

    def create(generation, flag):
        owner['generation'] = generation
        prefix = 'cra-' + owner['lab_id'].replace('-', '')[:10] + '-' + format(generation, 'x')
        op, target = prefix + 'o', prefix + 't'
        for name, port, memory, role in [(target, 29402, 768, 'target'), (op, 29401, 1024, 'operator')]:
            created.append(name)
            runtime.prepare(name, port, memory, lab={'port': 23990, 'role': role, 'subnet': 198}, ownership=owner)
            runtime.start(name)
        for name in (target, op):
            runtime.wait_ready(name)
            assert runtime.kvm_status(name)['enabled']
        for path, data in cybercon_recipe.pages().items():
            course_recipes.root_file(target, '/srv/cra-target/' + path, data, guest)
        service = '[Unit]\nDescription=Synthetic recipe landing page\n[Service]\nUser=www-data\nWorkingDirectory=/srv/cra-target\nExecStart=/usr/bin/python3 -m http.server 8080\n[Install]\nWantedBy=multi-user.target\n'
        course_recipes.root_file(target, '/etc/systemd/system/cra-target.service', service, guest)
        guest(target, 'sudo systemctl daemon-reload && sudo systemctl enable --now cra-target')
        cybercon_recipe.setup(SimpleNamespace(mission_id='cybercon-incident'), target, [op], [{'subnet': 198}], flag, guest)
        cybercon_recipe.check(None, target, [op], [{'subnet': 198}], guest)
        return op, target

    def analyst(op, command):
        return guest(op, 'ssh -F /home/cra/academy/cybercon/ssh_config nordtele ' + shlex.quote(command))

    def change(op, values):
        code = 'import json; p="/etc/cybercon/policy.json"; v=json.load(open(p)); v.update(' + repr(values) + '); open(p,"w").write(json.dumps(v))'
        analyst(op, 'python3 -c ' + shlex.quote(code))

    def remove(name):
        manifest = json.loads((runtime.STATE / 'vms' / name / 'manifest.json').read_text())
        assert manifest['ownership']['lab_id'] == owner['lab_id']
        runtime.destroy(name)
        created.remove(name)

    try:
        runtime.start_daemons()
        flag = 'CRA{' + secrets.token_hex(16) + '}'
        op, target = create(1, flag)
        passed('real x86_64 KVM pair; production recipe and pinned SSH ready')
        assert 'IKKE GODKENDT' in analyst(op, 'cybercon-check || true')
        initial = analyst(op, 'cat /srv/cybercon/evidence/initial-access.jsonl')
        assert '8202' in initial and '"status": 200' in initial
        initial_hash = hashlib.sha256(initial.encode()).hexdigest()
        passed('genuine historical HTTP evidence and incomplete repair rejected')
        for command in ['cat /etc/cybercon/completion.txt', 'cat /etc/cybercon/../cybercon/completion.txt',
                        'sudo -n /usr/bin/id', 'sudo -n /usr/bin/python3 -I /opt/cybercon/verify.py extra',
                        'touch /opt/cybercon/verify.py', 'touch /srv/cybercon/evidence/initial-access.jsonl']:
            out = analyst(op, command + ' >/dev/null 2>&1 && echo UNEXPECTED || echo DENIED')
            assert out == 'DENIED'
        passed('analyst cannot read flag, alter verifier/evidence or obtain broad sudo')
        change(op, {'emergency_lockdown': True})
        assert 'IKKE GODKENDT' in analyst(op, 'cybercon-check || true')
        change(op, {'emergency_lockdown': False, 'enforce_report_ownership': True,
                    'expose_support_bundle': False, 'revoked_sessions': ['nt-support-legacy']})
        checked = analyst(op, 'cybercon-check')
        assert flag in checked
        assert hashlib.sha256(analyst(op, 'cat /srv/cybercon/evidence/initial-access.jsonl').encode()).hexdigest() == initial_hash
        passed('real repair passes positive and negative HTTP checks; evidence unchanged')
        # Graceful shutdown/restart preserves state, keys, evidence and policies.
        for name in (op, target):
            runtime.virsh('shutdown', name)
        import time
        deadline = time.monotonic() + 60
        for name in (op, target):
            while runtime.virsh('domstate', name).stdout.strip() != 'shut off':
                if time.monotonic() > deadline:
                    raise RuntimeError('Synthetic shutdown deadline')
                time.sleep(1)
        for name in (target, op):
            runtime.start(name); runtime.wait_ready(name)
        cybercon_recipe.check(None, target, [op], [{'subnet': 198}], guest)
        assert flag in analyst(op, 'cybercon-check')
        passed('repaired stop/start preserves policy and resumes both services')
        for name in (op, target):
            remove(name)
        new_flag = 'CRA{' + secrets.token_hex(16) + '}'
        op, target = create(2, new_flag)
        assert 'IKKE GODKENDT' in analyst(op, 'cybercon-check || true')
        change(op, {'enforce_report_ownership': True, 'expose_support_bundle': False,
                    'revoked_sessions': ['nt-support-legacy']})
        checked = analyst(op, 'cybercon-check')
        assert new_flag in checked and flag not in checked
        passed('fresh generation restores vulnerable state and releases only new completion token')
        result['status'] = 'PASS'
    except subprocess.CalledProcessError as error:
        result['failure'] = error.stderr
        print(error.stderr, flush=True)
        raise
    finally:
        for name in list(reversed(created)):
            try:
                if (runtime.STATE / 'vms' / name / 'manifest.json').exists():
                    remove(name)
                else:
                    runtime.recover_pending(name)
            except Exception as error:
                result['cleanup_errors'].append(type(error).__name__ + ': ' + name)
        for daemon in ('libvirtd', 'virtlogd'):
            try:
                pidfile = runtime.STATE / (daemon + '.pid')
                if pidfile.exists():
                    pid = int(pidfile.read_text())
                    cmd = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
                    assert str(TEST_HOME).encode() in cmd and daemon.encode() in cmd
                    handle = os.pidfd_open(pid)
                    signal.pidfd_send_signal(handle, signal.SIGTERM)
                    os.close(handle)
            except ProcessLookupError:
                pass
            except Exception as error:
                result['cleanup_errors'].append(type(error).__name__ + ': ' + daemon)
        output.write_text(json.dumps(result, indent=2) + '\n')
        if result['cleanup_errors']:
            raise RuntimeError('Synthetic cleanup incomplete; inspect recipe.json')


if __name__ == '__main__':
    main()
