"""Atomically update the owner's private login files; no public upload or secret output."""
import base64
import contextlib
import fcntl
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from add_terminal_students import ACCESS, PRIVATE, STATE, REPORT, ROOT, atomic, no_test_writer
from bounded_browser import run_browser

JOURNAL = PRIVATE / 'publishing.json'


def finish(journal):
    state = json.loads(STATE.read_text())
    assert journal['purpose'] == state['purpose'] == 'CLASS-CAPACITY-03'
    assert journal['operation_id'] == state['operation_id']
    assert state['phase'] in ('checked', 'published-private')
    expected_names = {'credentials.json', 'url.txt', 'CRA-adgang-og-login.html', 'CRA-adgang-og-login.pdf', 'render-access.py'}
    assert set(journal['files']) == expected_names
    generation = PRIVATE / journal['generation']
    assert generation.parent == PRIVATE and re.fullmatch(r'publish-[0-9a-f]{32}', generation.name)
    assert generation.is_dir() and not generation.is_symlink()
    assert ACCESS.is_dir() and not ACCESS.is_symlink() and ACCESS.stat().st_mode & 0o077 == 0
    for name, values in journal['files'].items():
        target = ACCESS / name
        assert not target.is_symlink() and not (generation / name).is_symlink()
        data = (generation / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == values['new']
        assert hashlib.sha256((PRIVATE / ('before-' + name)).read_bytes()).hexdigest() == values['old']
        current = hashlib.sha256(target.read_bytes()).hexdigest()
        assert current in (values['old'], values['new']), 'Concurrent owner change; publication stopped'
        if current != values['new']:
            temp = ACCESS / (name + '.pending-' + uuid4().hex)
            with temp.open('xb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            temp.chmod(0o600)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == values['old']
            temp.replace(target)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == values['new']
        target.chmod(0o600)
    state['phase'] = 'published-private'
    atomic(STATE, json.dumps(state, indent=2) + '\n')
    atomic(ROOT / 'reports/local/class-capacity-03/private-access.json', json.dumps(journal['report'], indent=2) + '\n')
    journal['status'] = 'complete'
    atomic(JOURNAL, json.dumps(journal, indent=2) + '\n')
    print('PASS: private HTML/PDF and catalog updated; 18 students plus separate test account; existing passwords unchanged')


def main():
    no_test_writer()
    assert PRIVATE.is_dir() and not PRIVATE.is_symlink() and PRIVATE.stat().st_mode & 0o077 == 0
    lock = (PRIVATE / 'operation.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if JOURNAL.exists():
        assert not JOURNAL.is_symlink()
        finish(json.loads(JOURNAL.read_text()))
        return
    state = json.loads(STATE.read_text())
    assert state['purpose'] == 'CLASS-CAPACITY-03' and state['phase'] == 'checked'
    assert json.loads(REPORT.read_text())['status'] == 'PASS'
    assert hashlib.sha256((ACCESS / 'credentials.json').read_bytes()).hexdigest() == state['source_sha256']
    original = state['original']
    catalog = {**original, 'accounts': original['accounts'] + state['new_accounts']}
    assert len(catalog['accounts']) == 20
    assert len({a['id'] for a in catalog['accounts']}) == len({a['username'] for a in catalog['accounts']}) == 20
    assert sorted(int(a['username'][4:]) for a in catalog['accounts'] if re.fullmatch(r'elev\d+', a['username'])) == list(range(1, 19))
    assert catalog['accounts'][:len(original['accounts'])] == original['accounts']
    url = json.loads((ROOT / 'reports/local/m8-03/quick-tunnel.json').read_text())['url'] if (ROOT / 'reports/local/m8-03/quick-tunnel.json').exists() else json.loads(Path('/home/crauser/cyber-range-academy-plan/reports/local/m8-03/quick-tunnel.json').read_text())['url']
    assert re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com', url)
    outcome = json.loads((ROOT / 'reports/local/class-capacity-03/solo-nodes-acceptance.json').read_text())
    assert outcome['students'] == 18 and outcome['browser_load'] == 'PASS' and outcome['cleanup_errors'] == []
    assert outcome['both_nodes_stop_start_reset'] == 'PASS' and outcome['original_data_preserved'] == 'PASS'
    date = datetime.now(timezone.utc).strftime('%d.%m.%Y')
    def order(a):
        return (0, 0) if a['role'] == 'teacher' else (1, 0) if a['username'] == 'testelev' else (2, int(a['username'][4:]))
    rows = ''.join('<tr><td>' + html.escape(a['username']) + '</td><td>' + html.escape(a['password']) + '</td><td>' + ('Lærer' if a['role'] == 'teacher' else 'Separat testkonto' if a['username'] == 'testelev' else 'Elev') + '</td></tr>' for a in sorted(catalog['accounts'], key=order))
    page = '''<!doctype html><html lang="da"><meta charset="utf-8"><title>CRA · privat klasseadgang</title>
<style>@page{size:A4;margin:15mm}body{font:13px Arial;line-height:1.4;color:#17243a}h1{font-size:25px}h2{font-size:18px}table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:6px;text-align:left;border-bottom:1px solid #ccd4de}td:nth-child(2){font-family:monospace}.notice{background:#e8f1ff;padding:10px}.break{break-before:page}</style>
<h1>CRA · 18 elevkonti</h1><p>Privat lærerdokument · ''' + date + '''. Indeholder adgangskoder. Del kun den enkelte elevs konto med eleven.</p>
<p><b>Login:</b> <a href="''' + html.escape(url) + '''">''' + html.escape(url) + '''</a></p>
<p class="notice">Elev01–elev18 er almindelige elevkonti. Testelev er en separat testkonto. De fire nye konti elev15–elev18 har eget kørende Dag 1-terminalmiljø. De tidligere gemte miljøer er bevaret og forbliver stoppet, indtil de startes i CRA.</p>
<table><thead><tr><th>Brugernavn</th><th>Adgangskode</th><th>Rolle</th></tr></thead><tbody>''' + rows + '''</tbody></table>
<div class="break"><h1>Brug CRA</h1><ol><li>Åbn loginadressen. Log ind med elevens egen konto. Der skal ikke installeres VPN eller SSH på elevcomputeren.</li>
<li>Vælg Dag 1 i Dit forløb. Åbn Mit lab. Vælg Start miljø, hvis det gemte miljø er stoppet, eller Start mission, hvis der endnu ikke er oprettet et miljø.</li>
<li>Undersøg kun dit tildelte lab. Første opgave bruger de rigtige Linux-filer i /home/cra/academy. Gem filer og noter før afslutning.</li>
<li>Læreren bruger sin eksisterende konto til elever, opgaver, hjælp og Command Center. Testelev kan bruges separat til at afprøve forløbet.</li>
<li>Brug adskilte browserprofiler til forskellige konti, eller log ud mellem dem. Almindelige faner deler login.</li></ol>
<h2>Kapacitet og opstart</h2><p>18 samtidige terminalmiljøer er funktionelt prøvet på to servere med syntetiske konti, reelle kommandoer og kort fælles belastning. Det er ikke en prøve med 18 grafiske skriveborde eller en hel undervisningsdag. Start miljøerne i god tid; samtidig opstart kan tage flere minutter. CRA afviser opstart, hvis den nødvendige RAM-reserve ikke kan bevares. Testkontoens ekstra miljø tæller også med.</p>
<h2>Den midlertidige adresse</h2><p>Adressen virker, mens serverne, CRA og tunnelprocessen kører. Tunnellen starter ikke automatisk efter reboot. Adressen kan ændres ved genstart og skal da opdateres i CRA og denne oversigt. Adgang fra skolens eget netværk skal også afprøves.</p>
<p>Del ikke hele dokumentet med klassen.</p></div></html>'''
    assert page.count('<tr>') == 21
    for account in catalog['accounts']:
        assert html.escape(account['password']) in page
    # The renderer emits a private PDF as base64. Capture even failure output in memory.
    with contextlib.redirect_stdout(io.StringIO()):
        encoded = run_browser('browser_access_pdf.py', {'html': page}, timeout=120)
    pdf = base64.b64decode(encoded, validate=True)
    assert pdf.startswith(b'%PDF-') and b'%%EOF' in pdf and len(pdf) < 10000000
    pages = len(re.findall(rb'/Type\s*/Page\b', pdf))
    assert 1 <= pages <= 4
    retired = "raise SystemExit('Den historiske 14-elev-generator er arkiveret. Aktuel oversigt: /home/crauser/cra-classroom-access/CRA-adgang-og-login.pdf. Den reviewede opdatering findes i cyber-range-academy-plan/scripts/publish_terminal_access.py.')\n"
    prepared = {'credentials.json': (json.dumps(catalog, indent=2) + '\n').encode(), 'url.txt': (url + '\n').encode(), 'CRA-adgang-og-login.html': page.encode(), 'CRA-adgang-og-login.pdf': pdf, 'render-access.py': retired.encode()}
    generation = PRIVATE / ('publish-' + uuid4().hex)
    generation.mkdir(mode=0o700)
    for name, value in prepared.items():
        path = generation / name
        with path.open('xb') as stream:
            stream.write(value); stream.flush(); os.fsync(stream.fileno())
        path.chmod(0o600)
    # Refuse to replace any file the owner changed after the prepared backup.
    for name in prepared:
        assert not (ACCESS / name).is_symlink()
        assert (ACCESS / name).read_bytes() == (PRIVATE / ('before-' + name)).read_bytes()
    result = {'status': 'PASS', 'normal_students': 18, 'separate_test_account': True, 'existing_credentials_unchanged': True, 'pdf_pages': pages, 'private_file_permissions': '0600', 'url_updated': True}
    journal = {'purpose': state['purpose'], 'operation_id': state['operation_id'], 'status': 'prepared', 'generation': generation.name, 'files': {name: {'old': hashlib.sha256((PRIVATE / ('before-' + name)).read_bytes()).hexdigest(), 'new': hashlib.sha256(data).hexdigest()} for name, data in prepared.items()}, 'report': result}
    atomic(JOURNAL, json.dumps(journal, indent=2) + '\n')
    finish(journal)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except Exception as error:
        print('FAIL:', type(error).__name__, 'private render output withheld')
        sys.exit(1)
