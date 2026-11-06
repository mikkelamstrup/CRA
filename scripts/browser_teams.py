"""Actual two-identity team creation, Linux terminals, lifecycle and shared score."""
import json
from pathlib import Path
import re
import sys
import time
import traceback
from playwright.sync_api import sync_playwright,expect
from teaching import login


def main():
    bundle=json.load(sys.stdin);base='http://127.0.0.1:8000'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        pages=[browser.new_page(viewport={'width':1440,'height':1050}) for _ in range(2)]
        teacher,student=pages;outputs=[bytearray(),bytearray()]
        for index,page in enumerate(pages):
            def watch(socket,index=index):
                def received(frame):
                    if isinstance(frame,bytes):
                        outputs[index].extend(frame)
                        if len(outputs[index])>262144:del outputs[index][:-131072]
                socket.on('framereceived',received)
            page.on('websocket',watch)
        teacher.goto('chrome://sandbox');assert 'You are adequately sandboxed.' in teacher.locator('body').inner_text()
        login(teacher,bundle['users']['teacher']);login(student,bundle['users']['student'])
        teacher.get_by_role('button',name='◇ Mit hold',exact=True).click()
        teacher.get_by_text('Opret et hold',exact=True).click()
        teacher.get_by_label('Holdnavn',exact=True).fill('Syntetisk Nordlys')
        for role in ('teacher','student'):
            teacher.get_by_label(bundle['users'][role]['username'],exact=True).check()
        teacher.get_by_label('Rolle for '+bundle['users']['teacher']['username'],exact=True).select_option('coordinator')
        with teacher.expect_response(lambda r:r.url.endswith('/api/v1/teams') and r.request.method=='POST') as created:
            teacher.get_by_role('button',name='Opret hold',exact=True).click()
        assert created.value.status==201
        expect(teacher.get_by_label('Vælg hold',exact=True)).to_be_visible()
        teacher.get_by_role('button',name='Start mission →',exact=True).click()
        student.get_by_role('button',name='◇ Mit hold',exact=True).click()
        expect(student.get_by_label('Vælg hold',exact=True)).to_be_visible()
        end=time.monotonic()+360
        while True:
            rows=teacher.request.get(base+'/api/v1/labs').json()
            active=next((row for row in rows if row['state']!='DESTROYED'),None)
            if active and active['state']=='ERROR':raise RuntimeError('Team provisioning failed')
            if active and active['state']=='READY':break
            if time.monotonic()>end:raise TimeoutError('Team provisioning deadline')
            teacher.wait_for_timeout(500)
        for page in pages:
            expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        assert student.get_by_role('button',name='Stop miljø',exact=True).count()==0
        assert student.get_by_role('button',name='Nulstil miljø',exact=True).count()==0
        labs=[page.request.get(base+'/api/v1/labs').json() for page in pages]
        a,b=[next(l for l in rows if l['state']=='READY') for rows in labs]
        assert a['id']==b['id'] and a['target_ip']!=b['target_ip'] and a['team_id']==b['team_id']
        lab_id=a['id']
        def command(index,text,expected,timeout=30000):
            page=pages[index];outputs[index].clear();page.locator('.xterm-helper-textarea').focus();page.keyboard.type(text);page.keyboard.press('Enter')
            end=time.monotonic()+timeout/1000
            while expected.encode() not in outputs[index]:
                if time.monotonic()>end:raise TimeoutError('Team terminal evidence missing')
                page.wait_for_timeout(100)
        prefix='cra-'+lab_id.replace('-','')[:10]+'-1'
        command(0,'hostname',prefix+'o')
        command(1,'hostname',prefix+'p')
        command(0,"touch /home/cra/only-first-operator && test -f /home/cra/only-first-operator && printf 'OWN_%s\\n' PRESENT",'OWN_PRESENT')
        command(1,"test ! -f /home/cra/only-first-operator && touch /home/cra/only-second-operator && printf 'SEPARATE_%s\\n' CONFIRMED",'SEPARATE_CONFIRMED')
        def flag(index,target):
            command(index,'curl --fail --max-time 5 http://'+target+':8080/','</html>')
            match=re.search(rb'CRA\{[0-9a-f]{32}\}',outputs[index]);assert match
            return match[0].decode()
        first=flag(0,a['target_ip']);assert first==flag(1,b['target_ip'])
        def submit(page,value):
            page.get_by_label('Dit flag',exact=True).fill(value)
            with page.expect_response(lambda r:'/objectives/find-service/attempts' in r.url) as response:page.get_by_role('button',name='Validér flag',exact=True).click()
            assert response.value.status==200
            return response.value.json()
        assert submit(teacher,first)['points_awarded']==100
        result=submit(student,first);assert result['accepted'] and result['points_awarded']==0 and result['total_points']==100
        print('PASS: browser team creation, two distinct real operators, same target flag and shared one-time score',flush=True)
        # Reconnecting the teacher must not revoke the student's existing shell.
        teacher.get_by_role('button',name='▦ Mit forløb',exact=True).click()
        teacher.get_by_role('button',name='◇ Mit hold',exact=True).click()
        expect(teacher.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        command(1,"printf 'OTHER_SESSION_%s\\n' STILL_CONNECTED",'OTHER_SESSION_STILL_CONNECTED')
        def wait_state(state,generation=None):
            end=time.monotonic()+300
            while time.monotonic()<end:
                row=teacher.request.get(base+'/api/v1/labs/'+lab_id).json()
                if row['state']=='ERROR':raise RuntimeError('Team lifecycle failed')
                if row['state']==state and (generation is None or row['generation']==generation):return row
                teacher.wait_for_timeout(500)
            raise TimeoutError('Team lifecycle deadline')
        teacher.get_by_role('button',name='Stop miljø',exact=True).click();wait_state('STOPPED')
        expect(teacher.get_by_role('button',name='Start miljø',exact=True)).to_be_visible(timeout=15000)
        teacher.get_by_role('button',name='Start miljø',exact=True).click();wait_state('READY')
        for page in pages:expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        command(0,"test -f /home/cra/only-first-operator && printf 'STOP_START_%s\\n' PRESERVED",'STOP_START_PRESERVED')
        command(1,"test -f /home/cra/only-second-operator && printf 'STOP_START_%s\\n' PRESERVED",'STOP_START_PRESERVED')
        teacher.get_by_role('button',name='Nulstil miljø',exact=True).click();teacher.get_by_role('button',name='Nulstil alle maskiner',exact=True).click()
        wait_state('READY',2)
        for page in pages:expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        command(0,"test ! -f /home/cra/only-first-operator && printf 'RESET_%s\\n' CONFIRMED",'RESET_CONFIRMED')
        command(1,"test ! -f /home/cra/only-second-operator && printf 'RESET_%s\\n' CONFIRMED",'RESET_CONFIRMED')
        new_flag=flag(1,b['target_ip']);assert first!=new_flag
        assert not submit(student,first)['accepted']
        result=submit(student,new_flag);assert result['accepted'] and result['points_awarded']==0 and result['total_points']==100
        for index in range(2):command(index,"clear; printf 'TEAM_%s\\n' VERIFIED",'TEAM_VERIFIED')
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        teacher.evaluate('window.scrollTo(0,0)');teacher.screenshot(path=str(folder/'teams-desktop.png'),full_page=True)
        student.set_viewport_size({'width':390,'height':844});student.evaluate('window.scrollTo(0,0)');assert student.evaluate('document.documentElement.scrollWidth<=innerWidth');student.screenshot(path=str(folder/'teams-mobile.png'),full_page=True)
        print('PASS: both operator disks survive stop/start; reset renews all disks/flag and preserves shared score; mobile layout',flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
