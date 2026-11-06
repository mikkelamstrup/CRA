"""Actual teacher/student browsers, real VM reset and observed domain events."""
import json
from pathlib import Path
import re
import sys
import time
import traceback
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect
from teaching import login


def main():
    b=json.load(sys.stdin);base='http://127.0.0.1:8000';out=Path('/var/lib/cra-browser/acceptance');out.mkdir(mode=0o700,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        teacher=browser.new_page(viewport={'width':1512,'height':1080});student=browser.new_page(viewport={'width':1366,'height':1000});output=bytearray();responses=[]
        def watch(ws):
            if '/terminal/' not in ws.url:return
            def received(frame):
                if isinstance(frame,bytes):
                    output.extend(frame)
                    if len(output)>262144:del output[:-131072]
            ws.on('framereceived',received)
        student.on('websocket',watch)
        def response(r):
            if '/api/v1/command-center/' in r.url and r.status==200:
                try:responses.append(r.text())
                except Exception:pass
                if len(responses)>500:del responses[:-500]
        teacher.on('response',response)
        def command(value,marker):
            output.clear();student.locator('.xterm-helper-textarea').focus();student.keyboard.type(value,delay=1);student.keyboard.press('Enter');until=time.monotonic()+30
            while marker.encode() not in output:
                if time.monotonic()>until:raise TimeoutError('Terminal proof absent')
                student.wait_for_timeout(100)
            return bytes(output)
        login(teacher,b['users']['teacher']);login(student,b['users']['student'])
        assert student.get_by_role('button',name='◎ Command Center',exact=True).count()==0
        assert student.request.get(base+'/api/v1/command-center/overview').status==403
        teacher.get_by_role('button',name='◎ Command Center',exact=True).click();expect(teacher.get_by_role('heading',name='Command Center',exact=True)).to_be_visible()
        expect(teacher.get_by_text('Opdaterer automatisk',exact=True)).to_be_visible(timeout=20000)
        expect(teacher.locator('.center-lab')).to_have_count(1);expect(teacher.locator('.center-lab')).to_contain_text(b['users']['student']['username'])
        student.get_by_role('button',name='⌘ Mit lab',exact=True).click();expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        command("touch /home/cra/center-reset-proof; printf 'CENTER_FILE_%s\\n' CREATED",'CENTER_FILE_CREATED')
        value=command("curl -fsS http://"+b['target_ip']+":8080/; printf 'REAL_HTTP_%s\\n' DONE",'REAL_HTTP_DONE')
        match=re.search(rb'CRA\{[^}\r\n]+\}',value);assert match;old_flag=match[0].decode()
        student.get_by_label('Dit flag',exact=True).fill(old_flag);began=time.monotonic();student.get_by_role('button',name='Validér flag',exact=True).click()
        expect(teacher.get_by_text('Opgavemål valideret · 100 nye point',exact=True)).to_be_visible(timeout=10000);objective_delay=time.monotonic()-began
        expect(teacher.locator('.center-lab')).to_contain_text('1/1 valideret nu · 100 optjente point')
        command("clear; printf 'CENTER_%s\\n' VERIFIED",'CENTER_VERIFIED')
        me=teacher.request.get(base+'/api/v1/auth/me').json();headers={'Origin':base,'X-CRA-CSRF':me['csrf_token']}
        for channel in ['terminal','desktop']:
            assert teacher.request.post(base+'/api/v1/labs/'+b['lab_id']+'/'+channel+'-grants',headers=headers).status==404
        student.get_by_role('button',name='? Bed om hjælp',exact=True).click();student.get_by_label('Hvad har du brug for hjælp til?',exact=True).fill('Jeg har fundet servicen. Hvordan begrunder jeg mit fund?');student.get_by_role('button',name='Send hjælpeanmodning',exact=True).click()
        expect(teacher.get_by_text('Hjælp efterspurgt af deltager',exact=True)).to_be_visible(timeout=10000)
        teacher.get_by_role('button',name='Åbn undervisning og hjælpekø →',exact=True).click();teacher.get_by_label('Svar til eleven',exact=True).fill('Notér kommando, tidspunkt og hvad svaret faktisk viser.');teacher.get_by_role('button',name='Send svar',exact=True).click()
        expect(student.get_by_text('Notér kommando, tidspunkt og hvad svaret faktisk viser.',exact=True)).to_be_visible(timeout=15000)
        teacher.get_by_role('button',name='◎ Command Center',exact=True).click();expect(teacher.get_by_text('Lærer har svaret på hjælp',exact=True)).to_be_visible(timeout=10000)
        teacher.locator('.command-center').screenshot(path=str(out/'command-center-desktop.png'))
        teacher.set_viewport_size({'width':390,'height':844});assert teacher.evaluate('document.documentElement.scrollWidth<=innerWidth');teacher.screenshot(path=str(out/'command-center-mobile.png'),full_page=True);teacher.set_viewport_size({'width':1512,'height':1080})
        # Explicit transport failure; the lab itself remains real and running.
        teacher.route('**/api/v1/command-center/overview',lambda route:route.fulfill(status=503,json={'detail':'synthetic_transport_failure'}))
        expect(teacher.get_by_text('Status ubekræftet',exact=True)).to_be_visible(timeout=10000)
        expect(teacher.get_by_role('button',name='Nulstil miljø',exact=True)).to_be_disabled()
        teacher.unroute('**/api/v1/command-center/overview');expect(teacher.get_by_text('Opdaterer automatisk',exact=True)).to_be_visible(timeout=10000)
        print('PASS: real teacher lab/progression/help/events; objective visible after '+str(round(objective_delay,3))+'s; no teacher shell/desktop takeover; stale transport disables actions; desktop/mobile fit',flush=True)
        student.get_by_role('button',name='⌘ Mit lab',exact=True).click();expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        teacher.get_by_role('button',name='Nulstil miljø',exact=True).click();confirmation=teacher.get_by_role('group',name='Bekræft lærerhandling',exact=True)
        expect(confirmation).to_contain_text('2 VM’er · generation 1');expect(confirmation).to_contain_text(b['users']['student']['username'])
        teacher.get_by_role('button',name='Bekræft nulstil miljø',exact=True).click();expect(teacher.get_by_text('Nulstilling bestilt',exact=True)).to_be_visible(timeout=10000)
        expect(teacher.locator('.center-lab')).to_contain_text('Generation 2',timeout=10000)
        expect(teacher.locator('.center-lab .status-chip')).to_have_text('Klar',timeout=240000)
        expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        command("test ! -e /home/cra/center-reset-proof && printf 'NEW_GENERATION_%s\\n' CLEAN",'NEW_GENERATION_CLEAN')
        command('hostname',b['next_operator'])
        me=student.request.get(base+'/api/v1/auth/me').json();sh={'Origin':base,'X-CRA-CSRF':me['csrf_token'],'Idempotency-Key':str(uuid4())}
        response=student.request.post(base+'/api/v1/labs/'+b['lab_id']+'/objectives/find-service/attempts',headers=sh,data={'generation':2,'flag':old_flag})
        assert response.status==200 and not response.json()['accepted']
        expect(teacher.locator('.center-lab')).to_contain_text('0/1 valideret nu · 100 optjente point')
        assert all(old_flag not in response for response in responses)
        command("touch /home/cra/center-stop-proof; printf 'PERSISTENCE_%s\\n' WRITTEN",'PERSISTENCE_WRITTEN')
        teacher.get_by_role('button',name='Stop og bevar filer',exact=True).click();teacher.get_by_role('button',name='Bekræft stop og bevar filer',exact=True).click();expect(teacher.locator('.center-lab .status-chip')).to_have_text('Stoppet',timeout=90000)
        teacher.get_by_role('button',name='Start miljø',exact=True).click();teacher.get_by_role('button',name='Bekræft start miljø',exact=True).click();expect(teacher.locator('.center-lab .status-chip')).to_have_text('Klar',timeout=180000)
        expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        command("test -f /home/cra/center-stop-proof && printf 'TEACHER_START_%s\\n' PRESERVED",'TEACHER_START_PRESERVED')
        print('PASS: teacher actual reset/start/stop preserves student ownership; gen2 old file/flag denied, 100 points retained, stop/start file retained; no flag in teacher projections',flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
