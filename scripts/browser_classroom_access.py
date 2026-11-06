"""Public HTTPS browser login check; credentials only arrive through stdin."""
import json
import sys

from playwright.sync_api import expect, sync_playwright


def main():
    data = json.load(sys.stdin)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/opt/cra-browser/chrome-linux64/chrome",
            chromium_sandbox=True,
            headless=True,
        )
        check = browser.new_page()
        check.goto("chrome://sandbox")
        assert "You are adequately sandboxed." in check.locator("body").inner_text()
        check.close()
        contexts = []
        for account in [data["accounts"][0], next(a for a in data["accounts"] if a["role"] == "teacher")]:
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(data["url"])
            page.get_by_label("Brugernavn").fill(account["username"])
            page.get_by_label("Adgangskode").fill(account["password"])
            page.get_by_role("button", name="Fortsæt →").click()
            expect(page.get_by_role("heading", name="Dit forløb", exact=True)).to_be_visible()
            actual = page.evaluate("async () => (await fetch('/api/v1/auth/me')).json()")
            assert actual["username"] == account["username"]
            assert actual["role"] == account["role"]
            assert actual["organization_id"] == data["organization_id"]
            response = page.request.get(data["url"] + "/api/v1/command-center/overview")
            assert response.status == (200 if account["role"] == "teacher" else 403)
            contexts.append((context, page, account["username"]))
        for context, page, username in contexts:
            page.reload()
            expect(page.get_by_role("heading", name="Dit forløb", exact=True)).to_be_visible()
            actual = page.evaluate("async () => (await fetch('/api/v1/auth/me')).json()")
            assert actual["username"] == username
            page.evaluate("""async () => {
                const me = await (await fetch('/api/v1/auth/me')).json();
                const response = await fetch('/api/v1/auth/logout', {
                    method: 'POST', headers: {'X-CRA-CSRF': me.csrf_token}
                });
                if (!response.ok) throw new Error('Logout failed');
            }""")
            context.close()
        browser.close()
    print("PASS: sandboxed Chromium public student/teacher login, roles, isolated contexts, reload, logout")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # No Playwright call log: a failing fill might otherwise reveal a password.
        print("Browser check failed:", type(error).__name__)
        raise SystemExit(1) from None
