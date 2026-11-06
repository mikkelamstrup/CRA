"""Run the synthetic UI fixture in CRA's existing sandbox-capable browser guest."""
import base64
import io
import os
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('CRA_NODE_HOME', '/home/crauser/cra-m0-02')
sys.path.insert(0, str(ROOT / 'infra/linux'))
import runtime
from lab_tools import upload


def main():
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        for folder in ('frontend/dist', 'backend/cra', 'content/missions'):
            for path in (ROOT / folder).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:
                    tar.add(path, arcname=str(path.relative_to(ROOT)))
        tar.add(ROOT / 'scripts/browser_cybercon_dispatch.py', arcname='scripts/browser_cybercon_dispatch.py')
    upload('cra-control', '/home/cra/cra-deploy/cybercon-browser.tar.gz', archive.getvalue())
    result = runtime.ssh('cra-control', '''set -eu
sudo install -d -o cra-browser -g cra-browser /opt/cra-cybercon-browser
sudo tar -xzf /home/cra/cra-deploy/cybercon-browser.tar.gz -C /opt/cra-cybercon-browser
sudo chown -R cra-browser:cra-browser /opt/cra-cybercon-browser
sudo -u cra-browser env CRA_TEST_BROWSER=/opt/cra-browser/chrome-linux64/chrome /opt/cra/backend/.venv/bin/python /opt/cra-cybercon-browser/scripts/browser_cybercon_dispatch.py
''', timeout=120)
    print(result.stdout)
    print(result.stderr)
    if result.returncode:
        raise SystemExit(result.returncode)
    dest = ROOT / 'reports/local/cybercon'
    dest.mkdir(parents=True, exist_ok=True)
    for name in ('dispatch-desktop.png', 'dispatch-mobile.png'):
        result = runtime.ssh('cra-control', 'base64 -w0 /opt/cra-cybercon-browser/reports/local/cybercon/' + name)
        if result.returncode:
            raise RuntimeError('Screenshot transfer failed')
        (dest / name).write_bytes(base64.b64decode(result.stdout))
    print('Synthetic screenshots saved in reports/local/cybercon')


if __name__ == '__main__':
    main()
