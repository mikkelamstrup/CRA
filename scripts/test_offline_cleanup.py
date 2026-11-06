"""A real owned child/process group is stopped when the offline wrapper is interrupted."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from smoke_control_offline import stop_process_group


class OfflineProcessCleanupTests(unittest.TestCase):
    def test_interruption_stops_term_ignoring_descendant_and_reaps_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'child.pid'
            child="import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
            parent="import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c',"+repr(child)+"],stdout=subprocess.PIPE,text=True); assert p.stdout.readline().strip()=='ready'; pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
            process=subprocess.Popen([sys.executable,'-c',parent,str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            try:
                end=time.monotonic()+5
                while not marker.exists():
                    if time.monotonic()>end:self.fail('Owned child not ready')
                    time.sleep(.01)
                descendant=int(marker.read_text())
                try:raise KeyboardInterrupt()
                except KeyboardInterrupt:stop_process_group(process)
                self.assertIsNotNone(process.returncode)
                stat=Path('/proc')/str(descendant)/'stat'
                end=time.monotonic()+2
                while True:
                    try:state=stat.read_text()
                    except (FileNotFoundError,ProcessLookupError):break
                    if ') Z ' in state:break
                    if time.monotonic()>end:self.fail('Owned descendant still active')
                    time.sleep(.01)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGKILL);process.communicate(timeout=5)


if __name__=='__main__':unittest.main()
