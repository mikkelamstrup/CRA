"""Local fault injection only; not VM or database acceptance."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import prepare_classroom as task


class RecoveryTests(unittest.TestCase):
    def test_functional_pass_does_not_hide_failed_swap_criterion(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            report = {'status': 'PENDING_RESOURCE_CHECK', 'normal_students_ready': 18, 'placement': {'1': 12, '2': 6}, 'kvm_and_guest_ssh': 'PASS', 'browser_connections_and_reconnect': 'PASS', 'old_accounts_notes_scores_flags_and_bindings_preserved': True, 'non_student_labs_still_stopped': 2, 'registry_restored_byte_exact': True}
            (folder / 'readiness.json').write_text(json.dumps(report))
            sample = {'samples': [{'nodes': {'1': {'MemAvailable': 8500, 'SwapTotal': 8191, 'SwapFree': 8190}, '2': {'MemAvailable': 20000, 'SwapTotal': 8191, 'SwapFree': 8191}}}], 'errors': []}
            (folder / 'check-memory.json').write_text(json.dumps(sample))
            with patch.object(task, 'REPORT', folder), contextlib.redirect_stdout(io.StringIO()): task.summarize()
            actual = json.loads((folder / 'readiness.json').read_text())
            self.assertEqual(actual['functional_status'], 'PASS')
            self.assertEqual(actual['status'], 'FAIL_SWAP_CRITERION')
            self.assertEqual(actual['zero_swap_criterion'], 'FAIL')
            self.assertEqual(json.loads((folder / 'readiness-before-summary.json').read_text()), report)
            sample['errors'].append('resource_sample_failed')
            (folder / 'check-memory.json').write_text(json.dumps(sample))
            with patch.object(task, 'REPORT', folder), self.assertRaises(AssertionError): task.summarize()

    def test_registry_preserves_bytes_and_recovers_lost_response(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'nodes.json'
            original = '[\n {"id":1,"host":"10.0.2.2","accept_new":true},\n {"id":2,"host":"100.64.0.2","accept_new":true}\n]\n'
            path.write_text(original); state = {}; lost = [True]
            def run(code, payload, **kwargs):
                code = code.replace("'/etc/cra/nodes.json'", repr(str(path)))
                with patch('sys.stdin', io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(io.StringIO()) as output:
                    exec(code, {})
                if kwargs.get('root') and lost and lost.pop():
                    raise ConnectionError('Injected lost reply after atomic registry replacement')
                return json.loads(output.getvalue())
            with patch.object(task, 'control', side_effect=run), patch.object(task, 'save'):
                with self.assertRaises(ConnectionError): task.registry(state)
                self.assertFalse(json.loads(path.read_text())[0]['accept_new'])
                task.registry(state, restore=True)
                self.assertEqual(path.read_text(), original)
                path.write_text('[]\n')
                with self.assertRaises(AssertionError): task.registry(state, restore=True)
                self.assertEqual(path.read_text(), '[]\n')

    def test_snapshot_interruption_does_not_publish_partial_json(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'preservation.json'
            code = task.PRESERVE.replace("'/var/lib/cra/class-capacity-04-preservation.json'", repr(str(path)))
            class DB:
                def execute(self, *args): return []
            @contextlib.contextmanager
            def connect(): yield DB()
            module = types.ModuleType('cra.db'); module.connect = connect
            payload = {'mode': 'before', 'operation_id': 'a' * 32}
            def execute():
                with patch.dict('sys.modules', {'cra.db': module}), patch('sys.stdin', io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(io.StringIO()): exec(code, {})
            def partial(value, stream):
                stream.write('{'); raise OSError('Injected interrupted snapshot')
            with patch('json.dump', side_effect=partial):
                with self.assertRaises(OSError): execute()
            self.assertFalse(path.exists())
            execute(); self.assertEqual(json.loads(path.read_text())['operation_id'], payload['operation_id'])
            payload['mode'] = 'after'; execute()
            payload['operation_id'] = 'b' * 32
            with self.assertRaises(AssertionError): execute()


if __name__ == '__main__': unittest.main()
