"""Live UI check: opgave 14 visible and openable for the test account, locked for a real student."""
import json
import sys
from playwright.sync_api import sync_playwright, expect

data = json.load(sys.stdin)
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',
                                chromium_sandbox=True, headless=True)
    check = browser.new_page()
    check.goto('chrome://sandbox')
    assert 'You are adequately sandboxed.' in check.locator('body').inner_text()
    check.close()

    def login(page, user):
        page.goto(data['url'])
        page.get_by_label('Brugernavn').fill(user['username'])
        page.get_by_label('Adgangskode').fill(user['password'])
        page.get_by_role('button', name='Fortsæt →', exact=True).click()
        expect(page.get_by_role('heading', name='Dit forløb', exact=True)).to_be_visible()
        dialog = page.get_by_role('dialog')
        if dialog.count() and dialog.first.is_visible():
            page.get_by_role('button', name='Luk', exact=True).click()

    student = browser.new_context(viewport={'width': 1440, 'height': 1000}).new_page()
    login(student, data['student'])
    card = student.locator('article', has_text='Request Forge')
    expect(card).to_be_visible()
    expect(card).to_contain_text('Opgave 14')
    expect(card).to_contain_text('700 point')
    student.locator('article', has_text='Request Forge').get_by_role('button').click()
    expect(student.get_by_role('heading', name='Din mission', exact=True)).to_be_visible()
    expect(student.locator('.lab-status')).to_contain_text('Klar')
    expect(student.locator('.lab-topology')).to_contain_text(data['target_ip'])
    expect(student.get_by_role('region', name='Dit lab')).to_be_visible()
    student.screenshot(path='/tmp/opgave14-elev.png', full_page=True)

    other = browser.new_context(viewport={'width': 1440, 'height': 1000}).new_page()
    login(other, data['other'])
    other_card = other.locator('article', has_text='Request Forge')
    expect(other_card).to_be_visible()
    expect(other_card).to_contain_text('Afventer lærerens frigivelse')
    expect(other_card.get_by_role('button')).to_be_disabled()
    print('PASS: opgave 14 frigivet og åbnbar for testelev; låst for ' + data['other']['username'])
    browser.close()
