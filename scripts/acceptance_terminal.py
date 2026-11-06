"""Real WS/PTY transport helpers with explicit ACKs; output stays in memory."""
import json
import time
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed
from acceptance_client import ORIGIN

def grant(client,lab):
    return client.request('/api/v1/labs/'+lab['id']+'/terminal-grants',{},expected=201)['grant']

def socket(client,lab,token):
    ws=connect(ORIGIN.replace('https:','wss:').replace('http:','ws:')+'/terminal/'+lab['id'],origin=ORIGIN,
               additional_headers={'Cookie':client.cookie()},proxy=None,max_size=65536,open_timeout=10)
    ws.send(json.dumps({'type':'auth','grant':token,'csrf':client.csrf,'cols':100,'rows':30}))
    return ws

def denied(client,lab,token):
    with socket(client,lab,token) as ws:
        try:ws.recv(timeout=12)
        except ConnectionClosed:return
        raise AssertionError('Foreign terminal access accepted')

class Terminal:
    def __init__(self,client,lab):
        self.ws=socket(client,lab,grant(client,lab));self.total=0
        assert json.loads(self.ws.recv(timeout=20))['type']=='ready'
        self.drain()

    def read(self,timeout=10):
        frame=self.ws.recv(timeout=timeout)
        if isinstance(frame,bytes):
            self.total+=len(frame);self.ws.send(json.dumps({'type':'ack','bytes':self.total}))
            return frame
        return b''

    def drain(self):
        while True:
            try:self.read(.2)
            except TimeoutError:return

    def command(self,command,marker,timeout=30):
        self.drain();self.ws.send(command.encode()+b'\n')
        output=bytearray();end=time.monotonic()+timeout
        while marker not in output:
            if time.monotonic()>end:raise TimeoutError('Terminal evidence absent')
            try:output.extend(self.read(min(10,max(.1,end-time.monotonic()))))
            except TimeoutError:continue
            if len(output)>1024*1024:raise RuntimeError('Acceptance output limit')
        return bytes(output)

    def close(self):self.ws.close()
