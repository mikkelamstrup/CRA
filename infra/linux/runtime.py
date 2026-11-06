"""Project-local, unprivileged libvirt/QEMU runtime. No host service changes."""
from __future__ import annotations
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
NODE = Path(os.environ.get('CRA_NODE_HOME',str(ROOT))).resolve()
TC = NODE / '.local/toolchain/root'
LV = NODE / '.local/libvirt'
STATE = NODE / '.runtime'
SECRETS = NODE / '.secrets'
QEMU_NS = 'http://libvirt.org/schemas/domain/qemu/1.0'
ET.register_namespace('qemu', QEMU_NS)


def node_id():
    value=os.environ.get('CRA_NODE_ID','1')
    if value not in ('1','2'):raise ValueError('Invalid runtime node identity')
    return int(value)


def require_node(data):
    value=data.get('node_id',1)
    if type(value) is not int or value!=node_id():raise ValueError('Runtime node identity mismatch')



def env():
    e = os.environ.copy()
    e.update({'LD_LIBRARY_PATH': str(LV/'lib64')+':'+str(TC/'usr/lib/x86_64-linux-gnu'),
              'LIBVIRT_DRIVER_DIR': str(LV/'lib64/libvirt/connection-driver'),
              'XDG_CONFIG_HOME': str(STATE/'config'), 'XDG_RUNTIME_DIR': str(STATE/'run'),
              'XDG_CACHE_HOME': str(STATE/'cache'),
              'PATH': ':'.join(map(str, [STATE/'bin', TC/'usr/bin', TC/'usr/sbin'])) + ':' + e['PATH']})
    return e


def run(argv, *, check=True, timeout=60, input=None):
    return subprocess.run(list(map(str, argv)), env=env(), input=input, text=True,
                          capture_output=True, timeout=timeout, check=check)


def virsh(*args, check=True, timeout=60):
    return run([LV/'bin/virsh', '-c', 'qemu:///session', *args], check=check, timeout=timeout)


def validate_name(name):
    if not re.fullmatch(r'cra-[a-z0-9-]{1,28}', name):
        raise ValueError('Invalid project domain name')
    return name


def socket_ready(path):
    if not path.exists() and not path.is_symlink():return False
    info=path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid():
        raise RuntimeError('Unexpected runtime socket resource')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        try:probe.connect(str(path))
        except ConnectionRefusedError:
            path.unlink()  # Only the confirmed stale socket in the private project runtime.
            return False
    return True


def start_daemons():
    os.umask(0o077)
    for sub in ['bin', 'config/libvirt', 'run', 'cache', 'vms']:
        (STATE/sub).mkdir(parents=True, exist_ok=True)
    (STATE/'run').chmod(0o700)
    for filename in ['landlock.py','unix_guard.py','qemu_sandbox.py']:
        shutil.copyfile(ROOT/'infra/linux'/filename,STATE/'bin'/filename)
    wrapper = STATE/'bin/qemu-system-x86_64'
    wrapper.write_text('#!/usr/bin/python3\nimport sys\n'
        + 'sys.argv.insert(1,'+repr(str(NODE))+')\n'
        + 'from qemu_sandbox import main\nmain()\n')
    wrapper.chmod(0o755)
    for name in ['virtlogd', 'libvirtd']:
        sock = STATE/'run/libvirt'/('libvirt-sock' if name == 'libvirtd' else 'virtlogd-sock')
        if socket_ready(sock):
            continue
        conf = STATE/'config/libvirt'/f'{name}.conf'
        conf.write_text('')
        run([LV/'sbin'/name, '--daemon', '--config', conf,
             '--pid-file', STATE/f'{name}.pid'])
    virsh('list', '--all')


def keypair(path):
    if not path.exists():
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', path])
    path.chmod(0o600)
    return path.read_text(), path.with_suffix('.pub').read_text().strip()


