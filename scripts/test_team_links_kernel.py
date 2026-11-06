"""Real kernel check: extra team port grants do not permit a sibling network."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest


class TeamLinkKernel(unittest.TestCase):
    def test_two_declared_ports_allowed_and_third_denied(self):
        sockets=[]
        try:
            for port in range(23550,23590):
                s=socket.socket()
                try:s.bind(('127.0.0.1',port));s.listen()
                except OSError:s.close();continue
                sockets.append(s)
                if len(sockets)==3:break
            self.assertEqual(len(sockets),3)
            ports=[s.getsockname()[1] for s in sockets]
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                for path in ['.local/toolchain/root','.local/images','.runtime/vms/cra-team-unit','.secrets/cra-team-unit']:(root/path).mkdir(parents=True)
                for path in ['.runtime/vms/cra-team-unit/disk.qcow2','.secrets/cra-team-unit/seed.img']:(root/path).write_text('synthetic')
                (root/'.runtime/vms/cra-team-unit/manifest.json').write_text(json.dumps({'name':'cra-team-unit','port':22289,'lab_port':ports[0],'lab':{'role':'target','port':ports[0],'subnet':140,'peers':[{'port':ports[1],'subnet':141}]},'egress':False}))
                code='''import json,socket,sys
from qemu_sandbox import confine
ports=list(map(int,sys.argv[2:]));confine(sys.argv[1],['-name','guest=cra-team-unit'])
for port in ports[:2]:
 with socket.socket() as sock:sock.connect(('127.0.0.1',port))
try:
 with socket.socket() as sock:sock.connect(('127.0.0.1',ports[2]))
except PermissionError:print('PASS')
else:raise RuntimeError('Sibling network reachable')
'''
                env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'infra/linux')}
                r=subprocess.run([sys.executable,'-c',code,str(root),*map(str,ports)],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),'PASS')
        finally:
            for s in sockets:s.close()
