"""Acceptance harness failure bookkeeping; synthetic SSH fixtures, no VM claim."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import acceptance_identity

class IdentityCleanup(unittest.TestCase):
    def test_created_scope_registered_before_ambiguous_ssh_failure(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'.secrets').mkdir()
            owned=[]
            with patch.object(acceptance_identity,'NODE',root),patch.object(acceptance_identity,'ssh',return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(RuntimeError):acceptance_identity.provision('isolation-a',on_owned=owned.append)
                self.assertEqual(owned,[root/'.secrets/acceptance-isolation-a.json'])
                self.assertTrue(owned[0].exists())
                self.assertEqual(owned[0].stat().st_mode&0o077,0)
                other=[]
                with self.assertRaises(RuntimeError):acceptance_identity.provision('isolation-a',on_owned=other.append)
                self.assertEqual(other,[])  # Never claim an existing scope without resume.

if __name__=='__main__':unittest.main()
