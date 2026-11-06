"""Actual host kernel isolation with synthetic files and controlled sockets."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest

class LandlockKernelTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux','Requires Linux Landlock kernel')
    def test_qemu_policy_blocks_pathname_unix_but_keeps_owned_channels(self):
        with tempfile.TemporaryDirectory() as tmp,socket.socket(socket.AF_UNIX) as outside,socket.socket() as tcp:
            root=Path(tmp)
            for folder in ['.local/toolchain/root','.local/images','.runtime/vms/cra-unit','.secrets/cra-unit']:(root/folder).mkdir(parents=True)
            for rel in ['.runtime/vms/cra-unit/disk.qcow2','.secrets/cra-unit/seed.img']:(root/rel).write_text('synthetic')
            outside.bind(str(root/'outside.sock'));outside.listen(2)
            tcp.bind(('127.0.0.1',0));tcp.listen(2);port=tcp.getsockname()[1]
            (root/'.runtime/vms/cra-unit/manifest.json').write_text(json.dumps({'name':'cra-unit','port':22289,'lab_port':port,'egress':False}))
            parent,child=socket.socketpair()
            try:
                code='''
import sys,os,socket,json,ctypes,errno
from pathlib import Path
from qemu_sandbox import confine
p=Path(sys.argv[1]);channel=socket.socket(fileno=int(sys.argv[2]));port=int(sys.argv[3]);libc=ctypes.CDLL(None,use_errno=True)
with socket.socket(socket.AF_UNIX) as s:s.connect(str(p/'outside.sock'))
confine(p,['-name','guest=cra-unit'])
r={'outside_positive_before_policy':True}
try:
 with socket.socket(socket.AF_UNIX) as s:s.connect(str(p/'outside.sock'));r['pathname_denied']=False
except PermissionError:r['pathname_denied']=True
try:socket.socketpair();r['new_pair_denied']=False
except PermissionError:r['new_pair_denied']=True
v=libc.syscall(41,ctypes.c_ulong((1<<32)|1),1,0)
r['upper_bits_cannot_bypass']=v==-1 and ctypes.get_errno()==errno.EPERM
with socket.socket() as s:s.connect(('127.0.0.1',port));r['owned_tcp_allowed']=True
channel.sendall(b'OWNED_MONITOR');r['inherited_monitor_allowed']=True
print(json.dumps(r))
'''
                env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'infra/linux')
                r=subprocess.run([sys.executable,'-c',code,tmp,str(child.fileno()),str(port)],env=env,text=True,capture_output=True,pass_fds=[child.fileno()],timeout=10)
                self.assertEqual(r.returncode,0,r.stderr)
                result=json.loads(r.stdout);self.assertEqual(len(result),6);self.assertTrue(all(result.values()),result)
                parent.settimeout(2);self.assertEqual(parent.recv(64),b'OWNED_MONITOR')
            finally:parent.close();child.close()

    @unittest.skipUnless(sys.platform=='linux','Requires Linux Landlock kernel')
    def test_actual_qemu_policy_denies_sibling_files_and_developer_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for folder in ['.local/toolchain/root','.local/images','.runtime/vms/cra-unit','.secrets/cra-unit','.secrets/cra-sibling','source']:(root/folder).mkdir(parents=True)
            for path in ['.runtime/vms/cra-unit/disk.qcow2','.secrets/cra-unit/seed.img','.secrets/cra-unit/client','.secrets/cra-sibling/seed.img','source/private-test']:(root/path).write_text('synthetic')
            (root/'.runtime/vms/cra-unit/manifest.json').write_text(json.dumps({'name':'cra-unit','port':22289,'egress':False}))
            code='''
import sys,json,os
from pathlib import Path
from qemu_sandbox import confine
p=Path(sys.argv[1]);confine(p,['-name','guest=cra-unit'])
r={}
for rel in ['.secrets/cra-unit/client','.secrets/cra-sibling/seed.img','source/private-test']:
 try:fd=os.open(p/rel,os.O_RDONLY);os.close(fd);r[rel]=False
 except PermissionError:r[rel]=True
fd=os.open(p/'.runtime/vms/cra-unit/disk.qcow2',os.O_RDWR);os.close(fd);r['own_disk']=True
r['own_seed']=(p/'.secrets/cra-unit/seed.img').read_text()=='synthetic'
print(json.dumps(r))
'''
            env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'infra/linux')
            r=subprocess.run([sys.executable,'-c',code,tmp],env=env,text=True,capture_output=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            result=json.loads(r.stdout);self.assertEqual(len(result),5);self.assertTrue(all(result.values()),result)

    @unittest.skipUnless(sys.platform=='linux','Requires Linux Landlock kernel')
    def test_kernel_denies_other_files_ports_and_parent_signal(self):
        with tempfile.TemporaryDirectory() as tmp,socket.socket() as allowed,socket.socket() as denied:
            p=Path(tmp);(p/'allowed').write_text('synthetic');(p/'denied').write_text('synthetic')
            allowed.bind(('127.0.0.1',0));allowed.listen()
            denied.bind(('127.0.0.1',0));denied.listen()
            code='''
import sys,os,socket,json
from pathlib import Path
from landlock import restrict,READ_FILE
p=Path(sys.argv[1]);port=int(sys.argv[2]);other=int(sys.argv[3])
def connect(port):
 with socket.socket() as s:s.connect(('127.0.0.1',port))
restrict([(p/'allowed',READ_FILE)],connect_ports=[port])
result={'allowed_read':(p/'allowed').read_text()=='synthetic'}
for label,action in [('denied_read',lambda:(p/'denied').read_text()),('denied_write',lambda:(p/'allowed').write_text('mutation')),('denied_tcp',lambda:connect(other)),('denied_signal',lambda:os.kill(os.getppid(),0))]:
 try:action();result[label]=False
 except PermissionError:result[label]=True
connect(port);result['allowed_tcp']=True
print(json.dumps(result))
'''
            env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'infra/linux')
            r=subprocess.run([sys.executable,'-c',code,tmp,str(allowed.getsockname()[1]),str(denied.getsockname()[1])],env=env,text=True,capture_output=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            result=json.loads(r.stdout)
            self.assertEqual(len(result),6)
            self.assertTrue(all(result.values()),result)

if __name__=='__main__':unittest.main()
