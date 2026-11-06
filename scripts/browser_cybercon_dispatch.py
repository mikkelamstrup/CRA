"""Synthetic API fixtures, real built React UI and sandboxed browser; no live student access."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from cra.assignments import INCIDENT_MESSAGE


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT / 'frontend/dist')))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    destination = ROOT / 'reports/local/cybercon'
    destination.mkdir(parents=True, exist_ok=True)
    state = {'released': False, 'ack': False, 'fail_ack': False, 'guidance': False, 'guidance_ack': False}
    alert = {'mission_id': 'cybercon-incident', 'mission_version': 1,
             'released_at': '2026-10-29T10:00:00Z', 'title': 'AKUT SAG · Nordtele er under angreb',
             'message': INCIDENT_MESSAGE}
    help_alert = {**alert, 'guidance_id': '00000000-0000-4000-8000-000000000009',
                  'title': 'Opgave 9 · sådan løser du den',
                  'message': (ROOT / 'content/labs/cybercon/guidance-popup.txt').read_text()}
    mutations = []
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.environ.get('CRA_TEST_BROWSER','/home/crauser/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome'),
                headless=True, chromium_sandbox=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto('chrome://sandbox')
            assert 'You are adequately sandboxed.' in page.locator('body').inner_text()
            page.on('pageerror', lambda error: errors.append(str(error)))

            def route(request):
                path = request.request.url.split('/api/v1')[-1]
                if request.request.method != 'GET':
                    mutations.append(path)
                status = 200
                if path == '/auth/me':
                    value = {'user_id': 'synthetic-student-a', 'organization_id': 'synthetic-org',
                             'username': 'testanalytiker', 'role': 'student', 'csrf_token': 'synthetic'}
                elif path == '/assignments/alert':
                    value = help_alert if state['guidance'] and not state['guidance_ack'] else alert if state['released'] and not state['ack'] else None
                elif path == '/assignments/guidance/' + help_alert['guidance_id'] + '/acknowledge':
                    state['guidance_ack'] = True
                    value = {'acknowledged': True}
                elif path == '/assignments/cybercon-incident/acknowledge':
                    if state['fail_ack']:
                        status, value = 503, {'detail': 'synthetic_failure'}
                    else:
                        state['ack'] = True
                        value = {'acknowledged': True}
                elif path == '/teaching/session':
                    value = None
                elif path == '/missions':
                    value = [json.loads((ROOT / 'content/missions/cybercon-incident.v1.json').read_text())]
                else:
                    value = []
                request.fulfill(status=status, content_type='application/json', body=json.dumps(value))

            page.route('**/api/v1/**', route)
            page.goto('http://127.0.0.1:' + str(server.server_port))
            expect(page.get_by_role('button', name='⌘ Mit lab', exact=True)).to_be_visible()
            page.get_by_role('button', name='⌘ Mit lab', exact=True).click()
            assert page.locator('dialog').count() == 0
            state['released'] = True
            modal = page.get_by_role('dialog')
            expect(modal).to_be_visible(timeout=8000)
            expect(modal).to_contain_text('ØVELSE')
            box = modal.bounding_box()
            assert abs((box['x'] + box['width']/2) - 720) < 3
            assert page.evaluate('document.activeElement.closest("dialog") !== null')
            page.keyboard.press('Escape')
            expect(modal).to_be_visible()
            page.screenshot(path=str(destination / 'dispatch-desktop.png'))
            page.reload()
            expect(modal).to_be_visible()
            state['fail_ack'] = True
            modal.get_by_role('button', name='Modtaget · bliv på denne side').click()
            expect(modal.get_by_role('alert')).to_be_visible()
            expect(modal).to_be_visible()
            modal.get_by_role('button', name='Luk beskeden · kvittér ved næste besøg').click()
            expect(modal).not_to_be_visible()
            page.wait_for_timeout(3500)
            expect(modal).not_to_be_visible()
            assert not state['ack']
            page.reload()
            expect(modal).to_be_visible()
            state['fail_ack'] = False
            modal.get_by_role('button', name='Modtaget · bliv på denne side').click()
            expect(modal).not_to_be_visible()
            page.wait_for_timeout(3500)
            expect(modal).not_to_be_visible()
            assert all(p.endswith('/acknowledge') for p in mutations)
            # A separate help dispatch arrives on the same page after an earlier acknowledgement.
            state['guidance'] = True
            expect(modal).to_be_visible(timeout=8000)
            expect(modal).to_contain_text('Hjælp'.upper() + ' FRA DIN LÆRER')
            expect(modal.locator('pre')).to_have_count(5)
            assert modal.locator('code').filter(has_text='d.update').count() == 1
            page.screenshot(path=str(destination / 'guidance-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.reload()
            expect(modal).to_be_visible()
            assert modal.evaluate('(el) => el.scrollWidth <= el.clientWidth')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            expect(modal.get_by_role('heading')).to_be_in_viewport()
            page.screenshot(path=str(destination / 'guidance-mobile.png'))
            modal.get_by_role('button', name='Forstået · tilbage til opgaven').click()
            expect(modal).not_to_be_visible()
            assert state['guidance_ack']
            page.reload()
            page.wait_for_timeout(3500)
            expect(modal).not_to_be_visible()
            state['guidance'] = False
            # New simulated account/release: narrow mobile screen and dirty-note navigation.
            state['ack'] = False
            page.set_viewport_size({'width': 390, 'height': 844})
            page.reload()
            expect(modal).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert modal.evaluate('(el) => el.scrollWidth <= el.clientWidth')
            assert modal.evaluate('(el) => el.scrollTop === 0')
            expect(modal.get_by_role('heading')).to_be_in_viewport()
            page.screenshot(path=str(destination / 'dispatch-mobile.png'))
            page.evaluate('document.querySelector("main").dataset.unsaved="true"')
            page.once('dialog', lambda d: d.dismiss())
            modal.get_by_role('button', name='Modtag sagen og åbn opgaven').click()
            expect(modal).to_be_visible()
            assert not state['ack']
            page.evaluate('delete document.querySelector("main").dataset.unsaved')
            modal.get_by_role('button', name='Modtag sagen og åbn opgaven').click()
            expect(modal).not_to_be_visible()
            expect(page.get_by_role('heading', name='Cybercon · Alarm hos Nordtele', exact=True)).to_be_visible()
            assert all(p.endswith('/acknowledge') for p in mutations)
            assert not errors, errors
            browser.close()
            print('PASS: sandboxed Chromium; targeted help after earlier acknowledgement without reload; separate acknowledgement; multiline commands; desktop/mobile; original alarm regression; no lab mutation. API is a synthetic fixture.')
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


if __name__ == '__main__':
    main()
