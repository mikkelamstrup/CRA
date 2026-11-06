"""Open actual course evidence/tools through the guest desktop's RFB connection."""
import json
from pathlib import Path
import sys
import time
import traceback
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect
from teaching import login


def main():
    b=json.load(sys.stdin);output=bytearray()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1050})
        def watch(ws):
            if '/terminal/' not in ws.url:return
            def received(frame):
                if isinstance(frame,bytes):
                    output.extend(frame)
                    if len(output)>262144:del output[:-131072]
            ws.on('framereceived',received)
        page.on('websocket',watch)
        login(page,b['users']['student']);page.get_by_role('button',name='⌘ Mit lab',exact=True).click()
        expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
        page.get_by_role('button',name='Åbn skrivebord',exact=True).click()
        expect(page.get_by_text('Forbundet · grafisk Linux-skrivebord',exact=True)).to_be_visible(timeout=30000)
        canvas=page.locator('.desktop-screen canvas')
        if b['mission']=='packet-hunter':
            page.get_by_role('button',name='Åbn Wireshark',exact=True).click();page.wait_for_timeout(2000)
            canvas.focus();page.keyboard.press('Control+o');page.wait_for_timeout(500)
            page.keyboard.type('/home/cra/academy/capture/incident.pcap',delay=3);page.keyboard.press('Enter');page.wait_for_timeout(1800)
            title='incident.pcap'
        else:
            page.get_by_role('button',name='Åbn labbrowser',exact=True).click();page.wait_for_timeout(1000)
            canvas.focus();page.keyboard.press('Control+l');page.keyboard.type('http://'+b['target_ip']+':8081/',delay=3);page.keyboard.press('Enter');page.wait_for_timeout(2000)
            # Real guest form and cookie jar; verify the rendered authenticated response.
            page.keyboard.press('Tab');page.keyboard.press('Tab');page.keyboard.type('nordlys-training-only');page.keyboard.press('Enter');page.wait_for_timeout(1000)
            canvas.focus();page.keyboard.press('Escape');page.keyboard.press('Control+l');page.keyboard.type('http://'+b['target_ip']+':8081/api/reports?id=702');page.keyboard.press('Enter');page.wait_for_timeout(1000)
            proof_title='GUI_AUTH_'+uuid4().hex
            # Verify the actual rendered JSON in the guest's own DevTools, using RFB keys.
            # The title is changed only if this browser's cookie authenticated report 702.
            # Host browsers reserve Ctrl+Shift+J. Invoke that guest shortcut through
            # the student's real terminal, then type into the guest console via RFB.
            output.clear();page.locator('.xterm-helper-textarea').focus()
            page.keyboard.type("DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool key --clearmodifiers ctrl+shift+j; printf 'DEVTOOLS_%s\\n' OPENED");page.keyboard.press('Enter')
            end=time.monotonic()+10
            while b'DEVTOOLS_OPENED' not in output:
                if time.monotonic()>end:raise TimeoutError('Guest shortcut missing')
                page.wait_for_timeout(100)
            page.wait_for_timeout(1000);canvas.focus()
            page.keyboard.type("(()=>{const r=JSON.parse(document.querySelector('pre').textContent);if(r.owner!=='administrator'||!/CRA[{][0-9a-f]{32}[}]/.test(r.body))throw Error('response not authenticated');document.title='"+proof_title+"';})()",delay=2)
            page.keyboard.press('Enter');page.wait_for_timeout(500)
            output.clear();page.locator('.xterm-helper-textarea').focus()
            page.keyboard.type("DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool search --name "+proof_title+" >/dev/null && printf 'GUI_APP_%s\\n' VERIFIED");page.keyboard.press('Enter')
            end=time.monotonic()+15
            while b'GUI_APP_VERIFIED' not in output:
                if time.monotonic()>end:raise TimeoutError('Authenticated GUI response missing')
                page.wait_for_timeout(100)
            # The authenticated report contains a private exercise flag. Retain no image.
            browser.close()
            print('PASS: actual RFB login and authenticated administrator report in guest browser',flush=True)
            return
        output.clear();page.locator('.xterm-helper-textarea').focus()
        page.keyboard.type('DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool search --name '+title+" >/dev/null && printf 'ADV_GUI_%s\\n' CONFIRMED")
        page.keyboard.press('Enter');end=time.monotonic()+15
        while b'ADV_GUI_CONFIRMED' not in output:
            if time.monotonic()>end:raise TimeoutError('Guest GUI evidence missing')
            page.wait_for_timeout(100)
        canvas.screenshot(path='/var/lib/cra-browser/acceptance/'+b['mission']+'-gui.png')
        browser.close()
    print('PASS: actual RFB opens course tool in guest:',b['mission'],flush=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL:',type(e).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
