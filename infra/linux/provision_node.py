"""Own node process and mTLS credentials. No host services or network rules changed."""
import base64
import json
import os
from pathlib import Path
import shlex
import signal
import secrets
import subprocess
import sys
import time

from runtime import ROOT, NODE, STATE, SECRETS, ssh

CERTS=SECRETS/'node-tls'


def openssl(*args):
    subprocess.run(['openssl',*map(str,args)],check=True,capture_output=True)


def certificates(*, server_addresses=('127.0.0.1','10.0.2.2')):
    import ipaddress
    addresses=[str(ipaddress.IPv4Address(value)) for value in server_addresses]
    if not addresses:raise ValueError('Server certificate requires an address')
    os.umask(0o077)
    CERTS.mkdir(mode=0o700,parents=True,exist_ok=True)
    if not (CERTS/'ca.key').exists():
        openssl('genpkey','-algorithm','ED25519','-out',CERTS/'ca.key')
    root_cert=CERTS/'ca.crt'
    valid_root=root_cert.exists() and 'Certificate Sign' in subprocess.run(['openssl','x509','-in',str(root_cert),'-text','-noout'],capture_output=True,text=True,check=True).stdout
    if not valid_root:
        openssl('req','-new','-x509','-key',CERTS/'ca.key','-out',root_cert,'-days','3650','-subj','/CN=CRA Local Node CA','-addext','basicConstraints=critical,CA:TRUE,pathlen:0','-addext','keyUsage=critical,keyCertSign,cRLSign')
    for name,cn,purpose in [('server','cra-node','serverAuth'),('client','cra-control','clientAuth'),('relay','cra-terminal','clientAuth')]:
        if not (CERTS/(name+'.crt')).exists():
            openssl('genpkey','-algorithm','ED25519','-out',CERTS/(name+'.key'))
            openssl('req','-new','-key',CERTS/(name+'.key'),'-out',CERTS/(name+'.csr'),'-subj','/CN='+cn)
            extensions='basicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage='+purpose+'\n'
            if name=='server':extensions+='subjectAltName='+','.join('IP:'+value for value in addresses)+'\n'
            (CERTS/(name+'.ext')).write_text(extensions)
            openssl('x509','-req','-in',CERTS/(name+'.csr'),'-CA',CERTS/'ca.crt','-CAkey',CERTS/'ca.key','-CAcreateserial','-out',CERTS/(name+'.crt'),'-days','365','-extfile',CERTS/(name+'.ext'))
        openssl('x509','-checkend','86400','-noout','-in',CERTS/(name+'.crt'))
    for address in addresses:openssl('x509','-in',CERTS/'server.crt','-noout','-checkip',address)
    openssl('verify','-x509_strict','-CAfile',CERTS/'ca.crt',CERTS/'server.crt',CERTS/'client.crt',CERTS/'relay.crt')


def provision_client():
    result=ssh('cra-control','sudo install -d -m 0700 -o craapp -g craapp /var/lib/cra/node-tls')
    if result.returncode:raise RuntimeError('Cannot prepare control credentials')
    for name in ['ca.crt','client.crt','client.key']:
        code="import os,sys,base64; p="+repr('/var/lib/cra/node-tls/'+name)+"; fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600); os.fchmod(fd,0o600); f=os.fdopen(fd,'wb'); f.write(base64.b64decode(sys.stdin.read())); f.close()"
        result=ssh('cra-control','sudo -u craapp python3 -c '+shlex.quote(code),input=base64.b64encode((CERTS/name).read_bytes()).decode())
        if result.returncode:raise RuntimeError('Control credential transfer failed')


def provision_terminal():
    key=SECRETS/'terminal-signing.key'
    if not key.exists():
        with os.fdopen(os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as file:file.write(secrets.token_bytes(32))
    if key.stat().st_mode & 0o077:raise RuntimeError('Signing key permissions must be private')
    result=ssh('cra-control','id crarelay >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir /var/lib/cra-relay --shell /usr/sbin/nologin crarelay')
    if result.returncode:raise RuntimeError('Cannot prepare relay user')
    result=ssh('cra-control','sudo chmod 0700 /var/lib/cra /var/lib/cra-relay && sudo install -d -m 0700 -o crarelay -g crarelay /var/lib/cra-relay/node-tls')
    if result.returncode:raise RuntimeError('Cannot prepare relay boundary')
    transfers=[('craapp',key,'/var/lib/cra/terminal-signing.key')]
    transfers += [('crarelay',CERTS/name,'/var/lib/cra-relay/node-tls/'+name) for name in ['ca.crt','relay.crt','relay.key']]
    for user,source,target in transfers:
        code="import os,sys,base64; fd=os.open("+repr(target)+",os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600); os.fchmod(fd,0o600); f=os.fdopen(fd,'wb'); f.write(base64.b64decode(sys.stdin.read())); f.close()"
        result=ssh('cra-control','sudo -u '+user+' python3 -c '+shlex.quote(code),input=base64.b64encode(source.read_bytes()).decode())
        if result.returncode:raise RuntimeError('Private terminal material transfer failed')


def provision_flags():
    key=SECRETS/'flags.key'
    if not key.exists():
        with os.fdopen(os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as file:file.write(secrets.token_bytes(32))
    if key.stat().st_mode & 0o077 or key.stat().st_size!=32:raise RuntimeError('Invalid flag key storage')


def start(*,restart=True):
    pidfile=STATE/'node-agent.pid'
    if pidfile.exists():
        pid=int(pidfile.read_text())
        proc=Path('/proc')/str(pid)
        if proc.exists():
            command=(proc/'cmdline').read_bytes().split(b'\0')
            if str(ROOT/'node/agent.py').encode() not in command:raise RuntimeError('Node PID ownership mismatch')
            if not restart:
                print('Project node process already running')
                return
            os.kill(pid,signal.SIGTERM)
            for _ in range(50):
                try:os.waitpid(pid,os.WNOHANG)
                except ChildProcessError:pass
                if not proc.exists():break
                time.sleep(.1)
            if proc.exists():raise RuntimeError('Node did not stop')
    environment=os.environ.copy();environment['CRA_NODE_HOME']=str(NODE)
    with (STATE/'node-agent.log').open('ab') as log:
        process=subprocess.Popen([ROOT/'backend/.venv/bin/python',ROOT/'node/agent.py'],cwd=ROOT,env=environment,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    pidfile.write_text(str(process.pid))
    time.sleep(.5)
    if process.poll() is not None:raise RuntimeError('Node failed to start; inspect project log')
    print('Project node process started on loopback with mutual TLS')


if __name__=='__main__':
    certificates();provision_client();provision_terminal();provision_flags();start()
