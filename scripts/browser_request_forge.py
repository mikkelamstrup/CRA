"""Synthetic, ephemeral loopback portal in the existing sandboxed browser guest."""
import importlib.util
import json
import os
from pathlib import Path
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    spec = importlib.util.spec_from_file_location('request_forge_portal', ROOT / 'content/labs/request-forge/portal.py')
    portal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(portal)
    server = portal.Portal(('127.0.0.1', 0), 'CRA{synthetic-browser-fixture-only}')
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    dest = ROOT / 'reports/local/request-forge'
    dest.mkdir(parents=True, exist_ok=True)
    errors, requests = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ.get('CRA_TEST_BROWSER'),
                                         headless=True, chromium_sandbox=True)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 800})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('chrome://sandbox')
                assert 'You are adequately sandboxed.' in page.locator('body').inner_text()
                page.goto('http://127.0.0.1:' + str(server.server_port))
                expect(page.get_by_role('heading', name='Nordtele · Ordrestatus')).to_be_visible()
                expect(page.get_by_role('button', name='Kontrollér ordre')).not_to_be_visible()
                page.get_by_label('Bruger', exact=True).fill(portal.USERNAME)
                page.get_by_label('Adgangskode', exact=True).fill(portal.PASSWORD)
                page.get_by_role('button', name='Log ind', exact=True).click()
                expect(page.get_by_role('status')).to_contain_text('Logget ind')
                page.on('request', lambda request: requests.append(request) if request.url.endswith('/api/search') else None)
                page.get_by_role('button', name='Kontrollér ordre').click()
                expect(page.get_by_role('status')).to_have_text('Ordren findes.')
                normal = requests[-1]
                assert normal.method == 'POST'
                assert json.loads(normal.post_data) == {'reference': 'NT-1042'}
                headers = normal.all_headers()
                assert headers['content-type'] == 'application/json'
                assert 'nt_session=' in headers['cookie']
                assert headers['x-csrf-token']
                page.get_by_label('Ordrenummer').fill('NT-0000')
                page.get_by_role('button', name='Kontrollér ordre').click()
                expect(page.get_by_role('status')).to_have_text('Ingen match.')
                # Manipulate and replay the captured request using the same browser session.
                for condition, expected in [('1=1', True), ('1=2', False)]:
                    response = page.request.post(normal.url,
                        headers={'X-CSRF-Token': headers['x-csrf-token']},
                        data={'reference': "NT-1042' AND (" + condition + ') -- '})
                    assert response.status == 200
                    assert response.json() == {'matched': expected}
                assert page.request.post(normal.url, data={'reference': 'NT-1042'}).status == 403
                page.get_by_label('Ordrenummer').fill('NT-1042')
                page.get_by_role('button', name='Kontrollér ordre').click()
                expect(page.get_by_role('status')).to_have_text('Ordren findes.')
                page.screenshot(path=str(dest / 'portal-desktop.png'))
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                expect(page.get_by_role('button', name='Kontrollér ordre')).to_be_in_viewport()
                page.screenshot(path=str(dest / 'portal-mobile.png'))
                assert not errors, errors
                print('PASS: sandbox, real login/search, captured request, replay, CSRF, desktop/mobile, no JS errors')
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()


if __name__ == '__main__':
    main()
