"""Owner-only, read-only snapshots of one explicitly selected CRA tmux session.

No input forwarding, terminal attachment, guest changes or recording. Student
output is untrusted and is rendered as plain text only in the owner's TTY.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from uuid import UUID

LIMIT = 131072


def plain(value: str) -> str:
    return ''.join(c for c in value if c in '\n\t' or c.isprintable())


def load_scope(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Scope must be an owner-only regular file')
    scope = json.loads(path.read_text())
    for field in ('id', 'organization_id', 'user_id'):
        if str(UUID(scope[field])) != scope[field]:
            raise ValueError('Invalid scope')
    if type(scope['generation']) is not int or not 1 <= scope['generation'] <= 10000:
        raise ValueError('Invalid generation')
    return scope


def resolve(runtime, scope: dict) -> tuple[str, str]:
    with sqlite3.connect('file:' + str(runtime.STATE / 'node/state.sqlite') + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        lab = db.execute('SELECT * FROM labs WHERE id=?', (scope['id'],)).fetchone()
        if not lab or lab['state'] != 'READY' or any(
            lab[k] != scope[k] for k in ('organization_id', 'user_id', 'generation')
        ):
            raise ValueError('Selected lab is no longer ready in this generation')
        operators = db.execute('SELECT name FROM operators WHERE lab_id=? AND user_id=?',
                               (scope['id'], scope['user_id'])).fetchall()
        if len(operators) != 1:
            raise ValueError('Operator scope mismatch')
        name = operators[0]['name']
    runtime.validate_name(name)
    manifest = json.loads((runtime.STATE / 'vms' / name / 'manifest.json').read_text())
    expected = {k: scope[k] for k in ('organization_id', 'user_id', 'generation')}
    expected['lab_id'] = scope['id']
    domain = runtime.owned_domain(name)
    owner = domain.find('metadata/{urn:cyber-range-academy:v1}ownership')
    if manifest.get('ownership') != expected or owner is None or any(
        owner.get(k) != str(v) for k, v in expected.items()
    ):
        raise ValueError('VM ownership mismatch')
    if manifest.get('egress') is not False or manifest.get('lab', {}).get('role') != 'operator':
        raise ValueError('Unexpected VM profile')
    if runtime.virsh('domstate', name).stdout.strip() != 'running':
        raise ValueError('VM not running')
    session = 'cra-' + scope['user_id'].replace('-', '') + '-' + str(scope['generation'])
    return name, session


def remote(runtime, name: str, command: str) -> str:
    """Pinned project SSH with bounded output/time; never inherit interactive input."""
    runtime.validate_name(name)
    manifest = json.loads((runtime.STATE / 'vms' / name / 'manifest.json').read_text())
    secret = runtime.SECRETS / name
    args = ['ssh', '-F', '/dev/null', '-T', '-i', str(secret / 'client'), '-p', str(manifest['port']),
            '-o', 'IdentityAgent=none', '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={secret / "known_hosts"}',
            '-o', 'ConnectTimeout=3', '-o', 'ClearAllForwardings=yes', '-o', 'EscapeChar=none',
            'cra@127.0.0.1', command]
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        assert process.stdout
        data = bytearray()
        deadline = time.monotonic() + 5
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError('Terminal snapshot timed out')
                    block = os.read(process.stdout.fileno(), min(8192, LIMIT + 1 - len(data)))
                    if not block:
                        break
                    data.extend(block)
                    if len(data) > LIMIT:
                        raise ValueError('Terminal snapshot exceeded limit')
            if process.wait(timeout=max(.1, deadline - time.monotonic())) != 0:
                raise ValueError('Selected terminal session unavailable')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    return data.decode('utf-8', errors='replace')


def capture_command(session: str) -> str:
    if not re.fullmatch(r'cra-[0-9a-f]{32}-[0-9]{1,5}', session):
        raise ValueError('Invalid terminal session')
    return 'tmux capture-pane -p -C -S -80 -t ' + shlex.quote('=' + session + ':')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', type=Path, required=True)
    parser.add_argument('--runtime-source', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    scope = load_scope(args.scope)
    sys.path.insert(0, str(args.runtime_source / 'infra/linux'))
    runtime = importlib.import_module('runtime')
    if not args.check and not sys.stdout.isatty():
        raise ValueError('Livevisningen skal åbnes i din egen terminal; ingen filoptagelse')
    name, session = resolve(runtime, scope)
    if args.check:
        remote(runtime, name, 'tmux has-session -t ' + shlex.quote('=' + session))
        print('Klar: valgt lab og terminal er verificeret. Intet terminalindhold læst.')
        return 0
    deadline = time.monotonic() + 3600
    try:
        while time.monotonic() < deadline:
            if resolve(runtime, scope) != (name, session):
                raise ValueError('Lab scope changed')
            screen = plain(remote(runtime, name, capture_command(session)))
            # Do not display a snapshot if a lifecycle change happened during the read.
            if resolve(runtime, scope) != (name, session):
                raise ValueError('Lab scope changed')
            rows = max(1, shutil.get_terminal_size((100, 30)).lines - 5)
            text = '\n'.join(screen.splitlines()[-rows:])
            print('\033[2J\033[H' + 'Sivbæk · skrivebeskyttet CRA-terminal · ' + time.strftime('%H:%M:%S')
                  + '\nOpdateres hvert 2. sekund · Ctrl+C lukker · ingen optagelse'
                  + '\nViser det aktive terminalpanel; ikke en komplet kommandolog.\n\n' + text,
                  end='', flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        print('\nLivevisning lukket. Elevens terminal fortsætter.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TimeoutError, sqlite3.Error, subprocess.SubprocessError):
        print('Visningen er ikke tilgængelig. Labbet kan være stoppet, nulstillet eller terminalen lukket.', file=sys.stderr)
        raise SystemExit(1)