def lab_links(lab):
    if lab is None:return []
    if not isinstance(lab,dict) or set(lab) not in ({'port','role','subnet'},{'port','role','subnet','peers'}):
        raise ValueError('Invalid fixed lab topology')
    if lab['role'] not in ('operator','target'):raise ValueError('Invalid lab role')
    peers=lab.get('peers',[])
    if not isinstance(peers,list) or len(peers)>3 or (peers and lab['role']!='target'):
        raise ValueError('Only a target may declare up to three additional operator links')
    links=[{'port':lab['port'],'subnet':lab['subnet']},*peers]
    for link in links:
        if not isinstance(link,dict) or set(link)!={'port','subnet'} or type(link['port']) is not int or type(link['subnet']) is not int or not 23000<=link['port']<=23999 or not 70<=link['subnet']<=199:
            raise ValueError('Invalid isolated link')
    if len({l['port'] for l in links})!=len(links) or len({l['subnet'] for l in links})!=len(links):
        raise ValueError('Duplicate lab link')
    return links


def prepare(name, port, memory=1024, *, egress=False, lab=None, ownership=None):
    os.umask(0o077)
    validate_name(name)
    if ownership is not None:
        if set(ownership) != {'lab_id','organization_id','user_id','generation'}:
            raise ValueError('Invalid lab ownership fields')
        for field in ('lab_id','organization_id','user_id'):
            if str(uuid.UUID(ownership[field])) != ownership[field]:
                raise ValueError('Invalid ownership UUID')
        if type(ownership['generation']) is not int or not 1 <= ownership['generation'] <= 10000:
            raise ValueError('Invalid ownership generation')
    if egress and name != 'cra-control':
        raise ValueError('Egress is reserved for trusted control provisioning')
    if lab is not None:
        lab_links(lab)
        if egress:raise ValueError('Lab interfaces cannot have egress')
    if not 20000 <= port <= 60000:
        raise ValueError('Port out of project range')
    if memory not in (768, 1024, 2048, 4096):
        raise ValueError('Unapproved memory allocation')
    available=int(re.search(r'^MemAvailable:\s+(\d+)',Path('/proc/meminfo').read_text(),re.M).group(1))*1024
    if available < (memory+4096)*1024**2:
        raise RuntimeError('Insufficient memory: preserve 4 GiB host reserve')
    if shutil.disk_usage(NODE).free < 20*1024**3:
        raise RuntimeError('Insufficient disk: preserve 20 GiB reserve')
    lock = json.loads((ROOT/'infra/image.lock.json').read_text())
    if lock['architecture'] != 'x86_64':
        raise ValueError('Architecture mismatch')
    base = NODE/'.local/images'/lock['filename']
    with base.open('rb') as image:
        if hashlib.file_digest(image,'sha256').hexdigest() != lock['sha256']:
            raise ValueError('Base image checksum mismatch')
    vm = STATE/'vms'/name
    if vm.exists() and (vm/'pending.json').exists() and not (vm/'manifest.json').exists():
        recover_pending(name)
    if vm.exists():
        raise ValueError('VM already prepared; explicit lifecycle required')
    if (SECRETS/name).exists():
        raise ValueError('Existing secrets require explicit recovery')
    with socket.socket() as s:
        s.bind(('127.0.0.1', port))
    created=[]
    definition_attempted=False
    try:
        vm.mkdir(parents=True, mode=0o700)
        created.append(vm)
        secrets = SECRETS/name
        secrets.mkdir(parents=True, mode=0o700, exist_ok=False)
        created.append(secrets)
        SECRETS.chmod(0o700)
        pending=json.dumps({'project':'cra','node_id':node_id(),'name':name,'nonce':uuid.uuid4().hex,'ownership':ownership})
        (vm/'pending.json').write_text(pending)
        (secrets/'pending.json').write_text(pending)
        _, public = keypair(secrets/'client')
        host_private, host_public = keypair(secrets/'host')
        known_hosts = secrets/'known_hosts'
        known_hosts.write_text(f'[127.0.0.1]:{port} {host_public}\n')
        known_hosts.chmod(0o600)
        cfg = {
            'hostname': name, 'manage_etc_hosts': True,
            'users': [{'name': 'cra', 'shell': '/bin/bash', 'lock_passwd': True,
                       'sudo': 'ALL=(ALL) NOPASSWD:ALL', 'ssh_authorized_keys': [public]}],
            'ssh_pwauth': False, 'disable_root': True,
            'ssh_keys': {'ed25519_private': host_private, 'ed25519_public': host_public},
            'package_update': False, 'package_upgrade': False,
            'write_files': [{'path':'/etc/ssh/sshd_config.d/10-cra.conf', 'content':
                'AllowAgentForwarding no\nAllowTcpForwarding no\nX11Forwarding no\nPermitTunnel no\nPasswordAuthentication no\n'}],
        }
        if lab:
            suffix=20 if lab['role']=='operator' else 10
            links=lab_links(lab)
            macs=['52:54:'+':'.join(f'{x:02x}' for x in hashlib.sha256((name if index==0 else name+':'+str(index)).encode()).digest()[:4]) for index in range(len(links))]
            ethernets={'management':{'match':{'macaddress':'52:54:00:12:34:56'},'dhcp4':True,'dhcp6':False,'accept-ra':False}}
            for index,link in enumerate(links):
                ethernets['lab'+str(index)]={'match':{'macaddress':macs[index]},'set-name':'lab'+str(index),'addresses':[f'192.168.{link["subnet"]}.{suffix}/24',f'fd42:{link["subnet"]}::{suffix}/64'],'dhcp4':False,'dhcp6':False,'accept-ra':False,'optional':True}
            net={'network':{'version':2,'ethernets':ethernets}}
            cfg['write_files'].extend([
                {'path':'/etc/netplan/60-cra.yaml','permissions':'0600','content':json.dumps(net)},
                {'path':'/etc/sysctl.d/90-cra.conf','content':'net.ipv4.ip_forward=0\nnet.ipv6.conf.all.forwarding=0\n'},
            ])
            cfg['runcmd']=[['netplan','apply']]
        (secrets/'user-data').write_text('#cloud-config\n'+json.dumps(cfg)+'\n')
        (secrets/'user-data').chmod(0o600)
        (vm/'meta-data').write_text(json.dumps({'instance-id': str(uuid.uuid4()), 'local-hostname': name}))
        run([TC/'usr/bin/qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', base, vm/'disk.qcow2', '12G'])
        seed_args=[]
        if lab:
            (vm/'network-config').write_text(json.dumps(net['network']))
            seed_args=['--network-config',vm/'network-config']
        run([TC/'usr/bin/cloud-localds',*seed_args,secrets/'seed.img', secrets/'user-data', vm/'meta-data'])
        domain = ET.Element('domain', {'type':'kvm'})
        ET.SubElement(domain,'name').text=name
        ET.SubElement(domain,'uuid').text=str(uuid.uuid4())
        meta=ET.SubElement(domain,'metadata')
        ET.SubElement(meta,'{urn:cyber-range-academy:v1}ownership',{'project':'cra','node_id':str(node_id()),'task':'M2-01' if ownership else 'M0-02', **{k:str(v) for k,v in (ownership or {}).items()}})
        ET.SubElement(domain,'memory', {'unit':'MiB'}).text=str(memory)
        ET.SubElement(domain,'vcpu').text='2' if memory==4096 else '1'
        osel=ET.SubElement(domain,'os')
        ET.SubElement(osel,'type',{'arch':'x86_64','machine':'pc-q35-10.2'}).text='hvm'
        ET.SubElement(osel,'loader',{'readonly':'yes','type':'rom'}).text=str(TC/'usr/share/seabios/bios-256k.bin')
        ET.SubElement(osel,'boot',{'dev':'hd'})
        features=ET.SubElement(domain,'features'); ET.SubElement(features,'acpi'); ET.SubElement(features,'apic')
        ET.SubElement(domain,'cpu',{'mode':'host-passthrough'})
        devices=ET.SubElement(domain,'devices')
        ET.SubElement(devices,'emulator').text=str(STATE/'bin/qemu-system-x86_64')
        if lab:
            for index,link in enumerate(lab_links(lab)):
                interface=ET.SubElement(devices,'interface',{'type':'server' if lab['role']=='target' else 'client'})
                ET.SubElement(interface,'source',{'address':'127.0.0.1','port':str(link['port'])})
                ET.SubElement(interface,'model',{'type':'virtio'})
                ET.SubElement(interface,'mac',{'address':macs[index]})
        for path,typ,device,target,bus in [(vm/'disk.qcow2','qcow2','disk','vda','virtio'),(secrets/'seed.img','raw','cdrom','sda','sata')]:
            disk=ET.SubElement(devices,'disk',{'type':'file','device':device})
            ET.SubElement(disk,'driver',{'name':'qemu','type':typ})
            ET.SubElement(disk,'source',{'file':str(path)})
            ET.SubElement(disk,'target',{'dev':target,'bus':bus})
            if device=='cdrom': ET.SubElement(disk,'readonly')
        serial=ET.SubElement(devices,'serial',{'type':'file'})
        ET.SubElement(serial,'source',{'path':str(vm/'serial.log')})
        ET.SubElement(serial,'target',{'port':'0'})
        command=ET.SubElement(domain,f'{{{QEMU_NS}}}commandline')
        for arg in ['-netdev', f'user,id=mgmt,restrict={"off" if egress else "on"},ipv6=off,hostfwd=tcp:127.0.0.1:{port}-:22',
                    '-device','virtio-net-pci,netdev=mgmt,bus=pcie.0,addr=0x1e']:
            ET.SubElement(command,f'{{{QEMU_NS}}}arg',{'value':arg})
        ET.indent(domain)
        (vm/'domain.xml').write_text(ET.tostring(domain,encoding='unicode'))
        if name in virsh('list','--all','--name').stdout.splitlines():
            raise ValueError('Refusing to overwrite an existing domain')
        definition_attempted=True
        virsh('define',vm/'domain.xml')
        (vm/'manifest.json').write_text(json.dumps({'node_id':node_id(),'name':name,'port':port,'memory_mib':memory,'image_sha256':lock['sha256'],'egress':egress,'lab_port':lab['port'] if lab else None,'lab':lab,'ownership':ownership}))
        (vm/'pending.json').unlink()
        (secrets/'pending.json').unlink()
        return vm
    except Exception:
        if not definition_attempted:
            for path in reversed(created): shutil.rmtree(path)
        elif (vm/'pending.json').exists() and not (vm/'manifest.json').exists():
            try: recover_pending(name)
            except Exception: pass  # Keep ownership markers for retry after node recovery.
        raise



