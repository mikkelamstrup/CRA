"""Real two-browser teacher/student acceptance, including actual WS revocation."""
import json
from pathlib import Path
import sys
import time
import traceback
from playwright.sync_api import sync_playwright,expect


def login(page,user,base='http://127.0.0.1:8000'):
    page.goto(base+'/')
    page.get_by_label('Brugernavn').fill(user['username']);page.get_by_label('Adgangskode').fill(user['password']);page.get_by_role('button',name='Fortsæt →').click()
    expect(page.get_by_role('heading',name='Dit forløb',exact=True)).to_be_visible()


def main():
    bundle=json.load(sys.stdin)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        teacher=browser.new_page(viewport={'width':1440,'height':1000});student=browser.new_page(viewport={'width':1366,'height':900})
        teacher.goto('chrome://sandbox');assert 'You are adequately sandboxed.' in teacher.locator('body').inner_text()
        login(teacher,bundle['users']['teacher']);login(student,bundle['users']['student'])
        assert student.get_by_role('button',name='◫ Lærerrum').count()==0
        teacher.get_by_role('button',name='◫ Lærerrum').click()
        teacher.get_by_label('Sessionens navn').fill('Syntetisk undervisning')
        teacher.get_by_role('button',name='Start undervisning',exact=True).click()
        expect(teacher.get_by_role('button',name='Gem session',exact=True)).to_be_visible()
        reloaded=[]
        def slow_reload(route):
            response=route.fetch();teacher.wait_for_timeout(500)
            assert teacher.get_by_role('button',name='Opdatér oversigt').is_disabled()
            assert teacher.get_by_label('Fælles besked').is_disabled()
            teacher.get_by_role('button',name='Opdatér oversigt').evaluate('(button)=>button.click()')
            reloaded.append(True);route.fulfill(response=response)
        teacher.route('**/api/v1/course/days',slow_reload,times=1)
        teacher.get_by_role('button',name='Opdatér oversigt').click()
        expect(teacher.get_by_label('Fælles besked')).to_be_enabled()
        assert len(reloaded)==1
        teacher.get_by_label('Fase',exact=True).select_option('solo')
        teacher.get_by_label('Fælles besked').fill('Arbejd kun i det tildelte scope. <script>window.teacherXss=1</script>')
        teacher.get_by_role('button',name='Gem session',exact=True).click()
        expect(student.get_by_text('Individuel undersøgelse',exact=True)).to_be_visible(timeout=15000)
        assert student.evaluate('window.teacherXss===undefined')
        student.get_by_role('button',name='? Bed om hjælp').click()
        student.get_by_label('Hvad har du brug for hjælp til?').fill('Jeg har prøvet pwd. Hvordan finder jeg den aktuelle mappe?')
        delayed=[]
        def slow_post(route):
            if route.request.method!='POST':route.continue_();return
            response=route.fetch();student.wait_for_timeout(500)
            assert student.get_by_label('Hvad har du brug for hjælp til?').is_disabled()
            delayed.append(True);route.fulfill(response=response)
        student.route('**/api/v1/teaching/help',slow_post)
        student.get_by_role('button',name='Send hjælpeanmodning').click()
        expect(student.get_by_text('AFVENTER LÆRER',exact=True)).to_be_visible();assert delayed
        student.unroute('**/api/v1/teaching/help',slow_post)
        teacher.get_by_role('button',name='Opdatér oversigt').click()
        teacher.get_by_label('Svar til eleven').fill('Læs outputtet fra pwd og sammenlign med prompten.')
        # Refresh after a concurrent server change must retain the reply draft.
        teacher.get_by_label('Fælles besked').fill('Nyt fælles spor')
        teacher.get_by_role('button',name='Gem session',exact=True).click()
        expect(teacher.get_by_label('Svar til eleven')).to_have_value('Læs outputtet fra pwd og sammenlign med prompten.')
        teacher.get_by_role('button',name='Send svar',exact=True).click()
        expect(student.get_by_text('Læs outputtet fra pwd og sammenlign med prompten.',exact=True)).to_be_visible(timeout=15000)
        student.get_by_role('button',name='⌘ Mit lab').click()
        expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        teacher.get_by_label('Elevterminaler og skriveborde åbne').uncheck();began=time.monotonic()
        teacher.get_by_role('button',name='Gem session',exact=True).click()
        expect(student.get_by_text('Forbindelsen er lukket.',exact=False)).to_be_visible(timeout=12000)
        elapsed=round(time.monotonic()-began,3)
        # Server refuses a fresh grant while VM remains READY.
        status=student.evaluate("""async()=>{const me=await(await fetch('/api/v1/auth/me')).json();const labs=await(await fetch('/api/v1/labs')).json();const lab=labs.find(l=>l.state!=='DESTROYED');if(lab.state!=='READY')throw Error('VM unexpectedly stopped');return(await fetch('/api/v1/labs/'+lab.id+'/terminal-grants',{method:'POST',headers:{'X-CRA-CSRF':me.csrf_token}})).status;}""")
        assert status==403
        teacher.get_by_label('Elevterminaler og skriveborde åbne').check();teacher.get_by_role('button',name='Gem session',exact=True).click()
        expect(student.get_by_text('Elevadgang spærret · processer kører videre',exact=True)).not_to_be_visible(timeout=15000)
        student.get_by_role('button',name='Genopret forbindelse',exact=True).click()
        expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        teacher.get_by_role('button',name='Markér hjulpet',exact=True).click()
        expect(teacher.get_by_text('Ingen åbne hjælpeanmodninger.',exact=True)).to_be_visible()
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        teacher.screenshot(path=str(folder/'teaching-desktop.png'),full_page=True)
        teacher.set_viewport_size({'width':390,'height':844});assert teacher.evaluate('document.documentElement.scrollWidth<=innerWidth');teacher.screenshot(path=str(folder/'teaching-mobile.png'),full_page=True)
        teacher.once('dialog',lambda d:d.accept());teacher.get_by_role('button',name='Afslut undervisning',exact=True).click()
        expect(teacher.get_by_role('button',name='Start undervisning',exact=True)).to_be_visible()
        print('PASS: teacher/student browser phases/help, protected drafts, actual terminal revocation '+str(elapsed)+'s and reconnect',flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
