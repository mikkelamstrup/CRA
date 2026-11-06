"""Public UI acceptance using only disposable synthetic teacher/student identities."""
import json
import sys
from playwright.sync_api import sync_playwright, expect


def main():
    b = json.load(sys.stdin)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome', chromium_sandbox=True, headless=True)
        def login(role):
            page = browser.new_context(viewport={'width': 1440, 'height': 1000}).new_page()
            page.goto(b['url'])
            page.get_by_label('Brugernavn').fill(b['users'][role]['username'])
            page.get_by_label('Adgangskode').fill(b['users'][role]['password'])
            page.get_by_role('button', name='Fortsæt →', exact=True).click()
            expect(page.get_by_role('heading', name='Dit forløb', exact=True)).to_be_visible()
            return page
        student = login('student')
        cards = student.locator('.assignment-card')
        expect(cards).to_have_count(14)
        for index, title in [(8, 'HTTP'), (9, 'Spor'), (10, 'Inddæmning'), (11, 'Ejerskab'), (12, 'Cybercon'), (13, 'Request Forge')]:
            expect(cards.nth(index)).to_contain_text('Opgave ' + str(index + 1))
            expect(cards.nth(index)).to_contain_text(title)
        expect(cards.nth(12).get_by_role('button')).to_be_disabled()
        expect(cards.nth(13).get_by_role('button')).to_be_disabled()
        cards.nth(11).get_by_role('button').click()
        expect(student.get_by_text('Forbundet · rigtig Linux-shell', exact=True)).to_be_visible(timeout=45000)
        teacher = login('teacher')
        teacher.get_by_role('button', name='◎ Command Center', exact=True).click()
        expect(teacher.get_by_role('heading', name='Elever og opgaver', exact=True)).to_be_visible()
        expect(teacher.locator('.student-assignments .assignment-card')).to_have_count(14)
        expect(teacher.get_by_role('button', name='Frigiv opgave 13 til ' + b['users']['student']['username'], exact=True)).to_be_visible()
        expect(teacher.get_by_role('button', name='Frigiv opgave 14 til ' + b['users']['student']['username'], exact=True)).to_be_visible()
        browser.close()
    print('PASS: public synthetic teacher/student UI, assignments 9–14, explicit release gates and real stage12 terminal; no guest input')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('FAIL:', type(error).__name__)
        sys.exit(1)
