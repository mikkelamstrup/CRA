"""Real isolated subprocess/kernel tests; generated Ethernet is explicitly a fixture."""
import fcntl
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'infra/linux'))
from flow_proxy import MAX_FRAME, MAX_QUEUE, header, launch


def packet(port=8080, *, source='192.168.82.20', fragment=0, payload=b'PRIVATE_PAYLOAD'):
    tcp=struct.pack('!HHIIHHHH',12345,port,0,0,5<<12,4096,0,0)+payload
    ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(tcp),1,fragment,64,6,0,socket.inet_aton(source),socket.inet_aton('192.168.82.10'))
    return bytes(12)+b'\x08\x00'+ip+tcp


def framed(data):return struct.pack('!I',len(data))+data


class Link:
    def __enter__(self):
        self.server=socket.socket()
        for port in range(23800,23900,2):
            try:self.server.bind(('127.0.0.1',port));break
            except OSError:continue
        else:raise RuntimeError('No test port')
        self.server.listen(1);self.proc=launch(port+1,port)
        self.target,_=self.server.accept();self.target.settimeout(8)
        self.operator=socket.create_connection(('127.0.0.1',port+1),timeout=8)
        self.buffer=b''
        return self
    def metadata(self, seconds=2):
        end=time.monotonic()+seconds;events=[]
        fd=self.proc.stdout.fileno()
        while time.monotonic()<end:
            if not select.select([fd],[],[],min(.1,end-time.monotonic()))[0]:continue
            chunk=os.read(fd,65536)
            if not chunk:break
            self.buffer+=chunk
            while b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1);events.append(json.loads(line))
        return events
    def __exit__(self,*args):
        self.operator.close();self.target.close();self.server.close()
        try:self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:self.proc.terminate();self.proc.wait(timeout=3)
        self.proc.stdout.close()


def exact(sock,n):
    value=b''
    while len(value)<n:
        data=sock.recv(n-len(value))
        if not data:raise EOFError('Forward ended')
        value+=data
    return value


