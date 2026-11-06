"""Individual Cybercon incident: bounded traffic, narrow remediation and private verifier."""
import json
from pathlib import Path

import course_recipes
import lab_tools

ROOT = Path(__file__).resolve().parents[1]
MISSION = 'cybercon-incident'


def pages():
    return {'index.html': '<!doctype html><meta charset="utf-8"><h1>Nordtele · Driftsoverdragelse</h1>'
            '<p>Cybercons sag CC-901. Kundeportalen kører på port 8081. Brug din udleverede '
            'sagsmappe i academy/cybercon og dit mandat. Fiktiv CRA-øvelse.</p>'}


def setup(command, target, operators, links, flag, guest):
    if len(operators) != 1 or len(links) != 1:
        raise ValueError('Cybercon is an individual incident')
    # Reuse restricted lab SSH; never put the completion flag in its readable report.
    course_recipes.setup_access(target, operators, links, 'Cybercon: se din sagsmappe.', guest)
    put = lambda path, text, mode='0644': course_recipes.root_file(target, path, text, guest, mode)
    guest(target, 'sudo install -d -m 0755 /opt/cybercon /etc/cybercon && '
          'sudo install -d -m 0750 -o www-data -g fieldops /var/log/cybercon && '
          'sudo install -d -m 0750 -o root -g fieldops /srv/cybercon/evidence')
    for filename in ('portal.py', 'attack.py', 'verify.py'):
        put('/opt/cybercon/' + filename, (ROOT / 'content/labs/cybercon' / filename).read_text())
    put('/etc/cybercon/portal.json', json.dumps({'addresses': ['127.0.0.1',
        '192.168.' + str(links[0]['subnet']) + '.10']}))
    put('/etc/cybercon/policy.json', json.dumps({
        'emergency_lockdown': False, 'enforce_report_ownership': False,
        'expose_support_bundle': True, 'revoked_sessions': []}, indent=2) + '\n', '0660')
    guest(target, 'sudo chown root:fieldops /etc/cybercon/policy.json && '
          'sudo usermod -a -G fieldops www-data')
    put('/etc/cybercon/completion.txt', flag + '\n', '0600')
    put('/usr/local/bin/cybercon-check', '#!/bin/sh\nexec sudo /usr/bin/python3 -I /opt/cybercon/verify.py\n', '0755')
    put('/etc/sudoers.d/cra-cybercon',
        'analyst ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/cybercon/verify.py\n', '0440')
    guest(target, 'sudo visudo -cf /etc/sudoers.d/cra-cybercon')
    for name, script in [('portal', 'portal.py --config /etc/cybercon/portal.json'), ('attack', 'attack.py')]:
        unit = '''[Unit]
Description=Cybercon fictional incident NAME
After=network-online.target cybercon-portal.service
[Service]
User=www-data
Group=fieldops
UMask=0027
ExecStart=/usr/bin/python3 -I /opt/cybercon/SCRIPT
Restart=on-failure
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
        if name == 'portal':
            unit = unit.replace('After=network-online.target cybercon-portal.service', 'After=network-online.target')
        put('/etc/systemd/system/cybercon-' + name + '.service', unit)
    guest(target, 'sudo systemctl daemon-reload && sudo systemctl enable --now cybercon-portal')
    # Capture genuine successful requests before the analyst arrives, not invented log lines.
    guest(target, "python3 -c \"import time,urllib.request; "
          "time.sleep(1); urllib.request.urlopen('http://127.0.0.1:8081/support/diagnostics.json').read(); "
          "urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8081/api/reports?id=8202',"
          "headers={'Cookie':'nt_session=nt-support-legacy'})).read()\"")
    guest(target, 'sudo cp /var/log/cybercon/access.jsonl /srv/cybercon/evidence/initial-access.jsonl && '
          'sudo chown root:fieldops /srv/cybercon/evidence/initial-access.jsonl && '
          'sudo chmod 0640 /srv/cybercon/evidence/initial-access.jsonl && '
          'sudo systemctl enable --now cybercon-attack')
    for name in operators:
        for filename in ('README.txt', 'mandat.txt', 'driftsoverdragelse.txt', 'rapport.md'):
            lab_tools.upload(name, '/home/cra/academy/cybercon/' + filename,
                             (ROOT / 'content/labs/cybercon' / filename).read_bytes())
        # Give this mission its own named SSH entry using the already pinned host key.
        guest(name, "mkdir -p /home/cra/academy/cybercon && sed 's/Host nordlys/Host nordtele/' "
              '/home/cra/academy/access/ssh_config > /home/cra/academy/cybercon/ssh_config && '
              'chmod 0600 /home/cra/academy/cybercon/ssh_config')


def check(command, target, operators, links, guest):
    for name, link in zip(operators, links):
        guest(name, 'curl --fail --max-time 5 http://192.168.' + str(link['subnet']) + '.10:8080/ >/dev/null')
        if guest(name, 'ssh -F /home/cra/academy/cybercon/ssh_config nordtele id -un').strip() != 'analyst':
            raise RuntimeError('Cybercon SSH readiness failed')
    # A deliberately invalid policy is a learner-recoverable incident, not a broken VM.
    guest(target, 'sudo systemctl is-active --quiet cybercon-portal cybercon-attack')
