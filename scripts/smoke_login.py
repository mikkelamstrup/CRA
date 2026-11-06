#!/usr/bin/env python3
"""Real loopback HTTP login probe. Credentials and cookies never leave memory/output."""
import http.cookiejar
import json
import os
from pathlib import Path
import urllib.request

root=Path(__file__).resolve().parents[1]
node=Path(os.environ.get('CRA_NODE_HOME',root))
credentials=json.loads((node/'.secrets/solo-login.json').read_text())
origin='http://127.0.0.1:28000'
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def request(path,payload=None,csrf=None):
    headers={'Origin':origin,'Content-Type':'application/json'}
    if csrf:headers['X-CRA-CSRF']=csrf
    message=urllib.request.Request(origin+path,headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None)
    with client.open(message,timeout=10) as response:return json.load(response)

assert request('/api/v1/auth/login',credentials)['authenticated'] is True
me=request('/api/v1/auth/me')
assert me['username']=='solo' and me['role']=='teacher'
assert request('/api/v1/auth/logout',{},me['csrf_token'])['authenticated'] is False
print('PASS: actual HTTP solo login, identity and logout (no secrets emitted)')
