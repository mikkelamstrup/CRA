"""Private PDF renderer. Caller must capture stdout privately; never log this output."""
import base64
import json
import sys
from playwright.sync_api import sync_playwright


def main():
    data = json.load(sys.stdin)
    assert isinstance(data['html'], str) and len(data['html']) < 100000
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome', chromium_sandbox=True, headless=True)
        page = browser.new_page()
        page.goto('chrome://sandbox')
        assert 'You are adequately sandboxed.' in page.locator('body').inner_text()
        page.close()
        context = browser.new_context()
        context.route('**/*', lambda route: route.abort())
        page = context.new_page()
        page.set_content(data['html'])
        assert page.locator('tbody tr').count() == 20
        pdf = page.pdf(format='A4', print_background=True)
        browser.close()
    sys.stdout.write(base64.b64encode(pdf).decode())


if __name__ == '__main__':
    main()
