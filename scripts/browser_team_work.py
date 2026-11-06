"""M5-02 actual two-browser notes, roles, transition; no mocked network responses."""
import json
from pathlib import Path
import sys
import time
import traceback
from playwright.sync_api import sync_playwright,expect
from teaching import login


def main():
    b=json.load(sys.stdin);base='http://127.0.0.1:8000'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        pages=[browser.new_page(viewport={'width':1440,'height':1050}) for _ in range(2)]
        teacher,student=pages;outputs=[bytearray(),bytearray()]
        for i,page in enumerate(pages):
            def watch(socket,i=i):
                def received(frame):
                    if isinstance(frame,bytes):
                        outputs[i].extend(frame)
                        if len(outputs[i])>262144:del outputs[i][:-131072]
                socket.on('framereceived',received)
            page.on('websocket',watch)
        teacher.goto('chrome://sandbox');assert 'You are adequately sandboxed.' in teacher.locator('body').inner_text()
        for page,role in zip(pages,('teacher','student')):
            login(page,b['users'][role]);page.get_by_role('button',name='◇ Mit hold',exact=True).click()
            expect(page.get_by_label('Vælg hold',exact=True)).to_be_visible()
            page.get_by_role('button',name='Stop og bevar mit sololab',exact=True).click()
        for page in pages:expect(page.get_by_text('Dit sololab er stoppet',exact=True)).to_be_visible(timeout=60000)
        teacher.get_by_role('button',name='Start mission →',exact=True).click()
        end=time.monotonic()+360
        while True:
            rows=teacher.request.get(base+'/api/v1/labs').json();hold=next((r for r in rows if r['team_id']==b['team_id'] and r['state']!='DESTROYED'),None)
            if hold and hold['state']=='ERROR':raise RuntimeError('Team provisioning failed')
            if hold and hold['state']=='READY':break
            if time.monotonic()>end:raise TimeoutError('Team provisioning deadline')
            teacher.wait_for_timeout(500)
        for page in pages:expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        def command(i,value,marker):
            outputs[i].clear();pages[i].locator('.xterm-helper-textarea').focus();pages[i].keyboard.type(value,delay=1);pages[i].keyboard.press('Enter');until=time.monotonic()+25
            while marker.encode() not in outputs[i]:
                if time.monotonic()>until:raise TimeoutError('Terminal output missing')
                pages[i].wait_for_timeout(100)
        prefix='cra-'+hold['id'].replace('-','')[:10]+'-1'
        command(0,'hostname',prefix+'o');command(1,'hostname',prefix+'p')
        for page in pages:
            page.get_by_text('Noter og fælles fund',exact=True).click()
            expect(page.get_by_label('Fælles forklaring',exact=True)).to_be_enabled()
        teacher.get_by_label('Fælles forklaring',exact=True).fill('Fælles observation fra læreren')
        teacher.get_by_role('button',name='Gem holdnoter',exact=True).click()
        expect(teacher.get_by_text('Holdets noter er gemt.',exact=True)).to_be_visible()
        student.get_by_label('Fælles forklaring',exact=True).fill('Bevar dette elevudkast')
        with student.expect_response(lambda r:r.url.endswith('/notebook') and r.request.method=='PUT') as conflict:
            student.get_by_role('button',name='Gem holdnoter',exact=True).click()
        assert conflict.value.status==409
        expect(student.get_by_label('Fælles forklaring',exact=True)).to_have_value('Bevar dette elevudkast')
        student.once('dialog',lambda d:d.dismiss());student.get_by_role('button',name='▦ Mit forløb',exact=True).click()
        expect(student.get_by_label('Fælles forklaring',exact=True)).to_have_value('Bevar dette elevudkast')
        delayed=[]
        def slow_get(route):
            if route.request.method!='GET':route.continue_();return
            response=route.fetch();student.wait_for_timeout(450)
            assert student.get_by_label('Fælles forklaring',exact=True).is_disabled()
            assert student.get_by_role('button',name='Hent holdets version',exact=True).is_disabled()
            delayed.append(True);route.fulfill(response=response)
        student.route('**/api/v1/teams/*/notebook',slow_get)
        student.once('dialog',lambda d:d.accept());student.get_by_role('button',name='Hent holdets version',exact=True).click()
        expect(student.get_by_label('Fælles forklaring',exact=True)).to_be_enabled();assert delayed
        student.unroute('**/api/v1/teams/*/notebook',slow_get)
        expect(student.get_by_label('Fælles forklaring',exact=True)).to_have_value('Fælles observation fra læreren')
        student.get_by_label('Fælles forklaring',exact=True).fill('Vi har sammenlignet kilde og observation.')
        student.get_by_role('button',name='Gem holdnoter',exact=True).click()
        expect(student.get_by_text('Holdets noter er gemt.',exact=True)).to_be_visible()
        literal='<img src=x onerror="window.teamXss=1">'
        student.get_by_label('Kilde til fundet',exact=True).fill('Egen terminal: hostname')
        student.get_by_label('Hvad observerede du?',exact=True).fill(literal)
        student.get_by_role('button',name='Indlevér fund',exact=True).click()
        expect(student.locator('.team-findings')).to_contain_text(literal)
        teacher.get_by_role('button',name='Opdatér fund',exact=True).click()
        expect(teacher.locator('.team-findings')).to_contain_text(literal)
        assert teacher.evaluate('window.teamXss===undefined') and student.evaluate('window.teamXss===undefined')
        teacher.get_by_text('Fordel og byt roller',exact=True).click()
        teacher.get_by_label('Arbejdsrolle for '+b['users']['teacher']['username'],exact=True).select_option('reporter')
        teacher.get_by_label('Arbejdsrolle for '+b['users']['student']['username'],exact=True).select_option('coordinator')
        with teacher.expect_response(lambda r:r.url.endswith('/roles') and r.request.method=='PUT') as roles:
            teacher.get_by_role('button',name='Gem roller',exact=True).click()
        assert roles.value.status==200
        command(0,'hostname',prefix+'o');command(1,'hostname',prefix+'p')
        for i in range(2):command(i,"clear; printf 'TEAM_WORK_%s\\n' VERIFIED",'TEAM_WORK_VERIFIED')
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        teacher.evaluate('window.scrollTo(0,0)');teacher.screenshot(path=str(folder/'team-work-desktop.png'),full_page=True)
        student.set_viewport_size({'width':390,'height':844});assert student.evaluate('document.documentElement.scrollWidth<=innerWidth');student.screenshot(path=str(folder/'team-work-mobile.png'),full_page=True)
        teacher.get_by_role('button',name='Stop miljø',exact=True).click()
        end=time.monotonic()+90
        while teacher.request.get(base+'/api/v1/labs/'+hold['id']).json()['state']!='STOPPED':
            if time.monotonic()>end:raise TimeoutError('Stop deadline')
            teacher.wait_for_timeout(500)
        print('PASS: real slot-2 team terminals, browser solo stop, conflicting notes/draft preservation, delayed real GET, literal findings, stable operators after role swap',flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL: '+type(e).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
