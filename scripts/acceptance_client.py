"""Real local HTTP acceptance client; never log credentials or response bodies."""
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4

ORIGIN=os.environ.get('CRA_ACCEPT_ORIGIN','http://127.0.0.1:28000')
if ORIGIN!='http://127.0.0.1:28000' and not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com',ORIGIN):raise ValueError('Unapproved acceptance origin')

class Client:
    def __init__(self, path):
        self.identity=path if isinstance(path,dict) else json.loads(path.read_text())
        self.cookies=http.cookiejar.CookieJar()
        self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf=None
        self.request('/api/v1/auth/login',{k:self.identity[k] for k in ('username','password')})
        self.csrf=self.request('/api/v1/auth/me')['csrf_token']

    def request(self,path,payload=None,key=None,expected=200,method=None):
        headers={'Origin':ORIGIN,'Content-Type':'application/json'}
        if self.csrf:headers['X-CRA-CSRF']=self.csrf
        if payload is not None:headers['Idempotency-Key']=key or str(uuid4())
        message=urllib.request.Request(ORIGIN+path,method=method,headers=headers,data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with self.http.open(message,timeout=15) as response:
                status=response.status;result=json.load(response)
        except urllib.error.HTTPError as error:
            status=error.code;result=json.load(error)
        if status!=expected:raise RuntimeError('Unexpected HTTP status '+str(status)+' for '+path)
        return result

    def create(self):
        return self.request('/api/v1/labs',{'mission_id':'first-contact','mission_version':1},expected=202)

    def action(self,lab,action):
        return self.request('/api/v1/labs/'+lab['id']+'/'+action,{},expected=202)

    def wait(self,operation,expected,timeout=300):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            job=self.request('/api/v1/operations/'+operation['operation_id'])
            if job['status'] in ('FAILED','SUCCEEDED'):
                lab=self.request('/api/v1/labs/'+operation['lab_id'])
                if lab['state']==expected:return lab
                if job['status']=='FAILED' and expected!='ERROR':raise RuntimeError('Lifecycle failed: '+str(job['error_code']))
            time.sleep(1)
        raise TimeoutError('Lifecycle did not reach '+expected)

    def cookie(self):
        return '; '.join(cookie.name+'='+cookie.value for cookie in self.cookies)
