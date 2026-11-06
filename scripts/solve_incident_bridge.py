"""Synthetic acceptance client, uploaded only to disposable acceptance operators."""
import http.cookiejar
import json
import subprocess
import sys
import urllib.error
import urllib.request

SSH = ['ssh', '-F', '/home/cra/academy/incident-bridge/ssh_config', 'nordtele']


def ssh(command, input=None, check=True):
    result = subprocess.run([*SSH, command], input=input, text=True, capture_output=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError('Synthetic restricted SSH failed')
    return result


def policy(values):
    code = "import json;p='/etc/cybercon/policy.json';d=json.load(open(p));d.update(" + repr(values) + ");open(p,'w').write(json.dumps(d))"
    result = subprocess.run([*SSH, 'python3 -'], input=code, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Synthetic policy edit failed')


def solve(stage, base, invalid_containment=False):
    clients = {}
    def request(path, account=None, legacy=False):
        if account not in clients:
            clients[account] = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        headers = {'Cookie': 'nt_session=nt-support-legacy'} if legacy else {}
        try:
            response = clients[account].open(urllib.request.Request(base + path, headers=headers), timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, response.read(16384)
    def login(user):
        request('/', user)
        import urllib.parse
        data = urllib.parse.urlencode({'username': 'kunde-' + user, 'password': 'nordtele-lab-' + user}).encode()
        with clients[user].open(base + '/login', data=data, timeout=5) as response:
            assert response.code == 200
    if stage == 9:
        anonymous = request('/api/reports?id=8201')[0]
        login('a')
        answer = {'anonymous_status': anonymous, 'own_status': request('/api/reports?id=8201', 'a')[0],
                  'foreign_status': request('/api/reports?id=8202', 'a')[0], 'identity': 'kunde-a', 'own_report_id': '8201'}
    elif stage == 10:
        frozen = [json.loads(line) for line in ssh('cat /srv/cybercon/evidence/initial-access.jsonl').stdout.splitlines()]
        support = next(r for r in frozen if r['path'] == '/support/diagnostics.json' and r['status'] == 200)
        report = next(r for r in frozen if r['path'] == '/api/reports?id=8202' and r['status'] == 200)
        assert request('/support/diagnostics.json')[0] == 200
        assert request('/api/reports?id=8202', legacy=True)[0] == 200
        answer = {'log_source': 'initial-access.jsonl', 'support_time': support['time'], 'report_time': report['time'],
                  'report_id': '8202', 'session_label': 'legacy-support', 'conclusion': 'confirmed_report_response', 'attribution': 'unknown'}
    elif stage == 11:
        assert request('/api/reports?id=8202', legacy=True)[0] == 200
        if invalid_containment:
            ssh('cat > /etc/cybercon/policy.json', '{bad-json')
        else:
            policy({'emergency_lockdown': True})
        containment = request('/api/reports?id=8201')[0]
        if invalid_containment:
            ssh('cat > /etc/cybercon/policy.json', json.dumps({'emergency_lockdown': True,
                'enforce_report_ownership': False, 'expose_support_bundle': True, 'revoked_sessions': []}))
        policy({'expose_support_bundle': False, 'revoked_sessions': ['nt-support-legacy'], 'emergency_lockdown': False})
        revoked = request('/api/reports?id=8201', legacy=True)[0]
        login('a')
        answer = {'containment_status': containment, 'revoked_status': revoked,
                  'restored_status': request('/api/reports?id=8201', 'a')[0],
                  'remaining_cross_status': request('/api/reports?id=8202', 'a')[0]}
    elif stage == 12:
        login('a'); login('b')
        assert request('/api/reports?id=8202', 'a')[0] == 200
        assert request('/api/reports?id=8201', 'b')[0] == 200
        policy({'enforce_report_ownership': True})
        answer = {'a_own': request('/api/reports?id=8201', 'a')[0], 'a_other': request('/api/reports?id=8202', 'a')[0],
                  'b_own': request('/api/reports?id=8202', 'b')[0], 'b_other': request('/api/reports?id=8201', 'b')[0],
                  'anonymous': request('/api/reports?id=8201')[0]}
    else:
        raise ValueError('Unknown stage')
    ssh('cat > /srv/incident-bridge/kontrol.json', json.dumps(answer))
    result = ssh('bridge-check', check=not invalid_containment)
    if invalid_containment:
        assert result.returncode != 0 and 'CRA{' not in result.stdout
        return {'stage': stage, 'invalid_policy_rejected': True}
    flag = next(line for line in result.stdout.splitlines() if line.startswith('CRA{'))
    return {'stage': stage, 'flag': flag, 'answer': answer}


if __name__ == '__main__':
    print(json.dumps(solve(int(sys.argv[1]), sys.argv[2], '--invalid-containment' in sys.argv)))
