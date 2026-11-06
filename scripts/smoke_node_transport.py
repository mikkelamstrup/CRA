#!/usr/bin/env python3
"""Actual mTLS endpoint negatives; does not create or mutate a lab."""
import json
import os
from pathlib import Path
import socket
import ssl
import urllib.error
import urllib.request
from uuid import uuid4

node=Path(os.environ['CRA_NODE_HOME']);certs=node/'.secrets/node-tls'
context=ssl.create_default_context(cafile=certs/'ca.crt');context.minimum_version=ssl.TLSVersion.TLSv1_3
context.load_cert_chain(certs/'client.crt',certs/'client.key')
url='https://127.0.0.1:22290'
with urllib.request.urlopen(url+'/v1/inventory',context=context,timeout=5) as response:
    inventory=json.load(response)
assert inventory['architecture']=='x86_64' and inventory['connected']
plain=ssl.create_default_context(cafile=certs/'ca.crt')
try:
    urllib.request.urlopen(url+'/v1/inventory',context=plain,timeout=5)
except (OSError,urllib.error.URLError):pass
else:raise AssertionError('Missing client certificate accepted')
try:
    with socket.create_connection(('127.0.0.1',22290),timeout=5) as connection:
        context.wrap_socket(connection,server_hostname='incorrect.invalid')
except ssl.SSLCertVerificationError:pass
else:raise AssertionError('Incorrect server identity accepted')
root=Path(__file__).resolve().parents[1]
resources=json.loads((root/'content/missions/first-contact.v1.json').read_text())['resources']
body={'schema_version':1,'job_id':str(uuid4()),'lab_id':str(uuid4()),'organization_id':str(uuid4()),
      'user_id':str(uuid4()),'operation':'create','generation':1,'slot':0,'mission_id':'first-contact',
      'mission_version':1,'resources':{**resources,'architecture':'aarch64'}}
try:
    urllib.request.urlopen(urllib.request.Request(url+'/v1/operations',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),context=context,timeout=5)
except urllib.error.HTTPError as error:assert error.code==409
else:raise AssertionError('Architecture mismatch accepted by actual node')
print('PASS: actual mTLS positive, absent client certificate, wrong server identity, invalid architecture')
relay=ssl.create_default_context(cafile=certs/'ca.crt');relay.minimum_version=ssl.TLSVersion.TLSv1_3
relay.load_cert_chain(certs/'relay.crt',certs/'relay.key')
for tls,path,payload,status in [
    (relay,'/v1/inventory',None,403),
    (relay,'/v1/operations',{},403),
    (context,'/v1/terminal-allocation',{},403),
    (relay,'/v1/terminal-allocation',{'payload':'{}','signature':'0'*64},409),
]:
    message=urllib.request.Request(url+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
    try:urllib.request.urlopen(message,context=tls,timeout=5)
    except urllib.error.HTTPError as error:assert error.code==status
    else:raise AssertionError('Terminal certificate role or signature boundary failed')
print('PASS: actual node certificate role separation and allocation signature rejection')
