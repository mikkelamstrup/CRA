"""Installed public UI acceptance with synthetic users only; never access classroom pages."""
import json
import sys
import time
from playwright.sync_api import sync_playwright, expect


def api(page, path, body=None, expected=200):
    result=page.evaluate('''async x=>{const me=await(await fetch('/api/v1/auth/me')).json();
const r=await fetch('/api/v1'+x.path,{method:x.body===null?'GET':'POST',headers:{'Content-Type':'application/json','X-CRA-CSRF':me.csrf_token,'Idempotency-Key':crypto.randomUUID()},...(x.body===null?{}:{body:JSON.stringify(x.body)})});return {status:r.status,value:await r.json()};}''',{'path':path,'body':body})
    assert result['status']==expected
    return result['value']


def main():
    data=json.load(sys.stdin)
    assert all(u['username'].startswith('cra_test_') for u in data['users'].values())
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        check=browser.new_page();check.goto('chrome://sandbox')
        assert 'You are adequately sandboxed.' in check.locator('body').inner_text();check.close()
        pages={}
        output=bytearray()
        for role,user in data['users'].items():
            page=browser.new_context(viewport={'width':1440,'height':1000}).new_page()
            if role=='student':
                def watch(ws):
                    def received(frame):
                        if isinstance(frame,bytes):
                            output.extend(frame)
                            if len(output)>262144:del output[:-131072]
                    ws.on('framereceived',received)
                page.on('websocket',watch)
            page.goto(data['url'])
            page.get_by_label('Brugernavn').fill(user['username'])
            page.get_by_label('Adgangskode').fill(user['password'])
            page.get_by_role('button',name='Fortsæt →',exact=True).click()
            expect(page.get_by_role('heading',name='Dit forløb',exact=True)).to_be_visible()
            pages[role]=page
        teacher,student,other=[pages[k] for k in ('teacher','student','other')]
        if data['phase']=='dispatch':
            expect(student.get_by_role('button',name='Åbn opgave 9',exact=True)).to_be_disabled()
            assert api(student,'/assignments/alert') is None
            student.get_by_role('button',name='⌘ Mit lab',exact=True).click()
            teacher.get_by_role('button',name='◎ Command Center',exact=True).click()
            teacher.get_by_label('Vælg elev',exact=True).select_option(data['users']['student']['user_id'])
            teacher.get_by_role('button',name='Frigiv opgave 9 til '+data['users']['student']['username'],exact=True).click()
            modal=student.get_by_role('dialog')
            expect(modal).to_be_visible(timeout=15000)
            expect(modal).to_contain_text('Nordtele er under angreb')
            assert api(other,'/assignments/alert') is None
            expect(other.get_by_role('button',name='Åbn opgave 9',exact=True)).to_be_disabled()
            api(other,'/labs',{'mission_id':'cybercon-incident','mission_version':1},403)
            student.reload();expect(modal).to_be_visible()
            modal.get_by_role('button',name='Modtag sagen og åbn opgaven',exact=True).click()
            expect(modal).not_to_be_visible()
            assert api(student,'/assignments/alert') is None
            student.get_by_role('button',name='Start mission →',exact=True).click()
            expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=900000)
            labs=[x for x in api(student,'/labs') if x['state']!='DESTROYED']
            assert len(labs)==1 and labs[0]['mission_id']=='cybercon-incident' and labs[0]['state']=='READY'
            lab=labs[0]
            student.locator('.xterm-helper-textarea').focus()
            command="curl --fail --max-time 5 -s http://"+lab['target_ip']+":8081/ >/dev/null && printf 'CYBERCON_%s\\n' CONNECTED"
            output.clear();student.keyboard.insert_text(command);student.keyboard.press('Enter')
            deadline=time.monotonic()+30
            while b'CYBERCON_CONNECTED' not in output:
                if time.monotonic()>deadline:raise TimeoutError('Synthetic real shell probe absent')
                student.wait_for_timeout(100)
            print('PASS: actual HTTPS teacher release, student modal in lab, reload/persistent ack, other student denied, new mission start and real browser shell',flush=True)
        else:
            student.get_by_role('button',name='⌘ Mit lab',exact=True).click()
            expect(student.get_by_text('Forbundet · rigtig Linux-shell',exact=True)).to_be_visible(timeout=45000)
            assert api(student,'/assignments/alert') is None
            student.get_by_label('Dit flag',exact=True).fill(data['flag'])
            student.get_by_role('button',name='Validér flag',exact=True).click()
            expect(student.get_by_text('Flaget er korrekt. Du har optjent 500 point.',exact=True)).to_be_visible()
            mission=next(a for a in api(student,'/assignments') if a['mission_id']=='cybercon-incident')
            assert mission['completed'] and mission['earned_points']==500
            print('PASS: actual HTTPS submission awards 500 points; persistent acknowledgement and completed assignment',flush=True)
        browser.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        import traceback
        print('FAIL:',type(error).__name__,'lines',','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True)
        sys.exit(1)
