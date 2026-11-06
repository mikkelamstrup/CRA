"""Real graphical operator acceptance; all terminal/desktop bytes are actual VM output."""
import json
import re
from pathlib import Path
import sys
import time
import traceback
from playwright.sync_api import sync_playwright,expect
from teaching import login


def main():
    b=json.load(sys.stdin);base=b.get('url','http://127.0.0.1:8000');resume=b.get('resume',False)
    proof_id=b.get('proof_id','');assert not proof_id or re.fullmatch('[0-9a-f]{12}',proof_id)
    suffix='-'+proof_id if proof_id else ''
    artifacts={
        '/home/cra/gui-browser-proof':'/home/cra/gui-browser-proof'+suffix,
        '/home/cra/.config/cra-chrome/Default/cra-profile-proof':'/home/cra/.config/cra-chrome/Default/cra-profile-proof'+suffix,
        'gui-proof.pcapng':'gui-proof'+suffix+'.pcapng',
        'gui-capture.log':'gui-capture'+suffix+'.log',
        'gui-proof.html':'gui-proof'+suffix+'.html',
        'LAB_BROWSER_WORKS':'LAB_BROWSER_WORKS'+suffix,
    }
    def scoped(value):
        for before,after in artifacts.items():value=value.replace(before,after)
        return value

    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        pages=[browser.new_page(viewport={'width':1440,'height':1050}) for _ in range(2)];teacher,student=pages;outputs=[bytearray(),bytearray()]
        for i,page in enumerate(pages):
            page.add_init_script("window.craClipboardCalls=0;Object.defineProperty(navigator,'clipboard',{value:{readText:async()=>{window.craClipboardCalls++;return 'synthetic clipboard'},writeText:async()=>{window.craClipboardCalls++}}});")
            def watch(socket,i=i):
                if '/terminal/' not in socket.url:return
                def received(frame):
                    if isinstance(frame,bytes):
                        outputs[i].extend(frame)
                        if len(outputs[i])>262144:del outputs[i][:-131072]
                socket.on('framereceived',received)
            page.on('websocket',watch)
        def command(i,value,marker,timeout=25):
            value=scoped(value)
            if marker not in b['operators']:
                # Fresh proofs for every retry; complete markers are never typed.
                value=re.sub(r'\b(CONFIRMED|VERIFIED|WRITTEN|SEPARATE|STARTED|RESPONSIVE|CONTINUES|EXISTS|OPEN|READY|RESUMED|RUNNING)\b',lambda match:match[0]+suffix,value)
                marker+=suffix
            assert marker not in value
            outputs[i].clear();page=pages[i];page.locator('.xterm-helper-textarea').focus();page.keyboard.type(value,delay=1);page.keyboard.press('Enter');until=time.monotonic()+timeout
            while marker.encode() not in outputs[i]:
                if time.monotonic()>until:
                    print(json.dumps({'missing_marker':marker,'terminal_status':page.locator('.terminal-status').inner_text(),'desktop_closed':page.get_by_text('Skrivebordets forbindelse er lukket.',exact=True).count()>0,'permission_denied':b'Permission denied' in outputs[i],'file_missing':b'No such file' in outputs[i]}),flush=True)
                    folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
                    page.locator('.desktop-panel').screenshot(path=str(folder/('desktop-failure'+suffix+'.png')))
                    raise TimeoutError('Real terminal evidence absent')
                page.wait_for_timeout(100)
        def save_access(enabled):
            teacher.get_by_label('Elevterminaler og skriveborde åbne',exact=True).set_checked(enabled)
            with teacher.expect_response(lambda response: response.request.method=='PUT' and '/api/v1/teaching/sessions/' in response.url) as saved:
                teacher.get_by_role('button',name='Gem session',exact=True).click()
            assert saved.value.status==200 and saved.value.json()['terminal_enabled'] is enabled
            assert teacher.request.get(base+'/api/v1/teaching/session').json()['terminal_enabled'] is enabled
            expect(teacher.get_by_role('button',name='Gem session',exact=True)).to_be_disabled()

        for page,role in zip(pages,('teacher','student')):
            login(page,b['users'][role],base);page.get_by_role('button',name='◇ Mit hold',exact=True).click()
            expect(page.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
            page.get_by_role('button',name='Åbn skrivebord',exact=True).click()
            expect(page.get_by_text('Forbundet · grafisk Linux-skrivebord',exact=True)).to_be_visible(timeout=30000)
        print('PASS: two real noVNC desktops and two independent Linux terminals connected simultaneously',flush=True)
        command(0,'hostname',b['operators'][0]);command(1,'hostname',b['operators'][1])
        if resume or b.get('reset'):
            condition='!' if b.get('reset') else ''
            command(1,"test "+condition+" -f /home/cra/gui-browser-proof && test -d /home/cra/.config/cra-chrome/Default && printf 'GUI_%s\\n' RESUMED",'GUI_RESUMED')
            check="test ! -e /home/cra/.config/cra-chrome/Default/cra-profile-proof" if b.get('reset') else "grep -qx synthetic-profile-preserved /home/cra/.config/cra-chrome/Default/cra-profile-proof"
            command(1,check+" && printf PROFILE_STATE_%s VERIFIED",'PROFILE_STATE_VERIFIED')
            print('PASS: actual graphical reconnect after '+('generation reset with old files absent' if b.get('reset') else 'VM stop/start with files and browser profile retained'),flush=True);browser.close();return
        # Keyboard input goes through RFB to an actual xterm on the student's own guest.
        student.get_by_role('button',name='Åbn grafisk terminal',exact=True).click();student.wait_for_timeout(1200)
        canvas=student.locator('.desktop-screen canvas');canvas.focus()
        student.keyboard.type(scoped('touch /home/cra/gui-browser-proof; sync'),delay=10);student.keyboard.press('Enter')
        command(1,"test -f /home/cra/gui-browser-proof && printf 'GUI_INPUT_%s\\n' CONFIRMED",'GUI_INPUT_CONFIRMED')
        command(1,"echo synthetic-profile-preserved > /home/cra/.config/cra-chrome/Default/cra-profile-proof && printf PROFILE_MARKER_%s WRITTEN",'PROFILE_MARKER_WRITTEN')
        command(0,"test ! -f /home/cra/gui-browser-proof && printf 'OTHER_DESKTOP_%s\\n' SEPARATE",'OTHER_DESKTOP_SEPARATE')
        student.get_by_role('button',name='Åbn labbrowser',exact=True).click()
        command(1,"for i in $(seq 1 100); do title=$(DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool getactivewindow getwindowname); case \"$title\" in *'CRA · Grafisk lab'*) printf 'BROWSER_WINDOW_%s\\n' READY; break;; esac; sleep .1; done",'BROWSER_WINDOW_READY')
        command(1,"dumpcap -i lab0 -a duration:12 -w /home/cra/gui-proof.pcapng >/home/cra/gui-capture.log 2>&1 & capture_pid=$!; sleep 0.5; kill -0 \"$capture_pid\" && printf 'CAPTURE_%s\\n' STARTED",'CAPTURE_STARTED')
        canvas.focus();student.keyboard.press('Control+l',delay=80);student.wait_for_timeout(500);student.keyboard.type('http://'+b['target_ip']+'/'+scoped('gui-proof.html'),delay=20);student.keyboard.press('Enter')
        command(1,"for i in $(seq 1 100); do if DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool search --name LAB_BROWSER_WORKS >/dev/null; then printf 'REAL_BROWSER_%s\\n' CONFIRMED; break; fi; sleep .1; done",'REAL_BROWSER_CONFIRMED')
        assert student.evaluate('window.guiTargetProbe===undefined && window.craClipboardCalls===0')
        began=time.monotonic();command(1,"printf 'TERMINAL_WITH_GUI_%s\\n' RESPONSIVE",'TERMINAL_WITH_GUI_RESPONSIVE');latency=time.monotonic()-began
        assert latency<3
        student.get_by_role('button',name='Forbind skrivebord igen',exact=True).click()
        expect(student.get_by_text('Forbundet · grafisk Linux-skrivebord',exact=True)).to_be_visible(timeout=30000)
        command(1,"printf 'SAME_TERMINAL_%s\\n' CONTINUES",'SAME_TERMINAL_CONTINUES')
        student.wait_for_timeout(12000)
        command(1,"test -s /home/cra/gui-proof.pcapng && printf 'CAPTURE_FILE_%s\\n' EXISTS",'CAPTURE_FILE_EXISTS')
        student.get_by_role('button',name='Åbn Wireshark',exact=True).click();student.wait_for_timeout(1800);canvas.focus();student.keyboard.press('Control+o');student.wait_for_timeout(500);student.keyboard.type(scoped('/home/cra/gui-proof.pcapng'),delay=4);student.keyboard.press('Enter');student.wait_for_timeout(1200)
        command(1,"for i in $(seq 1 100); do if DISPLAY=:0 XAUTHORITY=/home/cra/.Xauthority xdotool search --name gui-proof.pcapng >/dev/null; then printf 'WIRESHARK_%s\\n' OPEN; break; fi; sleep .1; done",'WIRESHARK_OPEN')
        command(0,"clear; printf 'GUI_%s\\n' VERIFIED",'GUI_VERIFIED');command(1,"clear; printf 'GUI_%s\\n' VERIFIED",'GUI_VERIFIED')
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        canvas.screenshot(path=str(folder/'desktop-wireshark.png'))
        student.locator('.desktop-panel').screenshot(path=str(folder/'desktop-panel.png'))
        student.set_viewport_size({'width':390,'height':844});assert student.evaluate('document.documentElement.scrollWidth<=innerWidth');student.locator('.desktop-panel').screenshot(path=str(folder/'desktop-mobile.png'));student.set_viewport_size({'width':1440,'height':1050})
        assert all(page.evaluate('window.craClipboardCalls===0') for page in pages)
        print('PASS: actual RFB keyboard creates only own guest file; Chromium renders isolated target HTML; Wireshark opens real capture; terminal latency '+str(round(latency,3))+'s; desktop reconnect preserves terminal',flush=True)
        teacher.get_by_role('button',name='◫ Lærerrum',exact=True).click();teacher.get_by_label('Sessionens navn',exact=True).fill('Grafisk syntetisk undervisning');teacher.get_by_role('button',name='Start undervisning',exact=True).click()
        expect(teacher.get_by_label('Elevterminaler og skriveborde åbne',exact=True)).to_be_visible()
        began=time.monotonic();save_access(False)
        expect(student.get_by_text('Skrivebordets forbindelse er lukket.',exact=True)).to_be_visible(timeout=12000)
        expect(student.get_by_text('Forbindelsen er lukket.',exact=False)).to_be_visible(timeout=12000)
        elapsed=time.monotonic()-began;assert elapsed<10
        me=student.request.get(base+'/api/v1/auth/me').json()
        assert student.request.post(base+'/api/v1/labs/'+b['lab_id']+'/desktop-grants',headers={'X-CRA-CSRF':me['csrf_token'],'Origin':base}).status==403
        assert student.request.get(base+'/api/v1/labs/'+b['lab_id']).json()['state']=='READY'
        expect(student.get_by_text('Elevadgang spærret · processer kører videre',exact=True)).to_be_visible(timeout=15000)
        save_access(True)
        expect(student.get_by_text('Elevadgang spærret · processer kører videre',exact=True)).not_to_be_visible(timeout=15000)
        student.get_by_role('button',name='Genopret forbindelse',exact=True).click();student.get_by_role('button',name='Forbind skrivebord igen',exact=True).click()
        try:
            expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=30000)
            expect(student.get_by_text('Forbundet · grafisk Linux-skrivebord',exact=True)).to_be_visible(timeout=30000)
        except Exception:
            print(json.dumps({'terminal_status':student.locator('.terminal-status').inner_text(),'desktop_status':student.locator('.desktop-panel p[role=status]').inner_text()}),flush=True)
            raise
        command(1,"printf 'AFTER_PAUSE_%s\\n' RUNNING",'AFTER_PAUSE_RUNNING')
        print('PASS: teacher pause revokes actual desktop and terminal in '+str(round(elapsed,3))+'s without stopping VMs; resume works',flush=True)
        browser.close()

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('FAIL: '+type(e).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(e.__traceback__)),flush=True);sys.exit(1)
