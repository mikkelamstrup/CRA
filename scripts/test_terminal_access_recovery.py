"""Synthetic local fault injection only; never touches CRA's database or credentials."""
import hashlib
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import add_terminal_students as accounts
import publish_terminal_access as publishing


class PublicationRecovery(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.private = root / 'private'; self.private.mkdir(mode=0o700)
        self.access = root / 'access'; self.access.mkdir(mode=0o700)
        self.report = root / 'reports/local/class-capacity-03'; self.report.mkdir(parents=True)
        self.state = self.private / 'state.json'
        self.state.write_text(json.dumps({'purpose': 'CLASS-CAPACITY-03', 'phase': 'checked', 'operation_id': 'a' * 32}))
        generation = self.private / ('publish-' + 'b' * 32); generation.mkdir()
        self.journal = {'purpose': 'CLASS-CAPACITY-03', 'operation_id': 'a' * 32, 'generation': generation.name, 'files': {}, 'report': {'status': 'SYNTHETIC_TEST'}}
        for name in ('credentials.json', 'url.txt', 'CRA-adgang-og-login.html', 'CRA-adgang-og-login.pdf', 'render-access.py'):
            old = ('synthetic old ' + name).encode(); new = ('synthetic new ' + name).encode()
            (self.access / name).write_bytes(old)
            (self.private / ('before-' + name)).write_bytes(old)
            (generation / name).write_bytes(new)
            self.journal['files'][name] = {'old': hashlib.sha256(old).hexdigest(), 'new': hashlib.sha256(new).hexdigest()}
        for key, value in {'PRIVATE': self.private, 'ACCESS': self.access, 'STATE': self.state, 'ROOT': root, 'JOURNAL': self.private / 'publishing.json'}.items():
            patcher = patch.object(publishing, key, value); patcher.start(); self.addCleanup(patcher.stop)

    def test_resume_after_all_replacements_before_state(self):
        real_atomic = publishing.atomic
        def interrupted(path, data):
            if path == self.state:
                raise OSError('Synthetic process interruption before state publication')
            real_atomic(path, data)
        with patch.object(publishing, 'atomic', interrupted):
            with self.assertRaises(OSError):
                publishing.finish(self.journal)
        self.assertEqual(json.loads(self.state.read_text())['phase'], 'checked')
        publishing.finish(self.journal)
        self.assertEqual(json.loads(self.state.read_text())['phase'], 'published-private')
        self.assertTrue((self.report / 'private-access.json').exists())

    def test_resume_mixed_files_and_refuse_unknown_owner_change(self):
        name = 'credentials.json'
        generation = self.private / self.journal['generation']
        (self.access / name).write_bytes((generation / name).read_bytes())
        changed = self.access / 'url.txt'; changed.write_text('synthetic owner change')
        with self.assertRaises(AssertionError):
            publishing.finish(self.journal)
        self.assertEqual(changed.read_text(), 'synthetic owner change')
        changed.write_bytes((self.private / 'before-url.txt').read_bytes())
        publishing.finish(self.journal)
        for name in self.journal['files']:
            self.assertEqual((self.access / name).read_bytes(), (generation / name).read_bytes())
            self.assertEqual((self.access / name).stat().st_mode & 0o777, 0o600)


class PreparationRecovery(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_prepared_ids_survive_snapshot_interruption(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); private = root / 'private'; private.mkdir(mode=0o700)
            access = root / 'access'; access.mkdir(); node = root / 'runtime'; node.mkdir()
            old = {'organization_id': 'synthetic', 'accounts': [{'id': f'synthetic-{i}', 'username': f'elev{i:02}', 'password': 'synthetic-placeholder', 'role': 'student'} for i in range(1, 15)] + [{'id': 'synthetic-test', 'username': 'testelev', 'password': 'synthetic-placeholder', 'role': 'student'}, {'id': 'synthetic-teacher', 'username': 'laerer', 'password': 'synthetic-placeholder', 'role': 'teacher'}]}
            (access / 'credentials.json').write_text(json.dumps(old)); (access / 'credentials.json').chmod(0o600)
            for name in ('url.txt', 'CRA-adgang-og-login.html', 'CRA-adgang-og-login.pdf', 'render-access.py'):
                (access / name).write_text('synthetic fixture ' + name)
            with patch.multiple(accounts, PRIVATE=private, ACCESS=access, STATE=private / 'state.json'), patch.object(accounts, 'no_test_writer'), patch.object(accounts.runtime, 'STATE', node), patch.object(accounts.runtime, 'virsh', return_value=SimpleNamespace(stdout='cra-control\n')), patch.object(accounts.n2, 'inventory', return_value={'defined': []}):
                with patch.object(accounts, 'preserve', side_effect=RuntimeError('Synthetic interruption')):
                    with self.assertRaises(RuntimeError): accounts.prepare()
                before = json.loads((private / 'state.json').read_text())
                with patch.object(accounts, 'preserve'):
                    accounts.prepare()
                after = json.loads((private / 'state.json').read_text())
                self.assertEqual(before['new_accounts'], after['new_accounts'])
                self.assertEqual(before['operation_id'], after['operation_id'])
                self.assertEqual(after['phase'], 'prepared')


if __name__ == '__main__':
    unittest.main()