def ssh(name, command, *, timeout=30, input=None):
    validate_name(name)
    m=json.loads((STATE/'vms'/name/'manifest.json').read_text())
    secret=SECRETS/name
    return run(['ssh','-F','/dev/null','-i',secret/'client','-p',str(m['port']),
                '-o','IdentityAgent=none','-o','IdentitiesOnly=yes','-o','BatchMode=yes',
                '-o','StrictHostKeyChecking=yes','-o',f'UserKnownHostsFile={secret/"known_hosts"}',
                '-o','ConnectTimeout=3','-o','ClearAllForwardings=yes','cra@127.0.0.1',command],
               timeout=timeout,check=False,input=input)


def wait_ready(name, timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            r=ssh(name,'cloud-init status --wait >/dev/null 2>&1 && printf CRA_READY',timeout=10)
        except subprocess.TimeoutExpired:
            continue
        if r.returncode==0 and r.stdout=='CRA_READY': return
        time.sleep(2)
    raise TimeoutError('Guest did not become ready')


def owned_domain(name, *, timeout=60):
    validate_name(name)
    root=ET.fromstring(virsh('dumpxml',name,timeout=timeout).stdout)
    owner=root.find('metadata/{urn:cyber-range-academy:v1}ownership')
    if owner is None or owner.get('project')!='cra':
        raise ValueError('Domain ownership mismatch')
    if owner.get('node_id','1')!=str(node_id()):raise ValueError('Domain node identity mismatch')
    if root.findtext('devices/emulator')!=str(STATE/'bin/qemu-system-x86_64'):
        raise ValueError('Domain belongs to another runtime')
    return root


def start(name):
    owned_domain(name)
    return virsh('start',name)


def stop(name):
    owned_domain(name)
    if virsh('domstate',name).stdout.strip()!='shut off':
        virsh('destroy',name)


def kvm_status(name):
    owned_domain(name)
    response=json.loads(virsh('qemu-monitor-command',name,'{"execute":"query-kvm"}').stdout)
    return response['return']


def destroy(name):
    owned_domain(name)
    vm=STATE/'vms'/name; secret=SECRETS/name
    manifest=json.loads((vm/'manifest.json').read_text())
    require_node(manifest)
    if manifest['name']!=name or vm.is_symlink() or secret.is_symlink():
        raise ValueError('Storage ownership mismatch')
    stop(name)
    virsh('undefine',name)
    shutil.rmtree(vm)
    shutil.rmtree(secret)


def control_egress(enabled):
    """Only the trusted control VM may fetch its own dependencies."""
    name='cra-control';domain=owned_domain(name)
    stop(name)
    for arg in domain.findall(f'{{{QEMU_NS}}}commandline/{{{QEMU_NS}}}arg'):
        value=arg.get('value','')
        if value.startswith('user,id=mgmt,'):
            value=re.sub(r'restrict=(on|off)',f'restrict={"off" if enabled else "on"}',value)
            arg.set('value',value)
    vm=STATE/'vms'/name
    (vm/'domain.xml').write_text(ET.tostring(domain,encoding='unicode'))
    virsh('define',vm/'domain.xml')
    manifest=json.loads((vm/'manifest.json').read_text());manifest['egress']=bool(enabled)
    (vm/'manifest.json').write_text(json.dumps(manifest))
    start(name);wait_ready(name)


def expose_control_web():
    name='cra-control';domain=owned_domain(name);vm=STATE/'vms'/name
    manifest=json.loads((vm/'manifest.json').read_text())
    if manifest.get('http_port')==28000:return
    with socket.socket() as probe:probe.bind(('127.0.0.1',28000))
    args=[arg for arg in domain.findall(f'{{{QEMU_NS}}}commandline/{{{QEMU_NS}}}arg') if arg.get('value','').startswith('user,id=mgmt,')]
    if len(args)!=1:raise ValueError('Unexpected control management topology')
    stop(name)
    args[0].set('value',args[0].get('value')+',hostfwd=tcp:127.0.0.1:28000-:8000')
    (vm/'domain.xml').write_text(ET.tostring(domain,encoding='unicode'))
    virsh('define',vm/'domain.xml')
    manifest['http_port']=28000;(vm/'manifest.json').write_text(json.dumps(manifest))
    start(name);wait_ready(name)


def recover_pending(name):
    """Remove only matching, unfinished resources after confirming node state."""
    validate_name(name)
    vm=STATE/'vms'/name; secret=SECRETS/name
    if vm.is_symlink() or secret.is_symlink() or (vm/'manifest.json').exists():
        raise ValueError('Refusing to recover completed or redirected resources')
    a=json.loads((vm/'pending.json').read_text());b=json.loads((secret/'pending.json').read_text())
    require_node(a);require_node(b)
    if a!=b or a.get('project')!='cra' or a.get('name')!=name or not a.get('nonce'):
        raise ValueError('Pending resource ownership mismatch')
    names=virsh('list','--all','--name').stdout.splitlines()
    if name in names:
        owned_domain(name)
        if virsh('domstate',name).stdout.strip()!='shut off':
            raise ValueError('Refusing to recover a running domain')
        virsh('undefine',name)
    shutil.rmtree(vm)
    shutil.rmtree(secret)
