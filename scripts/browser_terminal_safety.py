"""Actual xterm handling of hostile terminal bytes; synthetic identity via stdin."""
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
        context=browser.new_context(viewport={'width':1440,'height':1000})
        context.add_init_script("window.craClipboardCalls=0; const oldExec=document.execCommand.bind(document); document.execCommand=(name,...args)=>{if(['copy','cut','paste'].includes(name.toLowerCase())){window.craClipboardCalls++;return false;}return oldExec(name,...args);}; for (const name of ['read','readText','write','writeText']) { if(navigator.clipboard) Object.defineProperty(navigator.clipboard,name,{value:()=>{window.craClipboardCalls++;throw new Error('synthetic clipboard guard')}}); }")
        page=context.new_page();output=bytearray()
        page.on('websocket',lambda ws:ws.on('framereceived',lambda frame:output.extend(frame) if isinstance(frame,bytes) else None))
        page.goto('chrome://sandbox');assert 'You are adequately sandboxed.' in page.locator('body').inner_text()
        page.goto('http://127.0.0.1:8000/')
        page.get_by_label('Brugernavn').fill(credentials['username']);page.get_by_label('Adgangskode').fill(credentials['password'])
        page.get_by_role('button',name='Fortsæt →').click()
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        page.locator('.xterm-helper-textarea').focus()
        command='''python3 -c 'import sys;sys.stdout.write("<img src=x onerror=window.craInjected=1><script>window.craInjected=2</script>\\n\\x1b]52;c;c3ludGhldGlj\\x07\\x1b]52;c;?\\x07");print("SAFETY_"+"DONE")' '''
        page.keyboard.type(command);page.keyboard.press('Enter')
        deadline=time.monotonic()+20
        while b'SAFETY_DONE' not in output:
            if time.monotonic()>deadline:raise TimeoutError('Terminal evidence missing')
            page.wait_for_timeout(100)
        page.wait_for_timeout(500)
        assert b'<img src=x onerror=window.craInjected=1><script>window.craInjected=2</script>' in output
        osc_arrived=b'\x1b]52;' in output
        assert page.evaluate('window.craInjected === undefined && window.craClipboardCalls === 0')
        assert page.locator('.terminal-screen img,.terminal-screen script').count()==0
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        page.screenshot(path=str(folder/'terminal-safety.png'),full_page=True)
        print('PASS: actual sandboxed browser displays hostile HTML as text; no clipboard access through complete terminal path; raw OSC52 received='+str(osc_arrived),flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
