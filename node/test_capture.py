"""Synthetic metadata fixtures; kernel and real VM evidence are separate spikes."""
import importlib.util
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'infra/linux')]
spec=importlib.util.spec_from_file_location('node_capture',Path(__file__).with_name('capture.py'))
capture=importlib.util.module_from_spec(spec);spec.loader.exec_module(capture)


def flow(seq=1,window=1,**extra):
    return {'kind':'flow','sequence':seq,'window_sequence':window,'observed_at':time.time(),
            'direction':'operator_to_target','src':'192.168.82.20','dst':'192.168.82.10',
            'protocol':6,'source_port':1000,'destination_port':8080,'packets':1,'bytes':100,**extra}


def status(seq=2,window=1,**extra):
    return {'kind':'status','sequence':seq,'window_sequence':window,'observed_at':time.time(),
            'state':'connected','metadata_dropped':0,'unknown_packets':0,'flow_overflow':0,
            'packets':1,'bytes':100,'max_queue':104,**extra}


def line(value):return (json.dumps(value)+'\n').encode()


def test_exact_duplicate_changed_payload_and_seal():
    r=capture.Recorder();first=line(flow());r.accept(first);r.accept(first);r.accept(line(status()))
    assert len(r.windows)==1 and len(r.windows[0].flows)==1 and not r.windows[0].partial
    with pytest.raises(ValueError):r.accept(line(flow(destination_port=22)))
    with pytest.raises(ValueError):r.accept(line(flow(seq=3)))


def test_lost_seal_never_combines_windows_and_gaps_visible():
    r=capture.Recorder();r.accept(line(flow()));r.accept(line(flow(seq=3,window=2,destination_port=22)))
    r.accept(line(status(seq=4,window=2,packets=2,bytes=200,metadata_dropped=1)))
    assert len(r.windows)==1 and r.windows[0].sequence==2 and r.windows[0].partial
    assert [f.destination_port for f in r.windows[0].flows]==[22]
    assert r.sequence_gaps==1 and r.partial_windows==2


def test_bounded_ring_and_fresh_process_required(monkeypatch):
    r=capture.Recorder()
    for i in range(1,13):r.accept(line(status(seq=i,window=i,packets=0,bytes=0,max_queue=0)))
    actor=str(uuid4());epoch=uuid4()
    assert r.snapshot(actor,epoch,True).connected
    assert len(r.windows)==8 and r.ring_overwrites==4
    assert not r.snapshot(actor,epoch,False).connected
    monkeypatch.setattr(capture.time,'monotonic',lambda:r.received+3.01)
    assert not r.snapshot(actor,epoch,True).connected


def test_limits_unknown_fields_counters_clock_and_addresses():
    for record in [flow(src='<script>'),flow(src='fe80::1%arbitrary-text'),flow(actor_id=str(uuid4())),flow(protocol=1),
                   flow(observed_at=float('nan')),flow(observed_at=time.time()-40),
                   status(packets=0),status(metadata_dropped=2),status(unknown_packets=2)]:
        with pytest.raises(ValueError):capture.Recorder().accept(line(record))
    with pytest.raises(ValueError):capture.Recorder().accept(b'x'*4097)
    r=capture.Recorder();r.accept(line(status()))
    with pytest.raises(ValueError):r.accept(line(status(seq=3,window=2,packets=0,bytes=0)))


def test_top_flows_report_omitted_packets():
    r=capture.Recorder()
    for i in range(1,21):r.accept(line(flow(seq=i,destination_port=i)))
    r.accept(line(status(seq=21,packets=22,bytes=2200,flow_overflow=2)))
    w=r.windows[0]
    assert len(w.flows)==16 and w.omitted_packets==6 and not w.partial


@pytest.mark.parametrize('mode',['oversize','rate'])
def test_reader_bounds_before_decode_and_stops_malicious_child(monkeypatch,mode):
    import subprocess
    value=line(status(seq=1,packets=0,bytes=0,max_queue=0))
    script="import os,time;os.write(1,b'x'*20000);time.sleep(10)" if mode=='oversize' else "import os,time;data="+repr(value)+"\nfor _ in range(1000):os.write(1,data)\ntime.sleep(10)"
    def child(*args):
        return subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    monkeypatch.setattr(capture,'launch',child)
    link=capture.Link(str(uuid4()),23200,23400)
    try:
        end=time.monotonic()+4
        while not link.failed and time.monotonic()<end:time.sleep(.01)
        assert link.failed and not link.snapshot().connected
        link.process.wait(timeout=3)
    finally:link.close()
