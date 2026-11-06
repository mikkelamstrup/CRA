"""Refresh only the private login document's classroom-status paragraph; keep credentials."""
import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from prepare_classroom import PRIVATE, STATE, REPORT, ACCESS, atomic
from bounded_browser import run_browser


def digest(path):
    assert path.is_file() and not path.is_symlink()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_private(path, data):
    assert not path.is_symlink()
    with path.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    path.chmod(0o600)


def main():
    os.umask(0o077)
    assert PRIVATE.is_dir() and not PRIVATE.is_symlink() and PRIVATE.stat().st_mode & 0o077 == 0
    lock = (PRIVATE / 'operation.lock').open('a'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = json.loads(STATE.read_text())
    assert state['purpose'] == 'CLASS-CAPACITY-04' and state['phase'] == 'all-ready'
    evidence = json.loads((REPORT / 'readiness.json').read_text())
    assert evidence['status'] in ('PASS', 'FAIL_SWAP_CRITERION')
    assert evidence['functional_status'] == evidence['ram_reserve'] == evidence['monitor_status'] == 'PASS'
    assert evidence['zero_swap_criterion'] in ('PASS', 'FAIL') and set(evidence['observed_swap_used_mib']) == {'1', '2'}
    assert digest(ACCESS) == state['credential_hash']
    folder = ACCESS.parent; assert not folder.is_symlink() and folder.stat().st_mode & 0o077 == 0
    journal_path = PRIVATE / 'access-refresh.json'
    names = ['CRA-adgang-og-login.html', 'CRA-adgang-og-login.pdf']
    if not journal_path.exists():
        old = {name: digest(folder / name) for name in names}
        page = (folder / names[0]).read_text()
        before = 'De fire nye konti elev15–elev18 har eget kørende Dag 1-terminalmiljø. De tidligere gemte miljøer er bevaret og forbliver stoppet, indtil de startes i CRA.'
        after = 'Alle 18 almindelige elevkonti har eget terminalmiljø: 12 på den første server og 6 på node2-example. Alle 18 miljøer og forbindelser var aktive samtidig ved kontrollen 27. oktober 2026. Eksisterende filer og progression er bevaret. Testkontoen og det tidligere holdmiljø holdes stoppet. Start miljøerne igen i CRA, hvis de siden er blevet stoppet.'
        assert page.count(before) == 1
        page = page.replace(before, after)
        with contextlib.redirect_stdout(io.StringIO()):
            encoded = run_browser('browser_access_pdf.py', {'html': page}, timeout=120)
        pdf = base64.b64decode(encoded, validate=True)
        assert pdf.startswith(b'%PDF-') and b'%%EOF' in pdf
        pages = len(re.findall(rb'/Type\s*/Page\b', pdf)); assert 1 <= pages <= 4
        generation = PRIVATE / ('access-refresh-' + uuid4().hex); generation.mkdir(mode=0o700)
        prepared = {names[0]: page.encode(), names[1]: pdf}
        for name in names:
            assert digest(folder / name) == old[name]
            write_private(generation / ('before-' + name), (folder / name).read_bytes())
            write_private(generation / name, prepared[name])
        journal = {'purpose': state['purpose'], 'operation_id': state['operation_id'], 'generation': generation.name, 'status': 'prepared', 'pdf_pages': pages, 'files': {name: {'old': old[name], 'new': hashlib.sha256(prepared[name]).hexdigest()} for name in names}}
        atomic(journal_path, json.dumps(journal))
    journal = json.loads(journal_path.read_text())
    assert journal['purpose'] == state['purpose'] and journal['operation_id'] == state['operation_id']
    assert re.fullmatch(r'access-refresh-[0-9a-f]{32}', journal['generation']) and set(journal['files']) == set(names)
    generation = PRIVATE / journal['generation']; assert not generation.is_symlink()
    for name, hashes in journal['files'].items():
        target = folder / name
        assert digest(generation / name) == hashes['new'] and digest(generation / ('before-' + name)) == hashes['old']
        assert digest(target) in (hashes['old'], hashes['new'])
        if digest(target) != hashes['new']:
            temp = folder / (name + '.' + uuid4().hex)
            write_private(temp, (generation / name).read_bytes())
            assert digest(target) == hashes['old']; temp.replace(target)
        assert digest(target) == hashes['new'] and target.stat().st_mode & 0o077 == 0
    assert digest(ACCESS) == state['credential_hash']
    journal['status'] = 'complete'; atomic(journal_path, json.dumps(journal))
    atomic(REPORT / 'private-access.json', json.dumps({'status': 'PASS', 'pdf_pages': journal['pdf_pages'], 'credentials_unchanged': True, 'private_permissions': '0600'}))
    print('PASS: private PDF/HTML status refreshed; credentials unchanged')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('FAIL:', type(error).__name__, 'private document contents withheld; journal retained')
        raise SystemExit(1)
