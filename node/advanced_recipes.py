"""Day5–7 recipes: genuine captured traffic, an isolated IDOR app and joined evidence."""
import json
from pathlib import Path

import course_recipes
import lab_tools

ROOT=Path(__file__).resolve().parents[1]
OBJECTIVES={'packet-hunter':'find-captured-report','vulnerable-app':'find-authorization-flaw','operation-shadow':'complete-investigation'}


def pages(mission_id,flag):
    if mission_id=='packet-hunter':
        return {'index.html':'<!doctype html><meta charset="utf-8"><h1>Nordlys trafikøvelse</h1><p>Undersøg din operators gemte incident.pcap. Historik og aktuelle svar er forskellige kilder.</p>',
                'evidence/trace-505.txt':'NL-505: intern rapport sendt over ukrypteret HTTP i en syntetisk øvelse. Kilde: det faktisk optagede HTTP-svar.\n'+flag+'\n'}
    if mission_id=='vulnerable-app':
        return {'index.html':'<!doctype html><meta charset="utf-8"><h1>Nordlys appøvelse</h1><p>Den særskilte, bevidst sårbare øvelsesportal kører på port8081 i dit eget lab.</p>'}
    if mission_id=='operation-shadow':
        return {'index.html':'<!doctype html><meta charset="utf-8"><h1>Nordlys serviceoversigt</h1><p>Navneopslag er ét spor. Læs <a href="/service/">driftsnotatet</a> for næste afgrænsede undersøgelse.</p>',
                'service/index.html':'<!doctype html><meta charset="utf-8"><h1>Driftsnotat</h1><p>Den aktuelle rapport NL-404 ligger i det beskyttede rapportarkiv. Rollen analyst har læseadgang via SSH på port2222. Brug din operators academy/access/ssh_config; ingen administratoradgang kræves.</p>'}
    raise ValueError('Unknown advanced recipe')


def setup_app(target,links,flag,guest):
    course_recipes.root_file(target,'/opt/cra-course/training_app.py',(ROOT/'content/labs/course/training_app.py').read_text(),guest)
    config={'addresses':['192.168.'+str(link['subnet'])+'.10' for link in links],'flag':flag}
    course_recipes.root_file(target,'/etc/cra-course/app.json',json.dumps(config),guest,mode='0640')
    guest(target,'sudo chown root:www-data /etc/cra-course/app.json')
    service='''[Unit]
Description=CRA deliberately vulnerable isolated training app
After=network-online.target
[Service]
User=www-data
ExecStart=/usr/bin/python3 /opt/cra-course/training_app.py --config /etc/cra-course/app.json
Restart=on-failure
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
PrivateTmp=true
MemoryMax=128M
TasksMax=32
[Install]
WantedBy=multi-user.target
'''
    course_recipes.root_file(target,'/etc/systemd/system/cra-course-app.service',service,guest)
    guest(target,'sudo systemctl daemon-reload && sudo systemctl enable --now cra-course-app')


def setup_capture(target,operators,links,guest):
    for name,link in zip(operators,links):
        ip='192.168.'+str(link['subnet'])+'.10'
        lab_tools.upload(name,'/home/cra/course-capture.py',(ROOT/'content/labs/course/capture_evidence.py').read_bytes())
        guest(name,'sudo python3 /home/cra/course-capture.py '+ip)
    # The historical report is no longer present in the live HTTP tree.
    guest(target,'sudo rm -- /srv/cra-target/evidence/trace-505.txt')


def setup(command,target,operators,links,flag,guest):
    if command.mission_id=='packet-hunter':setup_capture(target,operators,links,guest)
    elif command.mission_id=='vulnerable-app':setup_app(target,links,flag,guest)
    elif command.mission_id=='operation-shadow':
        course_recipes.setup_dns(target,operators,links,guest)
        course_recipes.setup_access(target,operators,links,flag,guest)
        for name in operators:
            lab_tools.upload(name,'/home/cra/academy/shadow/README.txt',b'Nordlys / Operation Shadow. Scope: egen operator og eget tildelte maal. Start med journal.txt. Sammenhold fil, DNS, HTTP og autoriseret SSH. Gem kilde og usikkerhed for hvert led.\n')
            lab_tools.upload(name,'/home/cra/academy/shadow/journal.txt',b'Fiktiv haendelsesjournal: Nordlys har flyttet rapporten til den autoriserede analyst-rolle. Servicen findes via rapport.nordlys.test i labbets DNS. Undersoeg HTTP paa port8080 foer du vaelger naeste trin.\n')


def check(command,target,operators,links,guest):
    if command.mission_id=='packet-hunter':
        for name in operators:
            guest(name,'test -s /home/cra/academy/capture/incident.pcap && tcpdump -nn -r /home/cra/academy/capture/incident.pcap >/dev/null')
    elif command.mission_id=='vulnerable-app':
        for name,link in zip(operators,links):
            guest(name,'curl --fail --max-time 5 http://192.168.'+str(link['subnet'])+'.10:8081/ >/dev/null')
    elif command.mission_id=='operation-shadow':
        from types import SimpleNamespace
        for mission in ['web-trail-dns','access-granted']:
            course_recipes.check(SimpleNamespace(mission_id=mission),target,operators,links,guest)
