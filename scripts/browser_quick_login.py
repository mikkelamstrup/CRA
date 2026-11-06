"""Actual HTTPS login using only a temporary synthetic account; no credential logs."""
import json,sys
from playwright.sync_api import sync_playwright,expect


def main():
    bundle=json.load(sys.stdin);base=bundle['url'];account=bundle['users']['student']
    assert base.startswith('https://') and base.endswith('.trycloudflare.com')
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        page=browser.new_page();page.goto('chrome://sandbox');assert 'You are adequately sandboxed.' in page.locator('body').inner_text()
        page.goto(base);page.get_by_label('Brugernavn').fill(account['username']);page.get_by_label('Adgangskode').fill(account['password']);page.get_by_role('button',name='Fortsæt →').click()
        expect(page.get_by_role('heading',name='Dit forløb',exact=True)).to_be_visible(timeout=30000)
        cookies=[value for value in page.context.cookies() if value['name']=='cra_session'];assert len(cookies)==1 and cookies[0]['secure'] and cookies[0]['httpOnly'] and cookies[0]['sameSite']=='Strict'
        assert page.request.get(base+'/api/v1/auth/me').ok
        page.get_by_role('button',name='Log ud',exact=True).click();expect(page.get_by_label('Brugernavn')).to_be_visible()
        browser.close()
    print('PASS: public HTTPS browser login/logout, Secure HttpOnly SameSite cookie, sandboxed Chromium')


if __name__=='__main__':main()
