"""Actual browser polling failure: stale READY must disappear without server reply."""
import json
from pathlib import Path
import sys
import traceback
from playwright.sync_api import sync_playwright,expect

def main():
    credentials=json.load(sys.stdin)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.goto('chrome://sandbox');assert 'You are adequately sandboxed.' in page.locator('body').inner_text()
        page.goto('http://127.0.0.1:8000/')
        page.get_by_label('Brugernavn').fill(credentials['username']);page.get_by_label('Adgangskode').fill(credentials['password'])
        page.get_by_role('button',name='Fortsæt →').click()
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        # Cancel real requests, never replace a response with a fixture.
        page.route('**/api/v1/labs',lambda route:route.abort())
        expect(page.get_by_text('Miljøets tilstand er ikke bekræftet for nylig. Terminalen afventer en ny kontrol.',exact=True)).to_be_visible(timeout=25000)
        assert page.get_by_text('Klar',exact=True).count()==0
        assert page.locator('.terminal-screen').count()==0
        assert page.get_by_role('button',name='Validér flag').count()==0
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        page.screenshot(path=str(folder/'stale-state.png'),full_page=True)
        page.unroute('**/api/v1/labs')
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=20000)
        page.screenshot(path=str(folder/'recovered-state.png'),full_page=True)
        print('PASS: actual failed browser requests expire READY and remove terminal/scoring; real polling and session recover',flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
