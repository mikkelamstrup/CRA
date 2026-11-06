"""Synthetic checks for the local teacher viewer, with no student content."""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

from watch_lab_terminal import capture_command, load_scope, plain, resolve


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = {'id': '11111111-1111-4111-8111-111111111111',
                      'organization_id': '22222222-2222-4222-8222-222222222222',
                      'user_id': '33333333-3333-4333-8333-333333333333', 'generation': 1}
        (self.root / 'node').mkdir()
        self.db = sqlite3.connect(self.root / 'node/state.sqlite')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE labs(id,organization_id,user_id,generation,state);'
                             'CREATE TABLE operators(lab_id,user_id,name);')
        self.db.execute('INSERT INTO labs VALUES (?,?,?,?,?)', (*self.scope.values(), 'READY'))
        self.db.execute('INSERT INTO operators VALUES (?,?,?)',
                        (self.scope['id'], self.scope['user_id'], 'cra-synthetic'))
        self.db.commit()
        self.owner = {k: self.scope[k] for k in ('organization_id', 'user_id', 'generation')}
        self.owner['lab_id'] = self.scope['id']
        self.vm = self.root / 'vms/cra-synthetic'
        self.vm.mkdir(parents=True)
        (self.vm / 'manifest.json').write_text(json.dumps(
            {'ownership': self.owner, 'egress': False, 'lab': {'role': 'operator'}}))
        self.domain = ET.Element('domain')
        metadata = ET.SubElement(self.domain, 'metadata')
        ET.SubElement(metadata, '{urn:cyber-range-academy:v1}ownership',
                      {k: str(v) for k, v in self.owner.items()})
        self.runtime = SimpleNamespace(STATE=self.root, validate_name=lambda n: None,
            owned_domain=lambda n: self.domain, virsh=lambda *a: SimpleNamespace(stdout='running'))

    def test_plain_control_sequences_cannot_execute(self):
        text = plain('\x1b]52;c;SECRET\x07\x9b31m\u202ehello\nworld\t!')
        self.assertTrue(all(c.isprintable() or c in '\n\t' for c in text))
        self.assertNotIn('\x1b', text)

    def test_command_is_exact_read_only_session(self):
        command = capture_command('cra-' + 'a' * 32 + '-1')
        self.assertIn('capture-pane -p -C -S -80 -t =cra-', command)
        with self.assertRaises(ValueError):
            capture_command('x; touch /tmp/bad')

    def test_scope_file_is_private(self):
        path = self.root / 'scope.json'
        path.write_text(json.dumps(self.scope)); path.chmod(0o600)
        self.assertEqual(load_scope(path), self.scope)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            load_scope(path)

    def test_scope_symlink_rejected(self):
        target = self.root / 'real.json'; target.write_text(json.dumps(self.scope)); target.chmod(0o600)
        path = self.root / 'link'; path.symlink_to(target)
        with self.assertRaises(ValueError):
            load_scope(path)

    def test_matching_owner(self):
        name, session = resolve(self.runtime, self.scope)
        self.assertEqual(name, 'cra-synthetic')
        self.assertEqual(session, 'cra-' + self.scope['user_id'].replace('-', '') + '-1')

    def test_generation_change_stops_view(self):
        self.db.execute('UPDATE labs SET generation=2'); self.db.commit()
        with self.assertRaises(ValueError):
            resolve(self.runtime, self.scope)

    def test_stop_stops_view(self):
        self.db.execute("UPDATE labs SET state='STOPPED'"); self.db.commit()
        with self.assertRaises(ValueError):
            resolve(self.runtime, self.scope)

    def test_owner_mismatch_denied(self):
        self.db.execute("UPDATE labs SET user_id='other'"); self.db.commit()
        with self.assertRaises(ValueError):
            resolve(self.runtime, self.scope)

    def test_domain_mismatch_denied(self):
        self.domain.find('metadata')[0].set('lab_id', 'other')
        with self.assertRaises(ValueError):
            resolve(self.runtime, self.scope)


if __name__ == '__main__':
    unittest.main()
