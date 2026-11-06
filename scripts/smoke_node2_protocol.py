"""Actual mTLS role/node fences; only rejected synthetic commands, no VM mutation."""
import json,os,shlex,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime


def main():
    code='''import hashlib,hmac,json,ssl,socket,time,urllib.request,urllib.error
from pathlib import Path
from uuid import uuid4
from cra.contracts import missions
from cra.node_contracts import NodeCommand,NodeInventory
from cra.node_client import request
report={}
for identity in (1,2):
 i=NodeInventory.model_validate(request('/v1/inventory',node_id=identity));assert i.node_id==identity and i.physical_memory_mib and i.running_declared_memory_mib is not None
report['both_actual_node_identities']='PASS'
ca=Path('/var/lib/cra/node-tls/2');relay=Path('/var/lib/cra-relay/node-tls/2')
def context(role):
 folder=ca if role=='client' else relay
 result=ssl.create_default_context(cafile=folder/'ca.crt');result.minimum_version=ssl.TLSVersion.TLSv1_3
 result.load_cert_chain(folder/(role+'.crt'),folder/(role+'.key'));return result
def status(path,role,body=None):
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context(role)))
 message=urllib.request.Request('https://100.64.0.2:22290'+path,data=body,headers={'Content-Type':'application/json'})
 try:
  with opener.open(message,timeout=8) as response:return response.status
 except urllib.error.HTTPError as error:return error.code
assert status('/v1/inventory','relay')==403
assert status('/v1/terminal-allocation','client',b'{}')==403
report['cross_role_http_denied']='PASS'
command=NodeCommand(schema_version=1,node_id=1,job_id=uuid4(),lab_id=uuid4(),organization_id=uuid4(),user_id=uuid4(),operation='destroy',generation=1,slot=17,mission_id='first-contact',mission_version=1,resources=missions()[0].resources)
assert status('/v1/operations','client',command.model_dump_json().encode())==409
report['wrong_node_command_denied']='PASS'
claims={'schema_version':1,'node_id':1,'lab_id':str(uuid4()),'organization_id':str(uuid4()),'user_id':str(uuid4()),'generation':1,'expires_at':int(time.time())+15,'grant_hash':'a'*64}
payload=json.dumps(claims);signature=hmac.new(Path('/var/lib/cra/node-signing/2.key').read_bytes(),payload.encode(),hashlib.sha256).hexdigest()
assert status('/v1/terminal-allocation','relay',json.dumps({'payload':payload,'signature':signature}).encode())==409
assert status('/v1/terminal-allocation','relay',json.dumps({'payload':payload,'signature':'0'*64}).encode())==409
report['wrong_node_claim_and_signature_denied']='PASS'
with context('client').wrap_socket(socket.socket(),server_hostname='100.64.0.2') as stream:
 stream.settimeout(8);stream.connect(('100.64.0.2',22291));stream.sendall(b'{}\\n');assert stream.recv(1)==b''
report['control_role_stream_denied']='PASS'
wrong=ssl.create_default_context(cafile='/var/lib/cra/node-tls/ca.crt');wrong.load_cert_chain('/var/lib/cra/node-tls/client.crt','/var/lib/cra/node-tls/client.key')
try:
 with wrong.wrap_socket(socket.socket(),server_hostname='100.64.0.2') as stream:
  stream.settimeout(8);stream.connect(('100.64.0.2',22290))
except ssl.SSLError:pass
else:raise AssertionError('Node1 CA trusted on node2')
report['separate_node_ca_denied']='PASS'
print(json.dumps({'status':'PASS','checks':report}))
'''
    result=runtime.ssh('cra-control','cd /opt/cra/backend && sudo .venv/bin/python -c '+shlex.quote(code),timeout=90)
    if result.returncode:raise RuntimeError('Node2 protocol test failed; private output withheld')
    report=json.loads(result.stdout);assert report['status']=='PASS'
    out=ROOT/'reports/local/m8-03';out.mkdir(parents=True,exist_ok=True)
    (out/'node2-protocol.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: actual per-node mTLS roles, node IDs, wrong signature, separate CA and stream role fences')


if __name__=='__main__':main()
