"""Connection-only classroom check. Never export terminal contents or screenshots."""
import json
import re
import sys
from playwright.sync_api import sync_playwright, expect


def main():
    data = json.load(sys.stdin)
    assert 2 <= len(data['accounts']) <= 20 and len(data['accounts']) == len(data['labs'])
    assert len({a['id'] for a in data['accounts']}) == len(data['accounts'])
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome', chromium_sandbox=True, headless=True)
        pages = []
        for index, account in enumerate(data['accounts'], 1):
            context = browser.new_context(); page = context.new_page()
            page.goto(data['url'])
            page.get_by_label('Brugernavn').fill(account['username'])
            page.get_by_label('Adgangskode').fill(account['password'])
            page.get_by_role('button', name='Fortsæt →').click()
            expect(page.get_by_role('heading', name='Dit forløb', exact=True)).to_be_visible()
            status = page.evaluate("async id=>{const r=await fetch('/api/v1/labs/'+id);const l=await r.json();return [r.status,l.state,l.can_operate]}", data['labs'][account['id']])
            assert status == [200, 'READY', True]
            page.get_by_role('button', name='⌘ Mit lab', exact=True).click()
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible(timeout=45000)
            pages.append(page)
            print('Browser connection ready:', index, 'of', len(data['accounts']), flush=True)
        for page in pages:
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible()
        pages[0].wait_for_timeout(20000)
        for page in pages:
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible()
            page.get_by_role('button', name='Forbind igen', exact=True).click()
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible(timeout=45000)
        for page in pages:
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible()
        foreign = data['labs'][data['accounts'][1]['id']]
        statuses = pages[0].evaluate("""async id=>{const me=await(await fetch('/api/v1/auth/me')).json();return [(await fetch('/api/v1/labs/'+id)).status,(await fetch('/api/v1/labs/'+id+'/terminal-grants',{method:'POST',headers:{'X-CRA-CSRF':me.csrf_token}})).status]}""", foreign)
        assert statuses == [404, 404]
        browser.close()
    print('PASS: restored classroom browser connections, reconnects and foreign-lab denial; no terminal input, files, flags or screenshots collected')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('FAIL:', type(error).__name__)
        sys.exit(1)
