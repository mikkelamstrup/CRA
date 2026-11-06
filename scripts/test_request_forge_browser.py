"""Run a temporary loopback-only fixture; no service or student lab modifications."""
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
        for name in ('portal.py', 'index.html', 'client.js'):
            path = ROOT / 'content/labs/request-forge' / name
            tar.add(path, arcname=str(path.relative_to(ROOT)))
        tar.add(ROOT / 'scripts/browser_request_forge.py', arcname='scripts/browser_request_forge.py')
    upload('cra-control', '/home/cra/cra-deploy/request-forge-browser.tar.gz', archive.getvalue())
    result = runtime.ssh('cra-control', '''set -eu
sudo install -d -o cra-browser -g cra-browser /opt/cra-request-forge-browser
sudo tar -xzf /home/cra/cra-deploy/request-forge-browser.tar.gz -C /opt/cra-request-forge-browser
sudo chown -R cra-browser:cra-browser /opt/cra-request-forge-browser
sudo -u cra-browser env CRA_TEST_BROWSER=/opt/cra-browser/chrome-linux64/chrome /opt/cra/backend/.venv/bin/python /opt/cra-request-forge-browser/scripts/browser_request_forge.py
''', timeout=120)
    dest = ROOT / 'reports/local/request-forge'
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'browser.log').write_text(result.stdout + result.stderr)
    print(result.stdout)
    print(result.stderr)
    if result.returncode:
        raise SystemExit(result.returncode)
    for name in ('portal-desktop.png', 'portal-mobile.png'):
        result = runtime.ssh('cra-control', 'base64 -w0 /opt/cra-request-forge-browser/reports/local/request-forge/' + name)
        if result.returncode:
            raise RuntimeError('Screenshot transfer failed')
        (dest / name).write_bytes(base64.b64decode(result.stdout))


if __name__ == '__main__':
    main()
