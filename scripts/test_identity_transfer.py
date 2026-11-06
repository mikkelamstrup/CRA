import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'infra/linux'))
import provision_control

class IdentityTransfer(unittest.TestCase):
    def test_private_local_file_and_no_guest_staging_even_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            previous=os.umask(0)
            try:
                with patch.object(provision_control,'SECRETS',directory),patch.object(provision_control,'ssh') as ssh,patch.object(provision_control,'upload') as upload:
                    ssh.return_value=subprocess.CompletedProcess([],1,'','')
                    with self.assertRaises(RuntimeError):provision_control.provision_identity()
                    private=directory/'solo-login.json'
                    self.assertEqual(private.stat().st_mode & 0o777,0o600)
                    self.assertEqual(ssh.call_args.kwargs['input'],private.read_text())
                    self.assertNotIn(private.read_text(),ssh.call_args.args[1])
                    self.assertIn('--stdin',ssh.call_args.args[1])
                    upload.assert_not_called()
                    self.assertEqual(list(directory.iterdir()),[private])
            finally:os.umask(previous)
