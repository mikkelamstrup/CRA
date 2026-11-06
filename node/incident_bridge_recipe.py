"""Four isolated, scaffolded incident exercises using real HTTP and restricted SSH."""
import json
from pathlib import Path

import course_recipes
import lab_tools

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / 'content/labs/incident-bridge'
STAGES = {'incident-http': 9, 'incident-evidence': 10,
          'incident-containment': 11, 'incident-ownership': 12}


def pages():
    return {'index.html': '<!doctype html><meta charset="utf-8"><h1>Cybercon · Træningssag</h1>'
            '<p>Læs academy/incident-bridge/README.txt. Din kundeportal findes på port 8081.</p>'}


def setup(command, target, operators, links, flag, guest):
    stage = STAGES[command.mission_id]
    if len(operators) != 1 or len(links) != 1:
        raise ValueError('Incident bridge requires one individual operator')
    course_recipes.setup_access(target, operators, links, 'Se din træningssag; ingen beviskode her.', guest)
    put = lambda path, value, mode='0644': course_recipes.root_file(target, path, value, guest, mode)
    guest(target, 'sudo install -d -m 0755 /opt/cybercon /opt/incident-bridge /etc/cybercon /etc/incident-bridge /srv/incident-bridge && '
          'sudo install -d -m 0750 -o www-data -g fieldops /var/log/cybercon && '
          'sudo install -d -m 0750 -o root -g fieldops /srv/cybercon/evidence')
    for filename in ('portal.py', 'attack.py'):
        put('/opt/cybercon/' + filename, (ROOT / 'content/labs/cybercon' / filename).read_text())
    put('/opt/incident-bridge/verify.py', (CONTENT / 'verify.py').read_text())
    put('/etc/cybercon/portal.json', json.dumps({'addresses': ['127.0.0.1', f'192.168.{links[0]["subnet"]}.10']}))
    policy = {'emergency_lockdown': False, 'enforce_report_ownership': stage == 9,
              'expose_support_bundle': stage not in (9, 12),
              'revoked_sessions': ['nt-support-legacy'] if stage in (9, 12) else []}
    put('/etc/cybercon/policy.json', json.dumps(policy, indent=2) + '\n', '0640' if stage in (9, 10) else '0660')
    guest(target, 'sudo chown root:fieldops /etc/cybercon/policy.json && sudo usermod -a -G fieldops www-data')
    put('/etc/incident-bridge/completion.txt', flag + '\n', '0600')
    submission = json.loads((CONTENT / f'stage-{stage}/kontrol.json').read_text())
    put('/srv/incident-bridge/kontrol.json', json.dumps(submission, indent=2) + '\n', '0660')
    guest(target, 'sudo chown root:fieldops /srv/incident-bridge/kontrol.json')
    put('/usr/local/bin/bridge-check', '#!/bin/sh\nexec sudo /usr/bin/python3 -I /opt/incident-bridge/verify.py\n', '0755')
    put('/etc/sudoers.d/cra-incident-bridge',
        'analyst ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/incident-bridge/verify.py\n', '0440')
    guest(target, 'sudo visudo -cf /etc/sudoers.d/cra-incident-bridge')
    for name, script in [('portal', 'portal.py --config /etc/cybercon/portal.json'), ('attack', 'attack.py')]:
        unit = '''[Unit]
Description=CRA incident bridge NAME
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
User=www-data
Group=fieldops
UMask=0027
ExecStart=/usr/bin/python3 -I /opt/cybercon/SCRIPT
Restart=on-failure
RestartSec=1
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/log/cybercon
PrivateTmp=true
MemoryMax=128M
TasksMax=32
[Install]
WantedBy=multi-user.target
'''.replace('NAME', name).replace('SCRIPT', script)
        put('/etc/systemd/system/cybercon-' + name + '.service', unit)
    guest(target, 'sudo systemctl daemon-reload && sudo systemctl enable --now cybercon-portal')
    # The immutable case evidence is actual HTTP traffic, never invented log lines.
    probe = '''import time,urllib.request,urllib.error
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(50):
 try:client.open('http://127.0.0.1:8081/',timeout=2).read();break
 except OSError:time.sleep(.2)
else:raise RuntimeError('Portal did not start')
for path,cookie in [('/support/diagnostics.json',''),('/api/reports?id=8202','nt_session=nt-support-legacy')]:
 try:client.open(urllib.request.Request('http://127.0.0.1:8081'+path,headers={'Cookie':cookie}),timeout=3).read()
 except urllib.error.HTTPError as e:e.read()
'''
    import shlex
    guest(target, 'python3 -c ' + shlex.quote(probe))
    guest(target, 'sudo cp /var/log/cybercon/access.jsonl /srv/cybercon/evidence/initial-access.jsonl && '
          'sudo chown root:fieldops /srv/cybercon/evidence/initial-access.jsonl && '
          'sudo chmod 0640 /srv/cybercon/evidence/initial-access.jsonl')
    # Learner requests must be newer than the frozen case evidence.
    guest(target, 'sudo python3 -c ' + shlex.quote(
        "import json;from datetime import datetime,timezone;"
        f"p={{'stage':{stage},'started_at':datetime.now(timezone.utc).isoformat()}};"
        "open('/etc/incident-bridge/config.json','w').write(json.dumps(p))"))
    guest(target, 'sudo chmod 0600 /etc/incident-bridge/config.json && sudo systemctl enable --now cybercon-attack')
    for filename in ('README.txt', 'mandat.txt', 'rapport.md', 'kontrol.json'):
        lab_tools.upload(operators[0], '/home/cra/academy/incident-bridge/' + filename,
                         (CONTENT / f'stage-{stage}' / filename).read_bytes())
    guest(operators[0], "sed 's/Host nordlys/Host nordtele/' /home/cra/academy/access/ssh_config > "
          '/home/cra/academy/incident-bridge/ssh_config && chmod 0600 /home/cra/academy/incident-bridge/ssh_config')


def check(command, target, operators, links, guest):
    guest(target, 'sudo systemctl is-active --quiet cybercon-portal cybercon-attack')
    for name in operators:
        if guest(name, 'ssh -F /home/cra/academy/incident-bridge/ssh_config nordtele id -un').strip() != 'analyst':
            raise RuntimeError('Incident bridge restricted SSH unavailable')
