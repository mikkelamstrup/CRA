"""Runs as the unprivileged browser user INSIDE the control VM. Secrets on stdin only."""
import json
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright, expect


def main():
    credentials=json.load(sys.stdin)
    folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000})
        page=context.new_page()
        page.goto('chrome://sandbox')
        sandbox=page.locator('body').inner_text()
        for evidence in ['You are adequately sandboxed.','Seccomp-BPF sandbox\tYes','PID namespaces\tYes','Network namespaces\tYes']:
            assert evidence in sandbox
        print('PASS: actual Chromium SUID/namespaces/seccomp sandbox',flush=True)
        page.goto('http://127.0.0.1:8000/')
        page.get_by_label('Brugernavn').fill(credentials['username'])
        page.get_by_label('Adgangskode').fill(credentials['password'])
        page.get_by_role('button',name='Fortsæt →').click()
        expect(page.get_by_role('button',name='Start mission →')).to_be_visible()
        page.screenshot(path=str(folder/'dashboard.png'),full_page=True)
        print('PASS: actual browser login and mission briefing',flush=True)
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
        page.screenshot(path=str(folder/'lab-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(folder/'lab-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.set_viewport_size({'width':1440,'height':1000})
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
        print('FAIL: browser acceptance ('+type(error).__name__+'); no raw exception emitted',flush=True)
        sys.exit(1)
