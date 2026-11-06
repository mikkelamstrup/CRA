"""Run inside the owned control VM; install only verified locked deb archives."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def verify(data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('Control package checksum mismatch')


def validate_plan(output, packages):
    allowed = {(p['name'], p['version']) for p in packages}
    for line in output.splitlines():
        if line.startswith('Remv '):
            raise RuntimeError('Package removal outside control lock')
        if line.startswith('Inst '):
            match = re.match(r'Inst (\S+)(?: \[[^\]]+\])? \((\S+)', line)
            if not match or (match[1], match[2]) not in allowed:
                raise RuntimeError('Package action outside control lock')


def install(lock):
    with tempfile.TemporaryDirectory(prefix='cra-packages-') as folder:
        Path(folder).chmod(0o755)  # Public verified archives must be readable by APT's _apt sandbox.
        archives = []
        for index, package in enumerate(lock['packages']):
            directory = Path(folder) / str(index)
            directory.mkdir(mode=0o755)
            subprocess.run(['apt-get', 'download', package['name']+'='+package['version']],
                           cwd=directory, check=True, capture_output=True)
            files = list(directory.glob('*.deb'))
            if len(files) != 1:
                raise RuntimeError('Expected exactly one locked archive')
            verify(files[0].read_bytes(), package['sha256'])
            files[0].chmod(0o644)
            archives.append(str(files[0]))
        specs = [p['name']+'='+p['version'] for p in lock['packages']]
        # APT treats local-path arguments as an acquisition, even with --simulate.
        # Put verified archives in its normal cache and install exact name/version
        # with downloads disabled; never relax the lock or _apt sandbox.
        for archive in archives:
            subprocess.run(['sudo','install','-m','0644',archive,
                            '/var/cache/apt/archives/'+Path(archive).name],
                           check=True,capture_output=True)
        command = ['apt-get', '--no-install-recommends', '--no-download', 'install', *specs]
        result = subprocess.run([*command[:1], '--simulate', *command[1:]],
                                text=True, capture_output=True, check=True)
        validate_plan(result.stdout, lock['packages'])
        subprocess.run(['sudo', 'env', 'DEBIAN_FRONTEND=noninteractive', *command[:1], '-y',
                        *command[1:]], check=True, capture_output=True)


if __name__ == '__main__':
    install(json.loads(Path(sys.argv[1]).read_text()))
