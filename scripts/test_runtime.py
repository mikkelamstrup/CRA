"""Boundary checks; these are not substitutes for the real KVM acceptance."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('runtime',Path(__file__).resolve().parents[1]/'infra/linux/runtime.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)


class RuntimeBoundaries(unittest.TestCase):
    def test_actual_stale_unix_socket_is_recovered_without_touching_live_or_non_socket_paths(self):
        import socket
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'owned.sock'
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
                server.bind(str(path));server.listen(2)
                self.assertTrue(runtime.socket_ready(path))
                accepted,_=server.accept();accepted.close()
            self.assertFalse(runtime.socket_ready(path))
            self.assertFalse(path.exists())
            path.write_text('not a socket')
            with self.assertRaises(RuntimeError):runtime.socket_ready(path)
            self.assertEqual(path.read_text(),'not a socket')
            link=Path(folder)/'redirect.sock';link.symlink_to(path)
            with self.assertRaises(RuntimeError):runtime.socket_ready(link)

    def test_multiple_links_are_target_only_unique_and_bounded(self):
        lab={'port':23640,'subnet':140,'role':'target','peers':[{'port':23641,'subnet':141}]}
        self.assertEqual(len(runtime.lab_links(lab)),2)
        for invalid in [{**lab,'role':'operator'},{**lab,'peers':[{'port':23640,'subnet':141}]},{**lab,'peers':[{'port':23641,'subnet':140}]},{**lab,'peers':[{'port':22,'subnet':141}]},{**lab,'peers':[{'port':23641,'subnet':141,'gateway':'outside'}]},{**lab,'peers':lab['peers']*4}]:
            with self.assertRaises(ValueError):runtime.lab_links(invalid)
        with ExitStack() as stack:
            root=self.fake_node(stack)
            with patch.object(runtime,'virsh',return_value=SimpleNamespace(stdout='')):
                runtime.prepare('cra-unit',22289,lab=lab)
            import xml.etree.ElementTree as ET
            domain=ET.fromstring((root/'.runtime/vms/cra-unit/domain.xml').read_text())
            self.assertEqual(len(domain.findall('devices/interface')),2)
            network=json.loads((root/'.runtime/vms/cra-unit/network-config').read_text())
            self.assertEqual(network['ethernets']['lab1']['addresses'][0],'192.168.141.10/24')

    def fake_node(self, stack):
        root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for folder in ['infra','.local/images']:(root/folder).mkdir(parents=True)
        (root/'.local/images/base.img').write_bytes(b'unit-test-only-base')
        (root/'infra/image.lock.json').write_text(json.dumps({'architecture':'x86_64','filename':'base.img','sha256':hashlib.sha256(b'unit-test-only-base').hexdigest()}))
        for key,value in {'ROOT':root,'NODE':root,'STATE':root/'.runtime','SECRETS':root/'.secrets'}.items():stack.enter_context(patch.object(runtime,key,value))
        stack.enter_context(patch.object(runtime,'keypair',return_value=('synthetic-unit-key','synthetic-unit-public')))
        stack.enter_context(patch.object(runtime,'run',return_value=SimpleNamespace(returncode=0,stdout='')))
        stack.enter_context(patch.object(runtime.shutil,'disk_usage',return_value=SimpleNamespace(free=100*1024**3)))
        return root

    def test_define_failure_removes_only_new_resources_and_allows_retry(self):
        with ExitStack() as stack:
            root=self.fake_node(stack)
            unrelated=root/'.runtime/vms/cra-unrelated';unrelated.mkdir(parents=True)
            def fail_define(*args,**kwargs):
                if args[0]=='define':raise subprocess.CalledProcessError(1,['virsh'])
                return SimpleNamespace(stdout='')
            with patch.object(runtime,'virsh',side_effect=fail_define):
                with self.assertRaises(subprocess.CalledProcessError):runtime.prepare('cra-unit',22289)
            self.assertFalse((root/'.runtime/vms/cra-unit').exists())
            self.assertFalse((root/'.secrets/cra-unit').exists())
            self.assertTrue(unrelated.exists())
            with patch.object(runtime,'virsh',return_value=SimpleNamespace(stdout='')):
                runtime.prepare('cra-unit',22289)
            self.assertTrue((root/'.runtime/vms/cra-unit/manifest.json').exists())

    def test_lost_node_preserves_pending_state_then_recovers_on_retry(self):
        with ExitStack() as stack:
            root=self.fake_node(stack)
            calls=0
            def disconnect(*args,**kwargs):
                nonlocal calls
                calls+=1
                if calls==1:return SimpleNamespace(stdout='')
                raise subprocess.CalledProcessError(1,['virsh'])
            with patch.object(runtime,'virsh',side_effect=disconnect):
                with self.assertRaises(subprocess.CalledProcessError):runtime.prepare('cra-unit',22289)
            vm=root/'.runtime/vms/cra-unit'
            self.assertTrue((vm/'pending.json').exists())
            self.assertFalse((vm/'manifest.json').exists())
            with patch.object(runtime,'virsh',return_value=SimpleNamespace(stdout='')):
                runtime.prepare('cra-unit',22289)
            self.assertTrue((vm/'manifest.json').exists())
            self.assertFalse((vm/'pending.json').exists())

    def test_cleanup_failure_still_writes_fail_report(self):
        spec=importlib.util.spec_from_file_location('smoke',Path(__file__).resolve().parents[1]/'infra/linux/smoke_m0_02.py')
        smoke=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'runtime':runtime}):spec.loader.exec_module(smoke)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);vm=root/'.runtime/vms/cra-smoke';vm.mkdir(parents=True)
            (vm/'manifest.json').write_text('{}')
            with patch.object(smoke,'ROOT',root),patch.object(smoke,'STATE',root/'.runtime'),patch.object(smoke,'start_daemons',side_effect=RuntimeError('node unavailable')),patch.object(smoke,'stop',side_effect=RuntimeError('cleanup unavailable')),redirect_stdout(StringIO()):
                with self.assertRaises(RuntimeError):smoke.main()
            report=json.loads((root/'reports/local/m0-02/smoke.json').read_text())
            self.assertEqual(report['status'],'FAIL')
            self.assertEqual(report['tests'][-1],{'test':'smoke cleanup','status':'FAIL'})

    def test_domain_name_scope(self):
        for name in ['default','../cra-a','cra-a/../../','cra-','cra-'+('a'*29),'cra-a;id']:
            with self.subTest(name=name), self.assertRaises(ValueError): runtime.validate_name(name)
        self.assertEqual(runtime.validate_name('cra-smoke'),'cra-smoke')

    def test_lab_cannot_request_egress(self):
        with self.assertRaises(ValueError): runtime.prepare('cra-smoke',22281,egress=True)

    def test_outside_port_budget(self):
        for port in [22,0,65535]:
            with self.assertRaises(ValueError): runtime.prepare('cra-smoke',port)

    def test_outside_memory_budget(self):
        with self.assertRaises(ValueError): runtime.prepare('cra-smoke',22281,32768)

    def test_owner_required_before_destroy(self):
        with patch.object(runtime,'virsh') as virsh:
            virsh.return_value.stdout='<domain><metadata/></domain>'
            with self.assertRaises(ValueError): runtime.stop('cra-smoke')
            self.assertEqual(virsh.call_args_list[0].args,('dumpxml','cra-smoke'))
            self.assertEqual(virsh.call_count,1)


if __name__=='__main__': unittest.main()
