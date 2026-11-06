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
TEST_HOME = Path(os.environ.get('CRA_BRIDGE_TEST_HOME', '/home/crauser/cra-bridge-v1'))
ORIGINAL_HOME = Path('/home/crauser/cra-m0-02')
os.environ['CRA_NODE_HOME'] = str(TEST_HOME)
sys.path[:0] = [str(ROOT / 'infra/linux'), str(ROOT / 'node')]
import runtime
import incident_bridge_recipe
import lab_tools
import course_recipes


def guest(name, command):
    result = runtime.ssh(name, command, timeout=90)
    if result.returncode:
        raise RuntimeError('Synthetic guest command failed: ' + command + '\n' + result.stderr)
    return result.stdout.strip()


def main():
    assert TEST_HOME.parent == Path('/home/crauser') and TEST_HOME.name.startswith('cra-bridge-v')
    assert 'kvm' in subprocess.check_output(['id', '-nG'], text=True).split()
    assert not TEST_HOME.exists(), 'Do not overwrite a previous validation runtime'
    # Check live host declarations before reserving the isolated pair.
    check = subprocess.run([sys.executable, '-c',
        'from memory_budget import running_declarations,total_memory_mib; '
        'assert sum(r["memory_mib"] for r in running_declarations())+1792+4096<=total_memory_mib()'],
        env={**os.environ, 'CRA_NODE_HOME': str(ORIGINAL_HOME),
             'PYTHONPATH': str(ROOT / 'infra/linux') + ':' + str(ROOT / 'node')}, check=False)
    assert check.returncode == 0, 'Preserve live host memory reserve'
    for port in (29451, 29452, 23991):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    TEST_HOME.mkdir(mode=0o700)
    (TEST_HOME / '.local').mkdir()
    for name in ('toolchain', 'libvirt', 'images', 'lab-tools', 'course-tools'):
        (TEST_HOME / '.local' / name).symlink_to(ORIGINAL_HOME / '.local' / name, target_is_directory=True)
    owner = {key: str(uuid4()) for key in ('lab_id', 'organization_id', 'user_id')}
    owner['generation'] = 1
    (TEST_HOME / 'scope.json').write_text(json.dumps({'purpose': 'synthetic-incident-bridge', 'owner': owner}))
    result = {'status': 'FAIL', 'checks': [], 'cleanup_errors': []}
    output = ROOT / 'reports/local/incident-bridge' / (TEST_HOME.name+'-recipe.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    created = []

    def passed(label):
        result['checks'].append(label)
        print('PASS: ' + label, flush=True)

    def create(generation, flag, mission):
        owner['generation'] = generation
        prefix = 'cra-' + owner['lab_id'].replace('-', '')[:10] + '-' + format(generation, 'x')
        op, target = prefix + 'o', prefix + 't'
        for name, port, memory, role in [(target, 29452, 768, 'target'), (op, 29451, 1024, 'operator')]:
            created.append(name)
            runtime.prepare(name, port, memory, lab={'port': 23991, 'role': role, 'subnet': 199}, ownership=owner)
            runtime.start(name)
        for name in (target, op):
            runtime.wait_ready(name)
            assert runtime.kvm_status(name)['enabled']
        for path, data in incident_bridge_recipe.pages().items():
            course_recipes.root_file(target, '/srv/cra-target/' + path, data, guest)
        service = '[Unit]\nDescription=Synthetic recipe landing page\n[Service]\nUser=www-data\nWorkingDirectory=/srv/cra-target\nExecStart=/usr/bin/python3 -m http.server 8080\n[Install]\nWantedBy=multi-user.target\n'
        course_recipes.root_file(target, '/etc/systemd/system/cra-target.service', service, guest)
        guest(target, 'sudo systemctl daemon-reload && sudo systemctl enable --now cra-target')
        incident_bridge_recipe.setup(SimpleNamespace(mission_id=mission), target, [op], [{'subnet': 199}], flag, guest)
        lab_tools.upload(op, '/home/cra/bridge-accept.py', (ROOT/'scripts/solve_incident_bridge.py').read_bytes())
        incident_bridge_recipe.check(None, target, [op], [{'subnet': 199}], guest)
        return op, target

    def analyst(op, command):
        return guest(op, 'ssh -F /home/cra/academy/incident-bridge/ssh_config nordtele ' + shlex.quote(command))

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
        for mission,stage in incident_bridge_recipe.STAGES.items():
            if stage not in {int(n) for n in os.environ.get('CRA_BRIDGE_STAGES','9,10,11,12').split(',')}:continue
            owner['lab_id']=str(uuid4())
            flag='CRA{'+secrets.token_hex(16)+'}'
            op,target=create(1,flag,mission)
            passed('stage '+str(stage)+': real x86_64 KVM pair and restricted SSH ready')
            assert 'IKKE GODKENDT' in analyst(op,'bridge-check || true')
            for command in ['cat /etc/incident-bridge/completion.txt','sudo -n /usr/bin/id',
                            'touch /opt/incident-bridge/verify.py','touch /srv/cybercon/evidence/initial-access.jsonl']:
                assert analyst(op,command+' >/dev/null 2>&1 && echo UNEXPECTED || echo DENIED')=='DENIED'
            if stage in (9,10):
                assert analyst(op,'test -w /etc/cybercon/policy.json && echo UNEXPECTED || echo DENIED')=='DENIED'
            passed('stage '+str(stage)+': incomplete answers rejected; verifier, evidence and flag protected')
            if stage==11:
                rejected=json.loads(guest(op,'python3 /home/cra/bridge-accept.py 11 http://192.168.199.10:8081 --invalid-containment'))
                assert rejected['invalid_policy_rejected']
                change(op,{'emergency_lockdown':False,'enforce_report_ownership':False,'expose_support_bundle':True,'revoked_sessions':[]})
                passed('stage 11: malformed-policy 503 is not accepted as genuine lockdown')
            solved=json.loads(guest(op,'python3 /home/cra/bridge-accept.py '+str(stage)+' http://192.168.199.10:8081'))
            assert solved['flag']==flag
            passed('stage '+str(stage)+': real HTTP, report submission and completion flag PASS')
            if stage==12:
                for name in (op,target):guest(name,'sync');runtime.virsh('shutdown',name)
                import time
                deadline=time.monotonic()+90
                for name in (op,target):
                    while runtime.virsh('domstate',name).stdout.strip()!='shut off':
                        assert time.monotonic()<deadline;time.sleep(1)
                for name in (target,op):runtime.start(name);runtime.wait_ready(name)
                incident_bridge_recipe.check(None,target,[op],[{'subnet':199}],guest)
                assert flag in analyst(op,'bridge-check')
                passed('repaired stop/start preserves report, policy, observations and flag')
            for name in (op,target):remove(name)
            if stage==12:
                new_flag='CRA{'+secrets.token_hex(16)+'}'
                op,target=create(2,new_flag,mission)
                assert 'IKKE GODKENDT' in analyst(op,'bridge-check || true')
                solved=json.loads(guest(op,'python3 /home/cra/bridge-accept.py 12 http://192.168.199.10:8081'))
                assert solved['flag']==new_flag and solved['flag']!=flag
                for name in (op,target):remove(name)
                passed('fresh generation requires new practical work and returns only new flag')
        result['status']='PASS'
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
