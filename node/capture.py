"""Trusted bounded reader for disposable, confined link observers.

The child supplies only numeric packet metadata. Assignment/epoch/lab identity
comes from the node journal. Losing this manager requires coordinated recovery.
"""
from collections import deque
import hashlib
import json
import os
import subprocess
import threading
import time
from uuid import UUID,uuid4

from cra.flow_contracts import FlowWindow,HeaderFlow,NodeCapture,RawFlow,RawStatus
from flow_proxy import launch


class Recorder:
    def __init__(self):
        self.lock=threading.Lock();self.sequence=0;self.window=0;self.sealed=False
        self.hashes={};self.hash_order=deque();self.pending=[];self.partial=False
        self.windows=deque(maxlen=8);self.sequence_gaps=0;self.partial_windows=0;self.ring_overwrites=0
        self.status=None;self.received=0.0

    def accept(self,line):
        if len(line)>4096 or not line.endswith(b'\n'):raise ValueError('Metadata bound')
        value=json.loads(line)
        raw=RawFlow.model_validate(value) if value.get('kind')=='flow' else RawStatus.model_validate(value)
        now=time.time()
        if not now-30<=raw.observed_at<=now+5:raise ValueError('Metadata clock bound')
        digest=hashlib.sha256(line).digest()
        with self.lock:
            if raw.sequence<=self.sequence:
                if self.hashes.get(raw.sequence)==digest:return
                raise ValueError('Changed or regressive metadata sequence')
            gap=raw.sequence-self.sequence-1
            self.sequence_gaps+=gap;self.sequence=raw.sequence
            self.hashes[raw.sequence]=digest;self.hash_order.append(raw.sequence)
            if len(self.hash_order)>128:del self.hashes[self.hash_order.popleft()]
            if raw.window_sequence<self.window:raise ValueError('Regressive metadata window')
            if raw.window_sequence!=self.window:
                if self.window and not self.sealed:self.partial_windows+=1
                if self.window:self.partial_windows+=max(0,raw.window_sequence-self.window-1)
                self.window=raw.window_sequence;self.pending=[];self.partial=False;self.sealed=False
            elif self.sealed:raise ValueError('Record after window seal')
            self.partial=self.partial or gap>0
            if isinstance(raw,RawFlow):
                if len(self.pending)>=64:raise ValueError('Flow window bound')
                self.pending.append(HeaderFlow.model_validate(raw.model_dump(exclude={'kind','sequence','window_sequence','observed_at'})))
                return
            previous=self.status
            if previous:
                for field in ('metadata_dropped','unknown_packets','flow_overflow','packets','bytes','max_queue'):
                    if getattr(raw,field)<getattr(previous,field):raise ValueError('Regressive sensor counter')
                if previous.state=='disconnected' or (previous.state=='connected' and raw.state=='waiting'):
                    raise ValueError('Regressive link state')
            old_packets=previous.packets if previous else 0;old_bytes=previous.bytes if previous else 0
            if sum(f.packets for f in self.pending)>raw.packets-old_packets or sum(f.bytes for f in self.pending)>raw.bytes-old_bytes:
                raise ValueError('Flow counts exceed sensor totals')
            ordered=sorted(self.pending,key=lambda f:(-f.packets,f.model_dump_json()))
            omitted=sum(f.packets for f in ordered[16:])+raw.flow_overflow-(previous.flow_overflow if previous else 0)
            partial=self.partial or raw.metadata_dropped>(previous.metadata_dropped if previous else 0)
            if partial:self.partial_windows+=1
            if len(self.windows)==8:self.ring_overwrites+=1
            self.windows.append(FlowWindow(sequence=self.window,source_sequence=raw.sequence,observed_at=raw.observed_at,
                                           flows=ordered[:16],partial=partial,omitted_packets=omitted))
            self.status=raw;self.received=time.monotonic();self.sealed=True;self.pending=[]

    def snapshot(self,operator,epoch,alive):
        with self.lock:
            raw=self.status
            return NodeCapture(operator_user_id=UUID(operator),epoch=epoch,
                connected=bool(alive and raw and raw.state=='connected' and time.monotonic()-self.received<3),
                observed_at=raw.observed_at if raw else 0,last_window=raw.window_sequence if raw else 0,
                metadata_dropped=raw.metadata_dropped if raw else 0,unknown_packets=raw.unknown_packets if raw else 0,
                flow_overflow=raw.flow_overflow if raw else 0,sequence_gaps=self.sequence_gaps,
                partial_windows=self.partial_windows,ring_overwrites=self.ring_overwrites,
                packets=raw.packets if raw else 0,bytes=raw.bytes if raw else 0,windows=list(self.windows))


class Link:
    def __init__(self,operator,front,target):
        self.operator=operator;self.epoch=uuid4();self.recorder=Recorder()
        self.process=launch(front,target);self.failed=False
        self.reader=threading.Thread(target=self.read,daemon=True);self.reader.start()

    def read(self):
        buffer=b'';tokens=131072.0;records=256.0;last=time.monotonic()
        try:
            while True:
                data=os.read(self.process.stdout.fileno(),4096)
                if not data:break
                now=time.monotonic();elapsed=now-last;last=now
                tokens=min(131072,tokens+elapsed*65536)-len(data)
                records=min(256,records+elapsed*128)
                if tokens<0:raise ValueError('Metadata rate bound')
                buffer+=data  # At most 8192 bytes, including a prior partial line.
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1);records-=1
                    if records<0:raise ValueError('Metadata record rate bound')
                    self.recorder.accept(line+b'\n')
                if len(buffer)>4096:raise ValueError('Metadata line bound')
            if buffer:raise ValueError('Truncated metadata')
        except Exception:
            self.failed=True  # No attacker-controlled exception text in logs.
        finally:
            if self.process.poll() is None:self.process.terminate()

    def snapshot(self):
        return self.recorder.snapshot(self.operator,self.epoch,not self.failed and self.process.poll() is None)

    def close(self):
        if self.process.poll() is None:self.process.terminate()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill();self.process.wait(timeout=5)
        self.reader.join(3);self.process.stdout.close()


class Manager:
    def __init__(self):
        self.lock=threading.Lock();self.labs={}

    def get(self,lab_id,generation,operators):
        with self.lock:
            entry=self.labs.get(str(lab_id))
            if not entry or entry[0]!=generation or [link.operator for link in entry[1]]!=[str(op) for op in operators]:return []
            links=list(entry[1])
        return [link.snapshot() for link in links]

    def healthy(self,lab_id,generation,operators):
        records=self.get(lab_id,generation,operators)
        return len(records)==len(operators) and bool(records) and all(row.connected for row in records)

    def stop(self,lab_id):
        with self.lock:entry=self.labs.pop(str(lab_id),None)
        if entry:
            for link in entry[1]:link.close()

    def start(self,lab_id,generation,operators,links):
        # Lifecycle caller has already fenced ownership and stopped own operators.
        self.stop(lab_id);created=[]
        try:
            for operator,network in zip(operators,links,strict=True):
                created.append(Link(str(operator),network['port'],network['port']+200))
            with self.lock:self.labs[str(lab_id)]=(generation,created)
        except Exception:
            for link in created:link.close()
            raise


MANAGER=Manager()
