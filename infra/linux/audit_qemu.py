"""Inspect only project QEMU metadata/FD targets; never read disk/key contents."""
import json
import os
from pathlib import Path
import re
import sys
from runtime import ROOT,NODE,STATE,SECRETS,owned_domain

def audit(name):
    owned_domain(name)
    pidfile=STATE/'run/libvirt/qemu/run'/f'{name}.pid';pid=int(pidfile.read_text())
    proc=Path('/proc')/str(pid)
    if (proc/'exe').resolve()!=NODE/'.local/toolchain/root/usr/bin/qemu-system-x86_64':raise ValueError('PID is not project QEMU')
    unix={}
    for line in (proc/'net/unix').read_text().splitlines()[1:]:
        fields=line.split(maxsplit=7);unix[fields[6]]=fields[7] if len(fields)>7 else ''
    records=[]
    for fd in (proc/'fd').iterdir():
        try:dest=os.readlink(fd)
        except FileNotFoundError:continue
        kind='kernel-or-pipe'
        allowed=dest.startswith(('anon_inode:','pipe:','/memfd:')) or dest in ['/dev/null','/dev/kvm','/dev/urandom','/dev/random']
        if dest.startswith('socket:'):
            inode=re.fullmatch(r'socket:\[(\d+)\]',dest).group(1)
            if inode in unix:
                kind='unix';path=Path(unix[inode])
                allowed=path.parent.parent==STATE/'config/libvirt/qemu/lib' and bool(re.fullmatch(r'domain-[0-9]+-'+re.escape(name),path.parent.name)) and path.name=='monitor.sock'
            else:kind='network';allowed=True
        elif dest.startswith('/'):
            kind='file';path=Path(dest.removesuffix(' (deleted)'))
            allowed=allowed or path.is_relative_to(NODE/'.local/images') or path.is_relative_to(STATE/'vms'/name) or path==SECRETS/name/'seed.img' or path==pidfile
            if path.is_relative_to(NODE/'.local/images') or path==SECRETS/name/'seed.img':
                flags=re.search(r'^flags:\s+([0-7]+)',(proc/'fdinfo'/fd.name).read_text(),re.M)
                allowed=allowed and flags is not None and int(flags.group(1),8)&3==0
        records.append({'fd':int(fd.name),'kind':kind,'allowed':allowed})
    fields=dict(line.split(':',1) for line in (proc/'status').read_text().splitlines() if ':' in line)
    environment={item.split(b'=',1)[0].decode() for item in (proc/'environ').read_bytes().split(b'\0') if item}
    checks={'file_and_unix_fds_scoped':all(item['allowed'] for item in records),'no_effective_capabilities':int(fields['CapEff'].strip(),16)==0,
            'no_new_privileges':fields['NoNewPrivs'].strip()=='1','stacked_seccomp':int(fields['Seccomp_filters'])>=2,
            'environment_whitelist':environment=={'PATH','LANG','LC_ALL','LD_LIBRARY_PATH','QEMU_MODULE_DIR'}}
    return {'name':name,'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'descriptors':records}

if __name__=='__main__':
    results=[audit(name) for name in sys.argv[1:]]
    if not results:raise SystemExit('Provide project domain names')
    out=ROOT/'reports/local/m0-03';out.mkdir(parents=True,exist_ok=True)
    (out/'processes.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps([{k:v for k,v in result.items() if k!='descriptors'} for result in results],indent=2))
    if any(result['status']!='PASS' for result in results):raise SystemExit(1)
