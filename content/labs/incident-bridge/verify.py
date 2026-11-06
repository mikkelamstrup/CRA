"""Fixed, root-owned completion check. No report-selected paths or executable input."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HOME = Path('/etc/incident-bridge')
SUBMISSION = Path('/srv/incident-bridge/kontrol.json')
BASE = 'http://127.0.0.1:8081'
CLIENT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def bounded_json(path, limit=16384):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError('Invalid submission file')
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise ValueError('Submission too large')
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError('Expected JSON object')
        return value
    finally:
        os.close(fd)


def request(path, cookie='', data=None):
    req = urllib.request.Request(BASE + path, headers={'Cookie': cookie}, data=data)
    try:
        response = CLIENT.open(req, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.code, response.read(16384), response.headers.get('Set-Cookie', '').split(';')[0]


def live_matrix():
    out = {'anonymous': request('/api/reports?id=8201')[0],
           'support': request('/support/diagnostics.json')[0],
           'revoked': request('/api/reports?id=8201', 'nt_session=nt-support-legacy')[0]}
    for short, user, password in [('a', 'kunde-a', 'nordtele-lab-a'), ('b', 'kunde-b', 'nordtele-lab-b')]:
        code, _, cookie = request('/login', data=urllib.parse.urlencode({'username': user, 'password': password}).encode())
        if code != 200 or not cookie:
            raise ValueError('Customer login unavailable')
        for report in ('8201', '8202'):
            code, body, _ = request('/api/reports?id=' + report, cookie)
            out[short + '_' + report] = code
            if code == 200 and json.loads(body).get('id') != report:
                raise ValueError('Unexpected report response')
    return out


def log_rows(path, limit=2_100_000):
    # Fixed root/portal-owned log paths, never chosen by report contents.
    if not path.exists():
        return []
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Log exceeds bound')
    rows = []
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # A concurrent final write can be incomplete; older records stay valid.
        if isinstance(row, dict):
            rows.append(row)
    return rows


def verify(stage, answer, config):
    actual = live_matrix()
    recent = []
    for suffix in ('', '.1', '.2', '.3'):
        recent.extend(log_rows(Path('/var/log/cybercon/access.jsonl' + suffix)))
    recent = [r for r in recent if isinstance(r.get('time'), str) and r['time'] >= config['started_at']
              and r.get('source') not in ('127.0.0.1', '::1')]
    def seen(user, report, status, session=None, after=None):
        return any(r.get('user') == user and r.get('path') == '/api/reports?id=' + report
                   and r.get('status') == status and (session is None or r.get('session') == session)
                   and (after is None or r['time'] > after) for r in recent)
    if stage == 9:
        expected = {'anonymous_status': 401, 'own_status': 200, 'foreign_status': 403,
                    'identity': 'kunde-a', 'own_report_id': '8201'}
        ok = answer == expected and actual['a_8201'] == 200 and actual['a_8202'] == 403 and actual['anonymous'] == 401
        ok = ok and seen(None, '8201', 401) and seen('kunde-a', '8201', 200) and seen('kunde-a', '8202', 403)
    elif stage == 10:
        frozen = log_rows(Path('/srv/cybercon/evidence/initial-access.jsonl'))
        support = next(r for r in frozen if r.get('path') == '/support/diagnostics.json' and r.get('status') == 200)
        report = next(r for r in frozen if r.get('path') == '/api/reports?id=8202' and r.get('status') == 200 and r.get('session') == 'legacy-support')
        expected = {'log_source': 'initial-access.jsonl', 'support_time': support['time'],
                    'report_time': report['time'], 'report_id': '8202', 'session_label': 'legacy-support',
                    'conclusion': 'confirmed_report_response', 'attribution': 'unknown'}
        ok = answer == expected and support['time'] <= report['time'] and report.get('bytes', 0) > 0
        ok = ok and seen('kunde-a', '8202', 200, 'legacy-support')
    elif stage == 11:
        remaining = actual['a_8202']
        expected = {'containment_status': 503, 'revoked_status': 401,
                    'restored_status': 200, 'remaining_cross_status': remaining}
        ok = answer == expected and remaining in (200, 403)
        ok = ok and all(actual[k] == v for k, v in {'support': 404, 'revoked': 401, 'a_8201': 200, 'b_8202': 200}.items())
        checkpoints = [r['time'] for r in recent if r.get('path') == '/api/reports?id=8201'
                       and r.get('status') == 503 and r.get('error') == 'incident_containment']
        ok = ok and any(seen(None, '8201', 401, 'legacy-support', after=t)
                        and seen('kunde-a', '8201', 200, after=t)
                        and seen('kunde-a', '8202', remaining, after=t) for t in checkpoints)
    elif stage == 12:
        expected = {'a_own': 200, 'a_other': 403, 'b_own': 200, 'b_other': 403, 'anonymous': 401}
        ok = answer == expected and all(actual[k] == v for k, v in {'anonymous': 401, 'support': 404, 'revoked': 401,
            'a_8201': 200, 'a_8202': 403, 'b_8201': 403, 'b_8202': 200}.items())
        ok = ok and all(seen(user, report, status) for user, report, status in [
            ('kunde-a', '8202', 200), ('kunde-b', '8201', 200),
            ('kunde-a', '8201', 200), ('kunde-a', '8202', 403),
            ('kunde-b', '8202', 200), ('kunde-b', '8201', 403), (None, '8201', 401)])
        for user, report in [('kunde-a', '8202'), ('kunde-b', '8201')]:
            ok = ok and any(seen(user, report, 403, after=r['time']) for r in recent
                            if r.get('user') == user and r.get('path') == '/api/reports?id=' + report and r.get('status') == 200)
    else:
        raise ValueError('Unknown stage')
    return bool(ok)


def main():
    try:
        if any(subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', name], check=False).returncode
               for name in ('cybercon-portal', 'cybercon-attack')):
            raise ValueError('Exercise services stopped')
        config = bounded_json(HOME / 'config.json')
        if not verify(config['stage'], bounded_json(SUBMISSION), config):
            print('IKKE GODKENDT: Kontrollér kontrol.json, dine faktiske HTTP-forsøg og den aktuelle portaladfærd.')
            return 1
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        print('IKKE GODKENDT: Kontrollér JSON-format, tjenester og portal. Ingen nulstilling er nødvendig.')
        return 1
    print('GODKENDT: Opgavens afgrænsede tekniske kontrol er bestået. Aflever også rapporten til læreren.')
    if config['stage'] == 11:
        print('Begrænsning er ikke en fuld sikkerhedsgodkendelse. Dokumentér den målte resterende ejerskabsfejl.')
    print((HOME / 'completion.txt').read_text().strip())
    return 0


if __name__ == '__main__':
    sys.exit(main())
