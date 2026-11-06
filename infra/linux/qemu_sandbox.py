"""Restrict each QEMU process to its own disk/seed and declared TCP ports."""
import json
import os
from pathlib import Path
import re
import sys
from landlock import restrict, READ, READ_FILE, EXECUTE, WRITE_FILE, TRUNCATE, IOCTL, ALL_FS
from unix_guard import enforce as guard_unix

def confine(node,args):
    node=Path(node).resolve()
    tc=node/'.local/toolchain/root'
    paths=[(tc,READ|EXECUTE)]
    for path in ['/usr/lib/x86_64-linux-gnu','/usr/lib/locale','/etc/ld.so.cache',
                 '/etc/localtime','/etc/nsswitch.conf','/etc/hosts','/etc/resolv.conf',
                 '/proc/self','/proc/meminfo','/proc/cpuinfo','/proc/sys/vm/overcommit_memory',
                 '/sys/devices/system']:
        p=Path(path)
        if p.exists():paths.append((p,READ|EXECUTE if p.is_dir() else READ_FILE))
    for path in ['/dev/kvm','/dev/null','/dev/zero','/dev/random','/dev/urandom']:
        paths.append((Path(path),READ_FILE|WRITE_FILE|IOCTL))
    ports=[];connections=[];trusted=False
    if '-name' not in args and '-pidfile' in args:
        probe=Path(args[args.index('-pidfile')+1]).parent.resolve()
        if probe.parent!=node/'.runtime/config/libvirt/qemu/lib' or not probe.name.startswith('qmp-'):
            raise ValueError('Unexpected capability probe directory')
        paths.append((probe,ALL_FS & ~IOCTL))
    if '-name' in args:
        namearg=args[args.index('-name')+1]
        match=re.match(r'guest=(cra-[a-z0-9-]{1,28})(?:,|$)',namearg)
        if not match:raise ValueError('Unowned QEMU domain')
        name=match.group(1);vm=node/'.runtime/vms'/name
        manifest=json.loads((vm/'manifest.json').read_text())
        if manifest['name']!=name:raise ValueError('Domain manifest mismatch')
        for i,arg in enumerate(args[:-1]):
            if arg!='-object':continue
            obj=json.loads(args[i+1])
            if obj.get('qom-type')=='secret' and 'file' in obj:
                key=Path(obj['file']).resolve()
                if key.parent.parent!=node/'.runtime/config/libvirt/qemu/lib' or not re.fullmatch(r'domain-[0-9]+-'+re.escape(name),key.parent.name) or key.name!='master-key.aes':
                    raise ValueError('Unexpected QEMU secret path')
                paths.append((key,READ_FILE))
        paths.extend([(vm/'disk.qcow2',READ_FILE|WRITE_FILE|TRUNCATE),
                      (node/'.secrets'/name/'seed.img',READ_FILE),
                      (node/'.local/images',READ)])
        if (vm/'serial.log').exists():paths.append((vm/'serial.log',READ_FILE|WRITE_FILE))
        ports=[manifest['port']]
        if manifest.get('http_port'):
            if name!='cra-control' or manifest['http_port']!=28000:
                raise ValueError('Only fixed control web forwarding is allowed')
            ports.append(28000)
        if manifest.get('lab_port'):
            ports.append(manifest['lab_port']);connections.append(manifest['lab_port'])
        lab=manifest.get('lab')
        if lab and lab.get('peers'):
            peers=lab['peers']
            if lab.get('role')!='target' or not isinstance(peers,list) or len(peers)>3:
                raise ValueError('Invalid target peer links')
            for peer in peers:
                if set(peer)!={'port','subnet'} or type(peer['port']) is not int or not 23000<=peer['port']<=23999 or type(peer['subnet']) is not int or not 70<=peer['subnet']<=199:
                    raise ValueError('Invalid target peer link')
                ports.append(peer['port']);connections.append(peer['port'])
        trusted=name=='cra-control' and manifest['egress'] is True
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
         'LD_LIBRARY_PATH':str(tc/'usr/lib/x86_64-linux-gnu'),
         'QEMU_MODULE_DIR':str(tc/'usr/lib/x86_64-linux-gnu/qemu')}
    if '-name' in args:guard_unix()
    restrict(paths,bind_ports=ports,connect_ports=connections,trusted_egress=trusted)
    return tc,env

def main():
    tc,env=confine(sys.argv[1],sys.argv[2:])
    os.execve(tc/'usr/bin/qemu-system-x86_64',
              ['qemu-system-x86_64','-L',str(tc/'usr/share/qemu'),*sys.argv[2:]],env)

if __name__=='__main__': main()
