"""Stage source separately and run against cra_test; never deploy services or alter live data."""
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
        for folder in ('backend/cra', 'backend/tests', 'content', 'frontend/src'):
            for path in (ROOT / folder).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:
                    tar.add(path, arcname=str(path.relative_to(ROOT)))
        tar.add(ROOT / 'backend/pyproject.toml', arcname='backend/pyproject.toml')
    upload('cra-control', '/home/cra/cra-deploy/request-forge-test.tar.gz', archive.getvalue())
    command = '''set -eu
sudo install -d -o craapp -g craapp /opt/cra-request-forge-test
sudo tar -xzf /home/cra/cra-deploy/request-forge-test.tar.gz -C /opt/cra-request-forge-test
sudo chown -R craapp:craapp /opt/cra-request-forge-test
cd /opt/cra-request-forge-test/backend
sudo -u craapp env CRA_TEST_DATABASE_DSN='dbname=cra_test user=craapp host=/var/run/postgresql' CRA_NODES_FILE=/opt/cra-request-forge-test/synthetic-nodes-absent.json /opt/cra/backend/.venv/bin/python -m pytest -q
'''
    result = runtime.ssh('cra-control', command, timeout=240)
    report = ROOT / 'reports/local/request-forge/postgres.log'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(result.stdout + result.stderr)
    print(result.stdout)
    print(result.stderr)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
