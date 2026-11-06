"""Actual UI recovery of an unexpectedly stopped VM, preserving its disk."""
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
        output=bytearray()
        page.on('websocket',lambda ws:ws.on('framereceived',lambda frame:output.extend(frame) if isinstance(frame,bytes) else None))
        page.goto('http://127.0.0.1:8000/')
        page.get_by_label('Brugernavn').fill(credentials['username']);page.get_by_label('Adgangskode').fill(credentials['password'])
        page.get_by_role('button',name='Fortsæt →').click()
        expect(page.get_by_role('button',name='Genstart miljø',exact=True)).to_be_visible(timeout=15000)
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        page.screenshot(path=str(folder/'vm-stopped.png'),full_page=True)
        page.get_by_role('button',name='Genstart miljø',exact=True).click()
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=180000)
        page.locator('.xterm-helper-textarea').focus()
        page.keyboard.type("test \"$(cat /home/cra/recovery-proof)\" = synthetic-recovery && printf 'RECOVERY_%s\\n' VERIFIED")
        page.keyboard.press('Enter');end=time.monotonic()+20
        while b'RECOVERY_VERIFIED' not in output:
            if time.monotonic()>end:raise TimeoutError('Disk recovery proof absent')
            page.wait_for_timeout(100)
        page.screenshot(path=str(folder/'vm-recovered.png'),full_page=True)
        print('PASS: actual browser Genstart miljø returns same generation to READY with persisted disk file',flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
