"""Runs as the unprivileged browser user INSIDE the control VM. Secrets on stdin only."""
import json
from pathlib import Path
import sys
import time
import traceback

from playwright.sync_api import sync_playwright, expect
from websockets.sync.client import connect
from websockets.exceptions import InvalidStatus,ConnectionClosed


def protocol_acceptance(context,lab_id):
    base='http://127.0.0.1:8000'
    csrf=context.request.get(base+'/api/v1/auth/me').json()['csrf_token']
    headers={'Origin':base,'X-CRA-CSRF':csrf}
    cookie='; '.join(c['name']+'='+c['value'] for c in context.cookies() if c['name']=='cra_session')
    url='ws://127.0.0.1:8000/terminal/'+lab_id
    try:
        with connect(url,origin='https://foreign.invalid',additional_headers={'Cookie':cookie},proxy=None):
            raise AssertionError('Foreign Origin was accepted')
    except InvalidStatus:pass
    print('PASS: foreign WS Origin rejected',flush=True)
    grant=context.request.post(base+'/api/v1/labs/'+lab_id+'/terminal-grants',headers=headers).json()['grant']
    hello={'type':'auth','grant':grant,'csrf':csrf,'cols':100,'rows':30}
    with connect(url,origin=base,proxy=None) as stranger:
        stranger.send(json.dumps(hello))
        try:stranger.recv(timeout=10);raise AssertionError('Missing cookie accepted')
        except ConnectionClosed:pass
    print('PASS: WS without cookie rejected',flush=True)
    with connect(url,origin=base,additional_headers={'Cookie':cookie},proxy=None,max_size=65536) as ws:
        ws.send(json.dumps(hello));assert json.loads(ws.recv(timeout=15))['type']=='ready'
        total=0
        # Drain the initial screen, acknowledging only consumed frames.
        while True:
            try:data=ws.recv(timeout=.3)
            except TimeoutError:break
            if isinstance(data,bytes):total+=len(data);ws.send(json.dumps({'type':'ack','bytes':total}))
        start=total
        ws.send(b"python3 -c 'import sys;sys.stdout.write(chr(88)*350000)'; printf 'FLOW_%s\\n' DONE\n")
        tail=bytearray()
        while total-start<65536:
            frame=ws.recv(timeout=10)
            assert isinstance(frame,bytes)
            total+=len(frame);tail.extend(frame)
        assert total-start==65536
        print('PASS: output stopped at 65536 unacknowledged bytes',flush=True)
        try:ws.recv(timeout=.7);raise AssertionError('Output credit limit exceeded')
        except TimeoutError:pass
        ws.send(json.dumps({'type':'ack','bytes':total}))
        end=time.monotonic()+30
        while b'FLOW_DONE' not in tail:
            if time.monotonic()>end:raise TimeoutError('Flow did not resume')
            frame=ws.recv(timeout=10)
            if isinstance(frame,bytes):
                total+=len(frame);tail.extend(frame)
                if len(tail)>65536:del tail[:-65536]
                ws.send(json.dumps({'type':'ack','bytes':total}))
        print('PASS: large output resumed after ACK',flush=True)
        with connect(url,origin=base,additional_headers={'Cookie':cookie},proxy=None) as duplicate:
            duplicate.send(json.dumps(hello))
            try:duplicate.recv(timeout=10);raise AssertionError('Grant replay accepted')
            except ConnectionClosed:pass
        ws.send(json.dumps({'type':'resize','cols':91,'rows':23}))
        time.sleep(.25)
        ws.send(b"tmux display-message -p '#{client_height} #{client_width}'; printf 'RESIZE_%s\\n' OK\n")
        tail=bytearray()
        while b'RESIZE_OK' not in tail:
            frame=ws.recv(timeout=10)
            if isinstance(frame,bytes):
                total+=len(frame);tail.extend(frame);ws.send(json.dumps({'type':'ack','bytes':total}))
        assert b'23 91' in tail
        # A new grant must close a live connection, without terminating tmux.
        context.request.post(base+'/api/v1/labs/'+lab_id+'/terminal-grants',headers=headers)
        end=time.monotonic()+8
        while True:
            try:ws.recv(timeout=max(.1,end-time.monotonic()))
            except ConnectionClosed:break
            if time.monotonic()>end:raise TimeoutError('Active terminal was not revoked')
    assert context.request.post(base+'/internal/terminal/consume',headers=headers,data=hello).status==404
    print('PASS: real WS Origin/cookie/replay denial, 64KiB backpressure/resume, PTY resize and live revocation',flush=True)


