"""Executed as cra inside its own isolated operator VM; verified offline artifacts."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
import zipfile


def run(args,**kwargs):
    subprocess.run(args,check=True,capture_output=True,**kwargs)


def install():
    existing=[name for name in ['cra-desktop','cra-vnc','cra-x'] if Path('/etc/systemd/system/'+name+'.service').exists()]
    if existing:run(['sudo','systemctl','stop',*existing])
    home=Path.home();lock=json.loads((home/'gui-lock.json').read_text())
    for package in lock['base_packages']:
        version=subprocess.run(['dpkg-query','-W','-f=${Version}',package['name']],capture_output=True,text=True,check=True).stdout
        if version!=package['version']:raise ValueError('Base GUI dependency mismatch')
    folder=home/'gui-packages';folder.mkdir(exist_ok=True)
    expected={p['sha256']+'.deb':p['sha256'] for p in lock['packages']}
    with tarfile.open(home/'gui-packages.tar') as tar:
        if set(tar.getnames())!=set(expected) or any(not m.isfile() for m in tar.getmembers()):raise ValueError('Unexpected desktop archive paths')
        for name,digest in expected.items():
            data=tar.extractfile(name).read()
            if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Package digest mismatch')
            (folder/name).write_bytes(data)
    run(['sudo','env','DEBIAN_FRONTEND=noninteractive','dpkg','--install',*[str(folder/n) for n in expected]],timeout=180)
    audit=subprocess.run(['dpkg','--audit'],check=True,capture_output=True,text=True)
    if audit.stdout.strip():raise RuntimeError('Incomplete GUI package configuration')
    archive=home/'gui-browser.zip'
    if hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()!=lock['chrome']['sha256']:raise ValueError('Browser digest mismatch')
    browser=home/'gui-browser';browser.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if any(Path(n).is_absolute() or '..' in Path(n).parts or not n.startswith('chrome-linux64/') for n in z.namelist()):raise ValueError('Unexpected browser paths')
        z.extractall(browser)
    for name in ['chrome','chrome_crashpad_handler','chrome_sandbox']:(browser/'chrome-linux64'/name).chmod(0o755)
    run(['sudo','install','-d','-m','0755','/opt/cra-desktop'])
    run(['sudo','cp','-a',str(browser/'chrome-linux64'),'/opt/cra-desktop/'])
    run(['sudo','chown','-R','root:root','/opt/cra-desktop/chrome-linux64'])
    run(['sudo','install','-o','root','-g','root','-m','4755','/opt/cra-desktop/chrome-linux64/chrome_sandbox','/opt/cra-desktop/chrome-linux64/chrome-sandbox'])
    run(['sudo','groupadd','-f','wireshark']);run(['sudo','usermod','-a','-G','wireshark','cra'])
    run(['sudo','chgrp','wireshark','/usr/bin/dumpcap']);run(['sudo','chmod','0750','/usr/bin/dumpcap'])
    run(['sudo','setcap','cap_net_raw,cap_net_admin=eip','/usr/bin/dumpcap'])
    auth=home/'.Xauthority'
    if not auth.exists():
        auth.touch(mode=0o600);run(['xauth','-f',str(auth),'add',':0','.',secrets.token_hex(16)])
    config=home/'.config/openbox';config.mkdir(parents=True,exist_ok=True)
    chrome='/opt/cra-desktop/chrome-linux64/chrome --no-first-run --no-default-browser-check --user-data-dir=/home/cra/.config/cra-chrome file:///opt/cra-desktop/welcome.html'
    (config/'rc.xml').write_text('''<?xml version="1.0"?><openbox_config xmlns="http://openbox.org/3.4/rc"><keyboard>
<keybind key="C-A-t"><action name="Execute"><command>xterm</command></action></keybind>
<keybind key="C-A-w"><action name="Execute"><command>wireshark</command></action></keybind>
<keybind key="C-A-b"><action name="Execute"><command>'''+chrome+'''</command></action></keybind>
</keyboard><mouse><context name="Root"><mousebind button="Right" action="Press"><action name="ShowMenu"><menu>root-menu</menu></action></mousebind></context></mouse><menu><file>menu.xml</file></menu></openbox_config>''')
    (config/'menu.xml').write_text('''<?xml version="1.0"?><openbox_menu xmlns="http://openbox.org/3.4/menu"><menu id="root-menu" label="CRA Lab">
<item label="Browser"><action name="Execute"><command>'''+chrome+'''</command></action></item>
<item label="Wireshark"><action name="Execute"><command>wireshark</command></action></item>
<item label="Terminal"><action name="Execute"><command>xterm</command></action></item></menu></openbox_menu>''')
    files={
        '/opt/cra-desktop/welcome.html':'''<!doctype html><meta charset="utf-8"><title>CRA · Grafisk lab</title><style>body{background:#101c22;color:#e6f1f3;font:20px system-ui;padding:8vh 8vw}small{color:#7fddbe}h1{font-size:52px}p{max-width:650px;line-height:1.7}</style><small>CYBER RANGE ACADEMY · DIN EGEN OPERATOR</small><h1>Undersøg dit lab.</h1><p>Denne browser kører inde i din isolerede Linux-maskine. Skriv det tildelte måls adresse i adresselinjen for at undersøge øvelsens webservice.</p><p>Ctrl+Alt+B åbner browseren.<br>Ctrl+Alt+W åbner Wireshark.<br>Ctrl+Alt+T åbner en terminal.</p><p>Wireshark: vælg lab0 for dit øvelsesnet. Du har ingen internetadgang.</p>''',
        '/usr/local/bin/cra-desktop-session':'#!/bin/sh\nfor attempt in $(seq 1 100); do xdotool getdisplaygeometry >/dev/null 2>&1 && break; sleep 0.2; done\nopenbox &\n'+chrome+' &\nwait\n',
        '/etc/systemd/system/cra-x.service':'''[Unit]
Description=CRA isolated operator X display
After=network.target
[Service]
User=cra
Environment=HOME=/home/cra
ExecStart=/usr/bin/Xvfb :0 -screen 0 1280x800x24 -auth /home/cra/.Xauthority -nolisten tcp
Restart=on-failure
[Install]
WantedBy=multi-user.target
''',
        '/etc/systemd/system/cra-desktop.service':'''[Unit]
Description=CRA operator graphical workspace
Requires=cra-x.service
After=cra-x.service
[Service]
User=cra
Environment=HOME=/home/cra DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority
ExecStart=/usr/bin/dbus-run-session /usr/local/bin/cra-desktop-session
Restart=on-failure
[Install]
WantedBy=multi-user.target
''',
        '/etc/systemd/system/cra-vnc.service':'''[Unit]
Description=CRA loopback desktop transport
Requires=cra-x.service
After=cra-x.service
[Service]
User=cra
Environment=HOME=/home/cra DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority
ExecStart=/usr/bin/x11vnc -display :0 -auth /home/cra/.Xauthority -localhost -listen 127.0.0.1 -no6 -noipv6 -rfbport 5900 -forever -shared -nopw -noremote -novncconnect -nosel -noclipboard -nosetclipboard -input KMB -noxdamage
Restart=on-failure
[Install]
WantedBy=multi-user.target
'''}
    for index,(target,value) in enumerate(files.items()):
        source=home/('gui-config-'+str(index));source.write_text(value)
        run(['sudo','install','-m','0755' if target.startswith('/usr/local/bin/') else '0644',str(source),target]);source.unlink()
    run(['sudo','systemctl','daemon-reload']);run(['sudo','systemctl','enable','--now','cra-x','cra-desktop','cra-vnc'])
    for name in ['cra-x','cra-desktop','cra-vnc']:run(['systemctl','is-active','--quiet',name])
    shutil.rmtree(folder);shutil.rmtree(browser);(home/'gui-packages.tar').unlink();archive.unlink()

if __name__=='__main__':install()
