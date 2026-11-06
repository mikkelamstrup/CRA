"""Deliberately vulnerable IDOR exercise. Run only in isolated target VMs.

The known flaw is the missing report ownership check after authentication.
No filesystem paths or shell commands are derived from HTTP requests.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit


class TrainingServer(ThreadingHTTPServer):
    daemon_threads=True

    def __init__(self,address,flag):
        super().__init__(address,Handler)
        self.flag=flag
        self.sessions={}
        self.lock=threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version='NordlysTraining/1'

    def log_message(self,*args):pass

    def send(self,status,body,kind='application/json',cookie=None):
        data=body.encode() if isinstance(body,str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers()
        self.wfile.write(data)

    def authenticated(self):
        try:
            cookies=SimpleCookie(self.headers.get('Cookie',''))
            token=cookies['training_session'].value if 'training_session' in cookies else ''
        except Exception:return False
        with self.server.lock:
            return self.server.sessions.get(token,0)>time.monotonic()

    def do_GET(self):
        parsed=urlsplit(self.path)
        if parsed.path=='/':
            self.send(200,'''<!doctype html><meta charset="utf-8"><title>Nordlys øvelsesportal</title>
<h1>Nordlys øvelsesportal</h1><p>Fiktive data. Kun til CRA-labbet.</p>
<form action="/login" method="post"><label>Bruger <input name="username" value="analyst"></label>
<label>Øvelseskode <input name="password" type="password"></label><button>Log ind</button></form>
<p>Efter login: <a href="/api/me">min rolle</a> · <a href="/api/reports?id=701">min rapport701</a>.</p>
<p>Servicekataloget nævner også rapport702, som tilhører administratorrollen.</p>''','text/html')
            return
        if parsed.path not in ('/api/me','/api/reports'):
            self.send(404,{'error':'not_found'});return
        if not self.authenticated():
            self.send(401,{'error':'login_required'});return
        if parsed.path=='/api/me':
            self.send(200,{'user':'analyst','role':'reader','allowed_reports':[701]});return
        query=parse_qs(parsed.query)
        if set(query)!={'id'} or len(query['id'])!=1:
            self.send(400,{'error':'one_report_id_required'});return
        ident=query['id'][0]
        if ident=='701':self.send(200,{'id':701,'owner':'analyst','body':'Planlagt service afsluttet. Ingen aktuel hændelse.'})
        elif ident=='702':
            # INTENTIONAL LAB VULNERABILITY: authentication without object authorization.
            self.send(200,{'id':702,'owner':'administrator','body':'NL-702: intern rapport blev udleveret til en forkert rolle. '+self.server.flag})
        else:self.send(404,{'error':'report_not_found'})

    def do_POST(self):
        if self.path!='/login':self.send(404,{'error':'not_found'});return
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:self.send(400,{'error':'bad_length'});return
        if not 0<length<=4096:
            self.close_connection=True;self.send(413,{'error':'body_limit'});return
        try:form=parse_qs(self.rfile.read(length).decode('utf-8'),strict_parsing=True)
        except (ValueError,UnicodeError):self.send(400,{'error':'bad_form'});return
        if form!={'username':['analyst'],'password':['nordlys-training-only']}:
            self.send(403,{'error':'invalid_training_login'});return
        now=time.monotonic()
        with self.server.lock:
            self.server.sessions={token:expiry for token,expiry in self.server.sessions.items() if expiry>now}
            if len(self.server.sessions)>=128:
                self.send(429,{'error':'training_session_limit'});return
            token=secrets.token_urlsafe(24);self.server.sessions[token]=now+3600
        self.send(200,{'message':'Logget ind som analyst','next':'/api/me'},cookie='training_session='+token+'; Path=/; HttpOnly; SameSite=Strict')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    servers=[TrainingServer((ip,8081),config['flag']) for ip in config['addresses']]
    threads=[threading.Thread(target=server.serve_forever) for server in servers]
    for thread in threads:thread.start()
    for thread in threads:thread.join()


if __name__=='__main__':main()
