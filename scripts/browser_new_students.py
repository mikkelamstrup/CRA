"""Only the four newly created generic student accounts; no screenshots or flags."""
import json
import sys
from playwright.sync_api import sync_playwright, expect


def main():
    data = json.load(sys.stdin)
    assert [a['username'] for a in data['accounts']] == ['elev15', 'elev16', 'elev17', 'elev18']
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome', chromium_sandbox=True, headless=True)
        pages = []
        for account in data['accounts']:
            context = browser.new_context()
            page = context.new_page()
            page.goto(data['url'])
            page.get_by_label('Brugernavn').fill(account['username'])
            page.get_by_label('Adgangskode').fill(account['password'])
            page.get_by_role('button', name='Fortsæt →').click()
            expect(page.get_by_role('heading', name='Dit forløb', exact=True)).to_be_visible()
            page.get_by_role('button', name='⌘ Mit lab', exact=True).click()
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible(timeout=30000)
            pages.append(page)
        for page in pages:
            page.get_by_role('button', name='Forbind igen', exact=True).click()
            expect(page.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible(timeout=30000)
        first = pages[0]
        other = data['labs']['elev16']
        statuses = first.evaluate("""async id=>{const me=await(await fetch('/api/v1/auth/me')).json();return [(await fetch('/api/v1/labs/'+id)).status,(await fetch('/api/v1/labs/'+id+'/terminal-grants',{method:'POST',headers:{'X-CRA-CSRF':me.csrf_token}})).status]}""", other)
        assert statuses == [404, 404]
        browser.close()
    print('PASS: four new student browser terminals, reconnect and foreign-lab denial; no passwords or screenshots exported')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('FAIL:', type(error).__name__)
        sys.exit(1)
