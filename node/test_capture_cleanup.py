"""Reap even a real owned child that ignores graceful capture stop signals."""
import importlib.util
from pathlib import Path
import subprocess
import sys

spec=importlib.util.spec_from_file_location('course_capture_evidence',Path(__file__).resolve().parents[1]/'content/labs/course/capture_evidence.py')
capture=importlib.util.module_from_spec(spec);spec.loader.exec_module(capture)


def test_unresponsive_owned_child_is_killed_and_reaped():
    child=subprocess.Popen([sys.executable,'-c',"import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('READY',flush=True); time.sleep(60)"],stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='READY'
        capture.stop_capture(child,timeouts=(.05,.05,2))
        assert child.poll()==-9
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=2)
        child.stdout.close()
