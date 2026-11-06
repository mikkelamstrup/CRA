"""Connection-only classroom check. Never export terminal contents or screenshots."""
import json
import re
import sys
import traceback
from playwright.sync_api import sync_playwright, expect

STAGE = 'initialization'


def main():
    global STAGE
    data = json.load(sys.stdin)
    assert sorted(int(a['username'][4:]) for a in data['accounts'] if re.fullmatch(r'elev[0-9]+', a['username'])) == list(range(1, 19))
    assert len(data['accounts']) == 18 and len(data['labs']) == 18
    active_indices = set(data.get('active_account_indices', []))
    assert all(type(i) is int and 1 <= i <= 18 for i in active_indices)
    expected_connections = 18 - len(active_indices)
    assert expected_connections >= 2
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome', chromium_sandbox=True, headless=True)
        pages = []
        desktops = 0
        deferred_notices = 0
        for index, account in enumerate(data['accounts'], 1):
            if index in active_indices:
                print('Existing active student session left untouched at account index:', index, flush=True)
                continue
            STAGE = f'account {index}: login'
            context = browser.new_context(); page = context.new_page()
            page.goto(data['url'])
            page.get_by_label('Brugernavn').fill(account['username'])
            page.get_by_label('Adgangskode').fill(account['password'])
            page.get_by_role('button', name='Fortsæt →').click()
            expect(page.get_by_role('heading', name='Dit forløb', exact=True)).to_be_visible()
            status = page.evaluate("async id=>{const r=await fetch('/api/v1/labs/'+id);const l=await r.json();return [r.status,l.state,l.can_operate,l.desktop_enabled]}", data['labs'][account['id']])
            assert status[:3] == [200, 'READY', True]
            # An unread teacher notice can cover navigation. Close it only in this
            # test DOM; do not acknowledge it or change the student's notification.
            pending_notice = page.evaluate("async()=>Boolean(await(await fetch('/api/v1/assignments/alert')).json())")
            if pending_notice:
                STAGE = f'account {index}: defer unread notice locally'
                notice = page.locator('dialog.incident-dispatch')
                expect(notice).to_be_visible(timeout=15000)
                notice.evaluate('(dialog)=>dialog.close()')
                deferred_notices += 1
            STAGE = f'account {index}: terminal'
            page.get_by_role('button', name='⌘ Mit lab', exact=True).click()
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible(timeout=45000)
            if status[3]:
                STAGE = f'account {index}: desktop connection'
                page.get_by_role('button', name='Åbn skrivebord', exact=True).click()
                expect(page.get_by_text('Forbundet · grafisk Linux-skrivebord', exact=True)).to_be_visible(timeout=45000)
                page.get_by_role('button', name='Luk skrivebord', exact=True).click()
                desktops += 1
            pages.append(page)
            print('Browser connection ready:', index, 'of 18', flush=True)
        assert len(pages) == expected_connections
        for index, page in enumerate(pages, 1):
            STAGE = f'account {index}: initial simultaneous stability'
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible()
        pages[0].wait_for_timeout(20000)
        for index, page in enumerate(pages, 1):
            STAGE = f'account {index}: reconnect'
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible()
            page.get_by_role('button', name='Forbind igen', exact=True).click()
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible(timeout=45000)
        for index, page in enumerate(pages, 1):
            STAGE = f'account {index}: final simultaneous stability'
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible()
        checked_accounts = [a for i, a in enumerate(data['accounts'], 1) if i not in active_indices]
        foreign = data['labs'][checked_accounts[1]['id']]
        STAGE = 'foreign lab denial'
        statuses = pages[0].evaluate("""async id=>{const me=await(await fetch('/api/v1/auth/me')).json();return [(await fetch('/api/v1/labs/'+id)).status,(await fetch('/api/v1/labs/'+id+'/terminal-grants',{method:'POST',headers:{'X-CRA-CSRF':me.csrf_token}})).status]}""", foreign)
        assert statuses == [404, 404]
        if 'expected_desktops' in data:
            assert desktops == data['expected_desktops']
        browser.close()
    print('PASS: actual graphical desktop connections:', desktops, 'with no guest keyboard/mouse input')
    print('Unread notices closed only in acceptance DOM, without acknowledgement:', deferred_notices)
    print('PASS:', expected_connections, 'simultaneous classroom browser connections, reconnects and foreign-lab denial; no terminal input, files, flags or screenshots collected')
    print('Existing active student sessions excluded from automation:', len(active_indices))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('FAIL:', type(error).__name__, 'stage:', STAGE, 'lines:', [f.lineno for f in traceback.extract_tb(error.__traceback__) if f.filename == __file__])
        sys.exit(1)