class FlowTests(unittest.TestCase):
    def test_headers_are_bounded_numeric_and_fragments_have_no_ports(self):
        self.assertEqual(header(packet()),('192.168.82.20','192.168.82.10',6,12345,8080))
        self.assertEqual(header(packet(fragment=0x2000))[-2:],(None,None))
        for i in range(54):self.assertIsNone(header(packet()[:i]))
        base=packet();vlan=base[:12]+b'\x81\x00\x00\x01'+base[12:]
        self.assertEqual(header(vlan),header(base))
        udp=struct.pack('!HHHH',5353,53,8,0)
        ip6=struct.pack('!IHBB16s16s',6<<28,len(udp),17,64,socket.inet_pton(socket.AF_INET6,'fd42:82::20'),socket.inet_pton(socket.AF_INET6,'fd42:82::10'))
        self.assertEqual(header(bytes(12)+b'\x86\xdd'+ip6+udp),('fd42:82::20','fd42:82::10',17,5353,53))
        self.assertIsNone(header(bytes(12)+b'\x86\xdd'+ip6+udp[:4]))

    def test_fragmented_coalesced_bidirectional_no_payload_metadata(self):
        with Link() as link:
            time.sleep(.1)
            targets=[str(p.readlink()) for p in Path(f'/proc/{link.proc.pid}/fd').iterdir()]
            self.assertTrue(all(t.startswith(('socket:','pipe:')) or t in ('/dev/null','anon_inode:[eventpoll]') for t in targets),targets)
            data=framed(packet())
            for piece in (data[:1],data[1:3],data[3:17],data[17:]):link.operator.sendall(piece)
            self.assertEqual(exact(link.target,len(data)),data)
            payload=framed(packet(22))*10
            link.target.sendall(payload);self.assertEqual(exact(link.operator,len(payload)),payload)
            events=link.metadata();flows=[e for e in events if e['kind']=='flow']
            self.assertEqual(sum(e['packets'] for e in flows),11)
            self.assertEqual({e['direction'] for e in flows},{'operator_to_target','target_to_operator'})
            seals={e['window_sequence'] for e in events if e['kind']=='status'}
            self.assertTrue(all(e['window_sequence'] in seals for e in flows))
            self.assertNotIn('PRIVATE',json.dumps(events))
            self.assertLessEqual(max(e.get('max_queue',0) for e in events),MAX_QUEUE)

    def test_oversized_length_rejected_before_body(self):
        for length in (0,MAX_FRAME+1,0xffffffff):
            with Link() as link:
                link.operator.sendall(struct.pack('!I',length));link.proc.wait(timeout=4)
                self.assertNotEqual(link.proc.returncode,0)
                self.assertEqual(link.target.recv(1),b'')

    def test_slow_reader_backpressure_and_full_metadata_pipe(self):
        with Link() as link:
            fcntl.fcntl(link.proc.stdout.fileno(),fcntl.F_SETPIPE_SZ,4096)
            # Larger than socket windows and parser queues; delayed reader must
            # receive the exact original bytes, not merely matching counters.
            data=framed(packet(payload=b'x'*60000))*100
            errors=[]
            def send():
                try:link.operator.sendall(data)
                except Exception as e:errors.append(type(e).__name__)
            thread=threading.Thread(target=send);thread.start();time.sleep(.4)
            self.assertEqual(exact(link.target,len(data)),data);thread.join(5);self.assertFalse(errors);self.assertFalse(thread.is_alive())
            # No metadata consumer while many different flows are emitted.
            for _ in range(4):
                data=b''.join(framed(packet(port)) for port in range(1000,1064))
                link.operator.sendall(data);self.assertEqual(exact(link.target,len(data)),data);time.sleep(1.05)
            events=link.metadata(2.2)
            self.assertGreater(max(e.get('metadata_dropped',0) for e in events),0)
            self.assertLessEqual(max(e.get('max_queue',0) for e in events),MAX_QUEUE)
            self.assertIsNone(link.proc.poll())

    def test_continuous_partial_frames_have_separate_deadlines(self):
        # Explicit fixture runner lowers only the timer, while exercising the
        # real child parser and kernel confinement with preopened TCP FDs.
        with socket.socket() as server, socket.socket() as listener:
            server.bind(('127.0.0.1',0));server.listen(1)
            backend=socket.create_connection(server.getsockname());target,_=server.accept()
            listener.bind(('127.0.0.1',0));listener.listen(1)
            script='import socket,sys,flow_proxy;flow_proxy.STALL_SECONDS=.3;flow_proxy.forward(socket.socket(fileno=int(sys.argv[1])),socket.socket(fileno=int(sys.argv[2])),1)'
            proc=subprocess.Popen([sys.executable,'-c',script,str(listener.fileno()),str(backend.fileno())],pass_fds=(listener.fileno(),backend.fileno()),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'infra/linux')})
            operator=socket.create_connection(listener.getsockname());target.settimeout(3)
            try:
                frame=framed(packet());operator.sendall(frame[:9])
                for _ in range(12):
                    time.sleep(.08);operator.sendall(frame[9:]+frame[:9])
                    self.assertEqual(exact(target,len(frame)),frame)
                self.assertIsNone(proc.poll())
                # Last unfinished frame really does time out.
                proc.wait(timeout=3);self.assertNotEqual(proc.returncode,0)
            finally:
                operator.close();target.close();backend.close()
                if proc.poll() is None:proc.terminate();proc.wait(timeout=3)
                proc.stdout.close()

    def test_metadata_reader_death_releases_waiting_and_connected_links(self):
        with socket.socket() as server:
            for port in range(23900,23998,2):
                try:server.bind(('127.0.0.1',port));break
                except OSError:continue
            else:raise RuntimeError('No waiting test port')
            server.listen(1);proc=launch(port+1,port);target,_=server.accept()
            try:
                self.assertEqual(json.loads(proc.stdout.readline(4097))['state'],'waiting')
                proc.stdout.close();proc.wait(timeout=4)
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);probe.bind(('127.0.0.1',port+1))
            finally:
                target.close()
                if proc.poll() is None:proc.terminate();proc.wait(timeout=3)
                proc.stdout.close()
        with Link() as link:
            link.metadata(.2);link.proc.stdout.close();link.proc.wait(timeout=4)
            self.assertNotEqual(link.proc.returncode,0)

    def test_kernel_denies_new_network_files_and_process_access(self):
        script='''import ctypes,os,socket,sys
from flow_proxy import sandbox
sandbox()
checks=0
for action in [lambda:socket.socket(),lambda:open('/etc/hostname').read(),lambda:os.fork(),lambda:os.execv('/bin/true',['true'])]:
 try:action()
 except OSError:checks+=1
assert checks==4
lib=ctypes.CDLL(None,use_errno=True)
for number,args in [(310,(os.getppid(),0,0,0,0,0)),(302,(os.getppid(),7,0,0)),(203,(os.getppid(),0,0)),(141,(0,os.getppid(),19)),(29,(0,1,0)),(250,(0,0,0,0,0))]:
 assert lib.syscall(number,*args)==-1 and ctypes.get_errno()==1
print('PASS')
'''
        result=subprocess.run([sys.executable,'-c',script],env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'infra/linux')},capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'PASS')


if __name__=='__main__':unittest.main()