def main():
    credentials=json.load(sys.stdin)
    folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000})
        page=context.new_page()
        output=bytearray();ready_frames=[]
        def watch(socket):
            def received(frame):
                if isinstance(frame,str):
                    if json.loads(frame).get('type')=='ready':ready_frames.append(True)
                if isinstance(frame,bytes):
                    output.extend(frame)
                    if len(output)>2*1024*1024:del output[:-1024*1024]
            socket.on('framereceived',received)
        page.on('websocket',watch)
        page.goto('chrome://sandbox')
        sandbox=page.locator('body').inner_text()
        for evidence in ['You are adequately sandboxed.','Seccomp-BPF sandbox\tYes','PID namespaces\tYes','Network namespaces\tYes']:
            assert evidence in sandbox
        print('PASS: actual Chromium SUID/namespaces/seccomp sandbox',flush=True)
        page.goto('http://127.0.0.1:8000/')
        page.get_by_label('Brugernavn').fill(credentials['username'])
        page.get_by_label('Adgangskode').fill(credentials['password'])
        page.get_by_role('button',name='Fortsæt →').click()
        expect(page.get_by_role('button',name='Log ud')).to_be_visible()
        page.screenshot(path=str(folder/'dashboard.png'),full_page=True)
        print('PASS: actual browser login and mission briefing',flush=True)
        existing=context.request.get('http://127.0.0.1:8000/api/v1/labs').json()
        if any(row['state']!='DESTROYED' for row in existing) and '--resume' not in sys.argv:
            raise RuntimeError('Active lab exists; explicit test resume required')
        if not any(row['state']!='DESTROYED' for row in existing):
            page.get_by_role('button',name='Start mission →').click()
        lab_id=None
        def wait(state):
            nonlocal lab_id
            end=time.monotonic()+240
            while time.monotonic()<end:
                rows=context.request.get('http://127.0.0.1:8000/api/v1/labs').json()
                if not lab_id and rows and rows[0]['state']!='DESTROYED':lab_id=rows[0]['id']
                lab=next((row for row in rows if row['id']==lab_id),None)
                if lab and lab['state']==state:
                    print('PASS: browser lifecycle '+state,flush=True);return
                if lab and lab['state']=='ERROR':raise RuntimeError('Browser lifecycle failed')
                time.sleep(1)
            raise TimeoutError('Browser lifecycle timeout')
        wait('READY')
        expect(page.get_by_role('button',name='Stop miljø',exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        print('PASS: browser terminal connected',flush=True)
        def command(text,expected,timeout=20000):
            print('Check terminal evidence: '+expected,flush=True)
            output.clear()
            page.locator('.xterm-helper-textarea').focus()
            page.keyboard.type(text);page.keyboard.press('Enter')
            end=time.monotonic()+timeout/1000
            while expected.encode() not in output:
                if time.monotonic()>end:raise TimeoutError('Terminal command did not produce expected evidence')
                page.wait_for_timeout(100)
        command("printf 'REAL_%s\\n' TERMINAL",'REAL_TERMINAL')
        command("printf 'UTF_%s\\n' 'æøå'",'UTF_æøå')
        row=context.request.get('http://127.0.0.1:8000/api/v1/labs/'+lab_id).json()
        command('nmap -sT -p 8080 '+row['target_ip'],'8080/tcp open')
        command('curl --fail --max-time 5 http://'+row['target_ip']+':8080/','Feltstation 01')
        command("printf 'kept' > /home/cra/browser-proof; printf 'FILE_%s\\n' SAVED",'FILE_SAVED')
        before=len(ready_frames)
        page.get_by_role('button',name='Forbind igen',exact=True).click()
        end=time.monotonic()+15
        while len(ready_frames)==before:
            if time.monotonic()>end:raise TimeoutError('New terminal did not authenticate')
            page.wait_for_timeout(100)
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=15000)
        command("test -f /home/cra/browser-proof && printf 'SESSION_%s\\n' KEPT",'SESSION_KEPT')
        output.clear();page.keyboard.type('sleep 60');page.keyboard.press('Enter');page.wait_for_timeout(400)
        page.keyboard.press('Control+c')
        command("printf 'INTERRUPT_%s\\n' OK",'INTERRUPT_OK')
        print('PASS: real browser SSH PTY, Unicode, nmap/curl, reconnect and Ctrl-C',flush=True)
        protocol_acceptance(context,lab_id)
        page.get_by_role('button',name='Genopret forbindelse',exact=True).click(timeout=15000)
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=15000)
        command("printf 'AFTER_FLOW_%s\\n' OK",'AFTER_FLOW_OK')
        page.screenshot(path=str(folder/'terminal-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(folder/'terminal-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.set_viewport_size({'width':1440,'height':1000})
        command("stty size; printf 'SIZE_%s\\n' OK",'SIZE_OK')
        page.get_by_role('button',name='Stop miljø',exact=True).click();wait('STOPPED')
        page.get_by_role('button',name='Start miljø',exact=True).click();wait('READY')
        page.get_by_role('button',name='Fjern miljø',exact=True).click()
        page.get_by_role('button',name='Fjern miljøet',exact=True).click();wait('DESTROYED')
        expect(page.get_by_role('button',name='Start mission →')).to_be_visible(timeout=15000)
        page.get_by_role('button',name='Log ud').click()
        expect(page.get_by_label('Brugernavn')).to_be_visible()
        assert context.request.get('http://127.0.0.1:8000/api/v1/labs').status==401
        print('PASS: browser cleanup, mobile overflow and logout',flush=True)
        browser.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Playwright exception details can include entered credentials; never emit them.
        print('FAIL: browser acceptance ('+type(error).__name__+') at lines '+','.join(str(frame.lineno) for frame in traceback.extract_tb(error.__traceback__))+'; no raw exception emitted',flush=True)
        sys.exit(1)
