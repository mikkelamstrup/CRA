"""Fixed offline teaching recipes. Never interpret browser input as guest commands."""
import shlex
from pathlib import Path

import course_tools
import lab_tools

ROOT=Path(__file__).resolve().parents[1]
DNS_MISSION='web-trail-dns'
ACCESS_MISSION='access-granted'
OBJECTIVES={DNS_MISSION:'find-dns-report', ACCESS_MISSION:'read-authorized-report'}


def root_file(name,path,data,guest,mode='0644'):
    lab_tools.upload(name,'/home/cra/cra-course-upload',data.encode())
    guest(name,'sudo install -D -m '+mode+' /home/cra/cra-course-upload '+shlex.quote(path)+' && rm -- /home/cra/cra-course-upload')


def pages(mission_id,flag):
    if mission_id==DNS_MISSION:
        return {
            'index.html':'<!doctype html><meta charset="utf-8"><h1>Nordlys serviceregister</h1><p>Find DNS-serveren på din mål-IP. Spørg efter A-record for rapport.nordlys.test. Webporten er 8080.</p><p>Efter navneopslaget: læs <a href="/service/">serviceoversigten</a>.</p>',
            'service/index.html':'<!doctype html><meta charset="utf-8"><h1>Service Desk</h1><p>Aktuel hændelse: NL-308. <a href="/rapporter/rapport-308.txt">Læs servicerapporten</a>. Rapporten NL-307 er en tidligere afsluttet sag.</p>',
            'rapporter/rapport-307.txt':'NL-307: planlagt vedligehold afsluttet. Ingen aktuel hændelse.\n',
            'rapporter/rapport-308.txt':'NL-308: navnet rapport.nordlys.test peger på labbets webservice på port8080. Kilde: Nordlys syntetiske serviceregister.\n'+flag+'\n',
        }
    if mission_id==ACCESS_MISSION:
        return {'index.html':'<!doctype html><meta charset="utf-8"><h1>Nordlys adgangsøvelse</h1><p>Rapporten findes kun via autoriseret SSH på port2222. Følg README i din operators academy/access-mappe.</p>'}
    raise ValueError('Unsupported course recipe')


def setup_dns(target,operators,links,guest):
    course_tools.install(target)
    ips=['192.168.'+str(link['subnet'])+'.10' for link in links]
    hosts=''.join(ip+' rapport.nordlys.test\n' for ip in ips)
    root_file(target,'/etc/cra-course/hosts',hosts,guest)
    config='\n'.join(['no-resolv','no-hosts','bind-interfaces','localise-queries',
                     'local=/nordlys.test/','addn-hosts=/etc/cra-course/hosts',
                     'listen-address='+','.join(ips),'no-dhcp-interface=*','user=dnsmasq',
                     'pid-file=/run/cra-course-dns/dnsmasq.pid'])+'\n'
    root_file(target,'/etc/cra-course/dnsmasq.conf',config,guest)
    service='''[Unit]
Description=CRA isolated course DNS (no upstream)
After=network-online.target
[Service]
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=/etc/cra-course/dnsmasq.conf
RuntimeDirectory=cra-course-dns
Restart=on-failure
ProtectHome=true
ProtectSystem=strict
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
'''
    root_file(target,'/etc/systemd/system/cra-course-dns.service',service,guest)
    guest(target,'sudo dnsmasq --test --conf-file=/etc/cra-course/dnsmasq.conf && sudo systemctl daemon-reload && sudo systemctl enable --now cra-course-dns')
    for name in operators:
        course_tools.install(name)


