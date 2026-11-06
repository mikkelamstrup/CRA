"""Real browser acceptance for teaching content and private notebooks; no fixtures."""
import json
from pathlib import Path
import sys
import time
import traceback
from playwright.sync_api import sync_playwright,expect

def main():
    credentials=json.load(sys.stdin)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.goto('chrome://sandbox');assert 'You are adequately sandboxed.' in page.locator('body').inner_text()
        page.goto('http://127.0.0.1:8000/')
        page.get_by_label('Brugernavn').fill(credentials['username']);page.get_by_label('Adgangskode').fill(credentials['password']);page.get_by_role('button',name='Fortsæt →').click()
        expect(page.get_by_role('heading',name='Dit forløb',exact=True)).to_be_visible()
        expect(page.get_by_label('Mine observationer')).to_be_visible()
        assert page.get_by_text('Lærerens forberedelse',exact=True).count()==0
        # Real concurrent writer causes a conflict; only delay the real reload response.
        page.evaluate("""async()=>{const me=await(await fetch('/api/v1/auth/me')).json();const r=await fetch('/api/v1/course/days/1/notebook',{method:'PUT',headers:{'Content-Type':'application/json','X-CRA-CSRF':me.csrf_token},body:JSON.stringify({revision:0,body:'Concurrent writer',reflection:'',theory_read:false})});if(!r.ok)throw Error('Concurrent edit failed');}""")
        page.get_by_label('Mine observationer').fill('Stale local edit')
        page.get_by_role('button',name='Gem notesbog',exact=True).click()
        expect(page.get_by_text('Noterne er ændret i et andet vindue.',exact=False)).to_be_visible()
        checked=[]
        def delayed_reload(route):
            response=route.fetch()
            page.wait_for_timeout(500)
            assert page.get_by_label('Mine observationer').is_disabled()
            assert page.get_by_role('button',name='Gem notesbog',exact=True).is_disabled()
            checked.append(True)
            route.fulfill(response=response)
        page.route('**/api/v1/course/days/1/notebook',delayed_reload,times=1)
        page.once('dialog',lambda dialog:dialog.accept())
        page.get_by_role('button',name='Hent gemt version',exact=True).click()
        expect(page.get_by_label('Mine observationer')).to_have_value('Concurrent writer')
        expect(page.get_by_label('Mine observationer')).to_be_enabled()
        assert checked
        page.get_by_label('Mine observationer').fill('Syntetisk kilde: journal/events.log. Observation: NL-104. <script>window.courseXss=1</script>')
        page.get_by_label('Min refleksion',exact=True).fill('grep søger i indhold; find søger filnavne. Jeg skal efterprøve kilden.')
        page.get_by_label('Jeg har læst teorien').check()
        page.get_by_role('button',name='Gem notesbog',exact=True).click()
        expect(page.get_by_text('Dine noter er gemt.',exact=True)).to_be_visible()
        page.reload();expect(page.get_by_label('Mine observationer')).to_have_value('Syntetisk kilde: journal/events.log. Observation: NL-104. <script>window.courseXss=1</script>')
        assert page.evaluate('window.courseXss===undefined')
        # A distinct keyboard/focus path and mobile layout use the real responses.
        page.keyboard.press('Tab')
        assert page.evaluate('document.activeElement !== document.body')
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        page.screenshot(path=str(folder/'course-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(folder/'course-mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_role('button',name='Åbn dagens lab →',exact=True).click()
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        data=bytearray();page.on('websocket',lambda ws:ws.on('framereceived',lambda f:data.extend(f) if isinstance(f,bytes) else None))
        # Existing socket predates listener: reconnect without losing the real shell.
        page.get_by_role('button',name='Forbind igen',exact=True).click()
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        page.locator('.xterm-helper-textarea').focus()
        page.keyboard.type("test -f /home/cra/academy/README.txt && printf 'COURSE_%s\\n' VERIFIED");page.keyboard.press('Enter')
        end=time.monotonic()+20
        while b'COURSE_VERIFIED' not in data:
            if time.monotonic()>end:raise TimeoutError('Real filesystem proof absent')
            page.wait_for_timeout(100)
        page.screenshot(path=str(folder/'course-lab.png'),full_page=True)
        browser.close()
        print('PASS: actual browser course, persisted notebook, safe text, keyboard/mobile and real Linux terminal',flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
