"""Actual mission solution/reset in a sandboxed browser, using a synthetic account."""
import json
from pathlib import Path
import re
import sys
import time
import traceback
from uuid import uuid4

from playwright.sync_api import sync_playwright,expect
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed


def main():
    credentials=json.load(sys.stdin)
    folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
    base='http://127.0.0.1:8000'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1050})
        page=context.new_page();output=bytearray()
        def watch(socket):
            def received(frame):
                if isinstance(frame,bytes):
                    output.extend(frame)
                    if len(output)>262144:del output[:-131072]
            socket.on('framereceived',received)
        page.on('websocket',watch)
        page.goto(base+'/')
        page.get_by_label('Brugernavn').fill(credentials['username'])
        page.get_by_label('Adgangskode').fill(credentials['password'])
        page.get_by_role('button',name='Fortsæt →').click()
        expect(page.get_by_role('button',name='Log ud')).to_be_visible()
        me=context.request.get(base+'/api/v1/auth/me').json()
        headers={'Origin':base,'X-CRA-CSRF':me['csrf_token']}
        labs=context.request.get(base+'/api/v1/labs').json()
        active=next((lab for lab in labs if lab['state']!='DESTROYED'),None)
        if active and '--resume' not in sys.argv:raise RuntimeError('Active lab requires explicit test resume')
        if not active:page.get_by_role('button',name='Start mission →').click()
        lab_id=active['id'] if active else None
        def wait(state,generation=None):
            nonlocal lab_id
            end=time.monotonic()+300
            while time.monotonic()<end:
                labs=context.request.get(base+'/api/v1/labs').json()
                if not lab_id and labs:lab_id=labs[0]['id']
                lab=next((row for row in labs if row['id']==lab_id),None)
                if lab and lab['state']=='ERROR':raise RuntimeError('Actual lifecycle failed')
                if lab and lab['state']==state and (generation is None or lab['generation']==generation):return lab
                page.wait_for_timeout(500)
            raise TimeoutError('Actual lifecycle deadline')
        lab=wait('READY')
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        print('PASS: actual mission and terminal ready',flush=True)
        def command(text,expected=None,timeout=25000):
            output.clear();page.locator('.xterm-helper-textarea').focus()
            page.keyboard.type(text);page.keyboard.press('Enter')
            end=time.monotonic()+timeout/1000
            while expected and expected.encode() not in output:
                if time.monotonic()>end:raise TimeoutError('Actual terminal evidence missing')
                page.wait_for_timeout(100)
        command('nmap -sT -p 1-9000 '+lab['target_ip'],'8080/tcp open')
        def find_flag():
            command('curl --fail --max-time 5 http://'+lab['target_ip']+':8080/','</html>')
            match=re.search(rb'CRA\{[0-9a-f]{32}\}',output)
            assert match
            return match[0].decode()
        flag=find_flag()
        print('PASS: real nmap/curl found generation-bound service flag (value withheld)',flush=True)
        def submit(value):
            page.get_by_label('Dit flag',exact=True).fill(value)
            with page.expect_response(lambda response:'/objectives/find-service/attempts' in response.url) as result:
                page.get_by_role('button',name='Validér flag',exact=True).click()
            response=result.value
            assert response.status==200
            return response.json()
        assert not submit('CRA{synthetic-invalid-answer}')['accepted']
        before=context.request.get(base+'/api/v1/labs/'+lab_id+'/progress').json()['total_points']
        answer=submit(flag)
        assert answer['accepted'] and answer['total_points']==100 and answer['points_awarded']==100-before
        answer=submit(flag)
        assert answer['accepted'] and answer['points_awarded']==0 and answer['already_completed']
        print('PASS: wrong flag denied, correct flag validated, repeated submission gives no extra points',flush=True)
        command("touch /home/cra/reset-proof; printf 'RESET_FILE_%s\\n' CREATED",'RESET_FILE_CREATED')
        old_generation=lab['generation']
        grant=context.request.post(base+'/api/v1/labs/'+lab_id+'/terminal-grants',headers=headers).json()['grant']
        cookie='; '.join(c['name']+'='+c['value'] for c in context.cookies() if c['name']=='cra_session')
        url='ws://127.0.0.1:8000/terminal/'+lab_id
        hello={'type':'auth','grant':grant,'csrf':me['csrf_token'],'cols':100,'rows':30}
        with connect(url,origin=base,additional_headers={'Cookie':cookie},proxy=None) as ws:
            ws.send(json.dumps(hello));assert json.loads(ws.recv(timeout=15))['type']=='ready'
            page.get_by_role('button',name='Nulstil miljø',exact=True).click()
            page.get_by_role('button',name='Nulstil begge maskiner',exact=True).click()
            end=time.monotonic()+10
            while True:
                try:ws.recv(timeout=max(.1,end-time.monotonic()))
                except ConnectionClosed:break
                if time.monotonic()>end:raise TimeoutError('Reset did not close active old terminal')
        print('PASS: reset immediately invalidates active old terminal',flush=True)
        lab=wait('READY',old_generation+1)
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        command("test ! -f /home/cra/reset-proof && printf 'FRESH_DISK_%s\\n' CONFIRMED",'FRESH_DISK_CONFIRMED')
        new_flag=find_flag();assert new_flag!=flag
        assert not submit(flag)['accepted']
        stale=context.request.post(base+'/api/v1/labs/'+lab_id+'/objectives/find-service/attempts',
            headers={**headers,'Idempotency-Key':str(uuid4())},data={'generation':old_generation,'flag':flag})
        assert stale.status==409
        with connect(url,origin=base,additional_headers={'Cookie':cookie},proxy=None) as old:
            old.send(json.dumps(hello))
            try:old.recv(timeout=10);raise AssertionError('Old generation grant accepted')
            except ConnectionClosed:pass
        answer=submit(new_flag)
        assert answer['accepted'] and answer['points_awarded']==0 and answer['total_points']==100
        print('PASS: actual fresh disks/new flag, old flag/generation/grant denied, earned points preserved without farming',flush=True)
        # Remove flag-bearing terminal output from the visible screen before screenshots.
        command("clear; printf 'MISSION_%s\\n' COMPLETE",'MISSION_COMPLETE')
        expect(page.get_by_role('heading',name='Fundet er valideret')).to_be_visible()
        page.screenshot(path=str(folder/'scoring-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(folder/'scoring-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.set_viewport_size({'width':1440,'height':1050})
        page.get_by_role('button',name='Fjern miljø',exact=True).click()
        page.get_by_role('button',name='Fjern miljøet',exact=True).click();wait('DESTROYED')
        page.get_by_role('button',name='Log ud').click()
        expect(page.get_by_label('Brugernavn')).to_be_visible()
        print('PASS: browser mission/reset/score journey, mobile layout, cleanup and logout',flush=True)
        browser.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: scoring acceptance ('+type(error).__name__+') at lines '+','.join(str(frame.lineno) for frame in traceback.extract_tb(error.__traceback__))+'; sensitive values withheld',flush=True)
        sys.exit(1)
