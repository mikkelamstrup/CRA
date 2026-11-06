#!/usr/bin/env python3
"""Actual API -> durable worker -> mTLS node -> KVM lifecycle acceptance."""
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
NODE=Path(os.environ.get('CRA_NODE_HOME',ROOT))
sys.path.insert(0,str(ROOT/'infra/linux'))
import runtime

ORIGIN='http://127.0.0.1:28000'
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
csrf=None

def request(path,payload=None,key=None):
    headers={'Origin':ORIGIN,'Content-Type':'application/json'}
    if csrf:headers['X-CRA-CSRF']=csrf
    if key:headers['Idempotency-Key']=key
    message=urllib.request.Request(ORIGIN+path,headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None)
    with client.open(message,timeout=15) as response:return json.load(response)

def wait(operation,expected):
    end=time.monotonic()+240
    while time.monotonic()<end:
        state=request('/api/v1/operations/'+operation['operation_id'])
        if state['status']=='FAILED':raise RuntimeError('Lifecycle failed: '+str(state['error_code']))
        if state['status']=='SUCCEEDED':
            lab=request('/api/v1/labs/'+operation['lab_id'])
            if lab['state']!=expected:raise RuntimeError('Unexpected observed state')
            print(expected+' PASS',flush=True)
            return lab
        time.sleep(2)
    raise TimeoutError('Lifecycle acceptance timeout')

credentials=json.loads((NODE/'.secrets/solo-login.json').read_text())
request('/api/v1/auth/login',credentials)
csrf=request('/api/v1/auth/me')['csrf_token']
body={'mission_id':'first-contact','mission_version':1}
key=str(uuid4())
operation=request('/api/v1/labs',body,key)
assert request('/api/v1/labs',body,key)==operation
print('PASS: actual duplicate create returns same operation/lab',flush=True)
if '--crash-worker' in sys.argv:
    deadline=time.monotonic()+30
    while request('/api/v1/labs/'+operation['lab_id'])['state']!='PROVISIONING':
        if time.monotonic()>deadline:raise TimeoutError('Worker did not claim')
        time.sleep(.2)
    result=runtime.ssh('cra-control','sudo systemctl kill --signal=KILL --kill-whom=main cra-worker')
    if result.returncode:raise RuntimeError('Could not inject owned worker failure')
    print('Injected actual worker exit during provisioning',flush=True)
lab=wait(operation,'READY')
name='cra-'+lab['id'].replace('-','')[:10]+'-1o'
assert runtime.kvm_status(name)=={'enabled':True,'present':True}
result=runtime.ssh(name,"printf synthetic-lifecycle-proof > /home/cra/lifecycle-proof")
assert result.returncode==0
print('PASS: actual KVM and guest file write',flush=True)
wait(request('/api/v1/labs/'+lab['id']+'/stop',{},str(uuid4())),'STOPPED')
wait(request('/api/v1/labs/'+lab['id']+'/start',{},str(uuid4())),'READY')
result=runtime.ssh(name,'cat /home/cra/lifecycle-proof')
assert result.returncode==0 and result.stdout=='synthetic-lifecycle-proof'
print('PASS: stop/start preserved guest disk',flush=True)
wait(request('/api/v1/labs/'+lab['id']+'/destroy',{},str(uuid4())),'DESTROYED')
for suffix in ['o','t']:
    name='cra-'+lab['id'].replace('-','')[:10]+'-1'+suffix
    assert not (runtime.STATE/'vms'/name).exists() and not (runtime.SECRETS/name).exists()
    assert name not in runtime.virsh('list','--all','--name').stdout.splitlines()
request('/api/v1/auth/logout',{})
print('PASS: actual domain/storage/secret cleanup and logout',flush=True)
