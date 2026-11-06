import hashlib
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('install_packages',Path(__file__).resolve().parents[1]/'infra/control/install_packages.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ControlPackageLock(unittest.TestCase):
    def test_hash_mismatch_rejected(self):
        module.verify(b'archive',hashlib.sha256(b'archive').hexdigest())
        with self.assertRaises(RuntimeError):module.verify(b'changed','0'*64)

    def test_plan_rejects_transitive_change_removal_and_unknown_version(self):
        packages=[{'name':'postgresql-16','version':'16.13-1'}]
        module.validate_plan('Inst postgresql-16 (16.13-1 Ubuntu [amd64])',packages)
        module.validate_plan('0 upgraded, 0 newly installed',packages)
        for plan in ['Inst other (1.0 Ubuntu [amd64])','Remv other [1.0]',
                     'Inst postgresql-16 (16.14-1 Ubuntu [amd64])','Inst malformed']:
            with self.subTest(plan=plan),self.assertRaises(RuntimeError):module.validate_plan(plan,packages)
