"""Actual local HTTP before/after, including containment that must not pass as repair."""
import importlib.util
import json
from pathlib import Path
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('cybercon_' + name, ROOT / 'content/labs/cybercon' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


portal, attack, verifier = [module(name) for name in ('portal', 'attack', 'verify')]


@pytest.fixture
def live_portal(tmp_path):
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps(portal.DEFAULT_POLICY))
    server = portal.Portal(('127.0.0.1', 0), policy, tmp_path / 'access.jsonl')
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), policy, tmp_path / 'access.jsonl'
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


def test_contain_investigate_repair_restore(live_portal):
    base, file, log = live_portal
    assert [r['status'] for r in attack.attempt(base)] == [200, 200]
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert events[-1]['user'] == 'kunde-a'
    assert events[-1]['path'] == '/api/reports?id=8202'
    assert len(verifier.verify(base)) == 4
    policy = {**portal.DEFAULT_POLICY, 'emergency_lockdown': True}
    file.write_text(json.dumps(policy))
    assert [r['status'] for r in attack.attempt(base)] == [503, 503]
    assert verifier.verify(base), 'A shut portal must not pass'
    policy.update(emergency_lockdown=False, enforce_report_ownership=True, expose_support_bundle=False)
    file.write_text(json.dumps(policy))
    assert [r['status'] for r in attack.attempt(base)] == [404, 403]
    assert len(verifier.verify(base)) == 1, 'The stolen session must be revoked as well'
    policy['revoked_sessions'] = [portal.STOLEN_SESSION]
    file.write_text(json.dumps(policy))
    assert [r['status'] for r in attack.attempt(base)] == [404, 401]
    assert verifier.verify(base) == []
    file.write_text('{broken')
    assert all(r['status'] == 503 for r in attack.attempt(base))
    assert verifier.verify(base), 'Malformed policy must not award completion'
    file.write_text(json.dumps(policy))
    assert verifier.verify(base) == []


def test_missing_ownership_control_is_not_hidden_by_revocation(live_portal):
    base, file, _ = live_portal
    file.write_text(json.dumps({**portal.DEFAULT_POLICY, 'expose_support_bundle': False,
                               'revoked_sessions': [portal.STOLEN_SESSION]}))
    failures = verifier.verify(base)
    assert len(failures) == 2
    assert all('anden kundes rapport' in f for f in failures)


def test_both_listeners_keep_one_timeline_through_rotation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    policy, log = tmp_path / 'policy.json', tmp_path / 'access.jsonl'
    policy.write_text(json.dumps(portal.DEFAULT_POLICY))
    shared = ({portal.STOLEN_SESSION: 'kunde-a'}, threading.Lock())
    first = portal.Portal(('127.0.0.1', 0), policy, log, shared)
    second = portal.Portal(('127.0.0.1', 0), policy, log, shared, audit=first.audit)
    first.log_handler.maxBytes = 1000
    first.log_handler.backupCount = 20
    threads = [threading.Thread(target=s.serve_forever) for s in (first, second)]
    for thread in threads:
        thread.start()
    try:
        urls = ['http://127.0.0.1:' + str(s.server_port) for s in (first, second)] * 6
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attack.attempt, urls))
        assert all([r['status'] for r in rows] == [200, 200] for rows in results)
    finally:
        for server in (second, first):
            server.shutdown(); server.server_close()
        for thread in threads:
            thread.join(3)
    entries = [json.loads(line) for path in tmp_path.glob('access.jsonl*') for line in path.read_text().splitlines()]
    assert len(entries) == 24
    assert sum(row['path'] == '/api/reports?id=8202' for row in entries) == 12


def test_new_mission_flag_is_private_and_generation_scoped(resources):
    from test_cleanup import agent
    _, command, _, _, _ = resources
    command.mission_id = 'cybercon-incident'; command.mission_version = 1
    (agent.runtime.SECRETS / 'flags.key').write_bytes(b'synthetic-cybercon'.ljust(32, b'0'))
    flag = agent.flag_value(command)
    assert flag not in ''.join(agent.target_pages(command).values())
    command.generation += 1
    assert agent.flag_value(command) != flag


from test_cleanup import resources  # noqa: E402,F401