def setup_access(target,operators,links,flag,guest):
    # The new sshd serves only lab interfaces. Management sshd/keys are untouched.
    guest(target,"id analyst >/dev/null 2>&1 || sudo useradd --create-home --shell /bin/bash analyst")
    guest(target,'sudo groupadd -f fieldops && sudo usermod -a -G fieldops analyst && sudo install -d -m 0755 /etc/cra-access /srv/cra-access && sudo install -d -m 0750 -o root -g fieldops /srv/cra-access/reports && sudo install -d -m 0700 /srv/cra-access/admin')
    root_file(target,'/srv/cra-access/reports/rapport-404.txt','NL-404: korrekt rolle kan læse denne rapport. Kilde: syntetisk rettighedsrevision.\n'+flag+'\n',guest,mode='0640')
    guest(target,'sudo chown root:fieldops /srv/cra-access/reports/rapport-404.txt')
    root_file(target,'/srv/cra-access/admin/internal.txt','Kun administratorrollen. Denne afvisning er en del af opgaven.\n',guest,mode='0600')
    public=[]
    for name in operators:
        guest(name,"umask 077; mkdir -p /home/cra/academy/access; test -f /home/cra/academy/access/id_ed25519 || ssh-keygen -q -t ed25519 -N '' -f /home/cra/academy/access/id_ed25519")
        public.append(guest(name,'cat /home/cra/academy/access/id_ed25519.pub').strip())
    root_file(target,'/etc/cra-access/authorized_keys','\n'.join(public)+'\n',guest)
    guest(target,"sudo test -f /etc/cra-access/host_ed25519 || sudo ssh-keygen -q -t ed25519 -N '' -f /etc/cra-access/host_ed25519")
    hostkey=guest(target,'sudo cat /etc/cra-access/host_ed25519.pub').strip()
    ips=['192.168.'+str(link['subnet'])+'.10' for link in links]
    config='\n'.join(['Port 2222',*['ListenAddress '+ip for ip in ips],
        'HostKey /etc/cra-access/host_ed25519','PidFile /run/cra-course-ssh.pid',
        'AuthorizedKeysFile /etc/cra-access/authorized_keys','AllowUsers analyst',
        'PermitRootLogin no','PubkeyAuthentication yes','PasswordAuthentication no',
        'KbdInteractiveAuthentication no','UsePAM yes','DisableForwarding yes',
        'PermitUserEnvironment no','PermitUserRC no','StrictModes yes','LogLevel VERBOSE'])+'\n'
    root_file(target,'/etc/cra-access/sshd_config',config,guest)
    service='''[Unit]
Description=CRA isolated course SSH
After=network-online.target
[Service]
ExecStartPre=/usr/sbin/sshd -t -f /etc/cra-access/sshd_config
ExecStart=/usr/sbin/sshd -D -e -f /etc/cra-access/sshd_config
Restart=on-failure
[Install]
WantedBy=multi-user.target
'''
    root_file(target,'/etc/systemd/system/cra-course-ssh.service',service,guest)
    guest(target,'sudo systemctl daemon-reload && sudo systemctl enable --now cra-course-ssh')
    for name,ip in zip(operators,ips):
        base='/home/cra/academy/access/'
        lab_tools.upload(name,base+'known_hosts',('['+ip+']:2222 '+hostkey+'\n').encode())
        config='Host nordlys\n  HostName '+ip+'\n  Port 2222\n  User analyst\n  IdentityFile '+base+'id_ed25519\n  IdentitiesOnly yes\n  IdentityAgent none\n  UserKnownHostsFile '+base+'known_hosts\n  StrictHostKeyChecking yes\n  BatchMode yes\n  ConnectTimeout 5\n'
        lab_tools.upload(name,base+'ssh_config',config.encode())
        lab_tools.upload(name,base+'README.txt',('Autoriseret adgang kun til '+ip+':2222.\nLog ind med: ssh -F '+base+'ssh_config nordlys\nPå målet: id; ls -ld /srv/cra-access/*; ls -l /srv/cra-access/reports; cat /srv/cra-access/reports/rapport-404.txt\nAdminområdet må ikke ændres. analyst har ingen sudo. exit vender tilbage til din operator. Del ikke id_ed25519.\n').encode())
        guest(name,'chmod 0700 /home/cra/academy/access && chmod 0600 /home/cra/academy/access/id_ed25519 /home/cra/academy/access/ssh_config')


def setup(command,target,operators,links,flag,guest):
    if command.mission_id==DNS_MISSION:setup_dns(target,operators,links,guest)
    elif command.mission_id==ACCESS_MISSION:setup_access(target,operators,links,flag,guest)


def check(command,target,operators,links,guest):
    if command.mission_id==DNS_MISSION:
        for name,link in zip(operators,links):
            ip='192.168.'+str(link['subnet'])+'.10'
            if guest(name,'dig +short +time=2 +tries=1 @'+ip+' rapport.nordlys.test A').strip()!=ip:
                raise RuntimeError('Course DNS readiness failed')
    elif command.mission_id==ACCESS_MISSION:
        for name in operators:
            if guest(name,'ssh -F /home/cra/academy/access/ssh_config nordlys id -un').strip()!='analyst':
                raise RuntimeError('Course SSH readiness failed')
