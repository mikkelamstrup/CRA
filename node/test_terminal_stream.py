"""Actual localhost TLS/SSH/socketpair transport; synthetic keys and SSH process."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
import socket
import ssl
import threading
import time
from uuid import uuid4

import asyncssh
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from test_cleanup import agent
from terminal_stream import TerminalStreamServer
from cra import relay_transport


@pytest.fixture
def certificates(tmp_path):
    ca=ed25519.Ed25519PrivateKey.generate()
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Synthetic transport CA')])
    now=datetime.now(timezone.utc)
    root=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(hours=1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca.public_key()),critical=False).sign(ca,None))
    (tmp_path/'ca.crt').write_bytes(root.public_bytes(serialization.Encoding.PEM))
    for leaf,cn,eku in [('server','cra-node',ExtendedKeyUsageOID.SERVER_AUTH),('relay','cra-terminal',ExtendedKeyUsageOID.CLIENT_AUTH),('control','cra-control',ExtendedKeyUsageOID.CLIENT_AUTH)]:
        key=ed25519.Ed25519PrivateKey.generate()
        builder=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,cn)]))
            .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .add_extension(x509.KeyUsage(True,False,False,False,False,False,False,False,False),critical=True)
            .add_extension(x509.ExtendedKeyUsage([eku]),critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()),critical=False))
        if leaf=='server':builder=builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        (tmp_path/(leaf+'.crt')).write_bytes(builder.sign(ca,None).public_bytes(serialization.Encoding.PEM))
        (tmp_path/(leaf+'.key')).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(tmp_path/'server.crt',tmp_path/'server.key');context.load_verify_locations(tmp_path/'ca.crt');context.verify_mode=ssl.CERT_REQUIRED
    return tmp_path,context


def signed_claims():
    actor=uuid4()
    claims={'node_id':2,'schema_version':1,'lab_id':str(uuid4()),'organization_id':str(uuid4()),'user_id':str(actor),
        'operator_user_id':str(actor),'generation':1,'purpose':'terminal','grant_hash':uuid4().hex*2,'expires_at':int(time.time())+15}
    raw=json.dumps(claims)
    return claims,{'payload':raw,'signature':hmac.new(b'synthetic',raw.encode(),hashlib.sha256).hexdigest()}


def client_context(folder,role='relay'):
    context=ssl.create_default_context(cafile=folder/'ca.crt');context.minimum_version=ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(folder/(role+'.crt'),folder/(role+'.key'))
    return context


def test_real_tls_ssh_socketpair_large_output_disconnect_and_replay(certificates,monkeypatch):
    folder,context=certificates
    class Target:
        id=2;host='127.0.0.1'
        def certificates(self,**kwargs):return folder
    monkeypatch.setattr(relay_transport,'node',lambda identity:Target())
    async def exercise():
        host=asyncssh.generate_private_key('ssh-ed25519');key=asyncssh.generate_private_key('ssh-ed25519')
        class Guest(asyncssh.SSHServer):
            def begin_auth(self,username):return True
            def public_key_auth_supported(self):return True
            def validate_public_key(self,username,pubkey):return username=='cra' and pubkey==key.convert_to_public()
        async def echo(process):
            while data:=await process.stdin.read(16384):
                process.stdout.write(data);await process.stdout.drain()
            process.exit(0)
        guest=await asyncssh.create_server(Guest,'127.0.0.1',0,server_host_keys=[host],process_factory=echo,encoding=None)
        port=guest.get_port();seen=set();lock=threading.Lock()
        def allocate(body):
            expected=hmac.new(b'synthetic',body.payload.encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(body.signature,expected):raise ValueError('signature')
            claims=agent.TerminalClaims.model_validate_json(body.payload)
            if claims.node_id!=2 or not time.time()<claims.expires_at:raise ValueError('claims')
            with lock:
                if claims.grant_hash in seen:raise ValueError('replay')
                seen.add(claims.grant_hash)
            return {'node_id':2,'lab_id':str(claims.lab_id),'organization_id':str(claims.organization_id),'user_id':str(claims.user_id),'operator_user_id':str(claims.operator_user_id),'generation':1,
                'purpose':'terminal','host':'10.0.2.2','port':port,'username':'cra','session':'cra-'+claims.operator_user_id.hex+'-1',
                'private_key':key.export_private_key().decode(),'host_key':host.export_public_key().decode().strip()}
        server=TerminalStreamServer(('127.0.0.1',22291),context,allocate);server.start()
        claims,signed=signed_claims()
        try:
            async with relay_transport.remote_transport(signed) as (allocation,sock):
                known=f"[10.0.2.2]:{port} {allocation['host_key']}\n".encode()
                async with asyncssh.connect('10.0.2.2',port=port,sock=sock,username='cra',client_keys=[key],known_hosts=known,config=None,agent_path=None) as ssh:
                    process=await ssh.create_process('synthetic-echo',encoding=None)
                    data=b'bounded-real-ssh-output\n'*24000
                    async def send():
                        for offset in range(0,len(data),16384):
                            process.stdin.write(data[offset:offset+16384]);await process.stdin.drain()
                        process.stdin.write_eof()
                    task=asyncio.create_task(send())
                    output=await asyncio.wait_for(process.stdout.read(),15)
                    await task
                    assert output==data
            with pytest.raises((ValueError,OSError)):
                async with relay_transport.remote_transport(signed):raise AssertionError('replay accepted')
            # A valid control certificate cannot use the relay endpoint.
            reader,writer=await asyncio.open_connection('127.0.0.1',22291,ssl=client_context(folder,'control'),server_hostname='127.0.0.1')
            writer.write(json.dumps(signed).encode()+b'\n');await writer.drain()
            assert await asyncio.wait_for(reader.read(),3)==b''
            writer.close();await writer.wait_closed()
            # Missing client certificate never reaches allocation.
            before=len(seen)
            no_cert=ssl.create_default_context(cafile=folder/'ca.crt')
            try:
                reader,writer=await asyncio.open_connection('127.0.0.1',22291,ssl=no_cert,server_hostname='127.0.0.1')
                writer.write(b'{}\n');await writer.drain()
                assert await asyncio.wait_for(reader.read(),3)==b''
                writer.close()
                try:await writer.wait_closed()
                except OSError:pass
            except OSError:pass
            assert len(seen)==before
        finally:
            await asyncio.to_thread(server.close)
            guest.close();await guest.wait_closed()
        assert not server.thread.is_alive()
    asyncio.run(exercise())


def test_pre_handshake_limit_releases_slots_after_disconnect(certificates):
    _,context=certificates
    server=TerminalStreamServer(('127.0.0.1',0),context,lambda body:None,limit=1);server.start()
    port=server.socket.getsockname()[1]
    sockets=[]
    try:
        first=socket.create_connection(('127.0.0.1',port));sockets.append(first)
        time.sleep(.1)
        second=socket.create_connection(('127.0.0.1',port));sockets.append(second);second.settimeout(2)
        assert second.recv(1)==b''
        first.close();time.sleep(.1)
        third=socket.create_connection(('127.0.0.1',port));sockets.append(third);third.settimeout(.2)
        with pytest.raises(TimeoutError):third.recv(1)
    finally:
        for item in sockets:item.close()
        server.close()
    assert not server.thread.is_alive()


def test_shutdown_during_bounded_allocation_joins_executor_and_releases_port(certificates):
    folder,context=certificates
    entered=threading.Event();finished=threading.Event()
    def slow_allocate(body):
        entered.set()
        # Production allocation has a five-second DB/virsh budget; model bounded work.
        time.sleep(.3)
        finished.set()
        raise ValueError('Synthetic deadline')
    server=TerminalStreamServer(('127.0.0.1',0),context,slow_allocate);server.start()
    port=server.socket.getsockname()[1]
    with client_context(folder).wrap_socket(socket.socket(),server_hostname='127.0.0.1') as client:
        client.connect(('127.0.0.1',port));_,signed=signed_claims()
        client.sendall(json.dumps(signed).encode()+b'\n')
        assert entered.wait(2)
        before=time.monotonic();server.close()
        assert finished.is_set() and not server.thread.is_alive() and time.monotonic()-before<5
    with socket.socket() as check:
        check.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        check.bind(('127.0.0.1',port));check.listen()
