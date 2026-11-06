"""Real HTTPS browser acceptance. Synthetic credentials and lab flag only on stdin."""
import json
import sys
import time
import traceback

from playwright.sync_api import expect, sync_playwright


def api(page, path, body=None, method="GET", status=200):
    value = page.evaluate("""async args => {
      const me=await (await fetch('/api/v1/auth/me')).json();
      const r=await fetch('/api/v1'+args.path,{method:args.method,
        headers:{'Content-Type':'application/json','X-CRA-CSRF':me.csrf_token,'Idempotency-Key':crypto.randomUUID()},
        ...(args.body===null?{}:{body:JSON.stringify(args.body)})});
      return {status:r.status,body:await r.json()};
    }""", {"path": path, "body": body, "method": method})
    assert value["status"] == status, "Unexpected API status"
    return value["body"]


def main():
    data = json.load(sys.stdin)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/cra-browser/chrome-linux64/chrome", chromium_sandbox=True, headless=True)
        check = browser.new_page()
        check.goto("chrome://sandbox")
        assert "You are adequately sandboxed." in check.locator("body").inner_text()
        check.close()
        pages = []
        output = bytearray()
        for account in data["accounts"]:
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            if account["key"] == "a":
                def watch(ws):
                    def received(frame):
                        if isinstance(frame, bytes):
                            output.extend(frame)
                            if len(output) > 262144:
                                del output[:-131072]
                    ws.on("framereceived", received)
                page.on("websocket", watch)
            page.goto(data["url"])
            page.get_by_label("Brugernavn").fill(account["username"])
            page.get_by_label("Adgangskode").fill(account["password"])
            page.get_by_role("button", name="Fortsæt →").click()
            expect(page.get_by_role("heading", name="Dit forløb", exact=True)).to_be_visible()
            pages.append(page)
        teacher, a, b = pages
        expect(a.get_by_role("button", name="Åbn opgave 2", exact=True)).to_be_disabled()
        expect(b.get_by_role("button", name="Åbn opgave 2", exact=True)).to_be_disabled()
        a.get_by_role("button", name="Åbn opgave 1", exact=True).click()
        expect(a.get_by_text("Forbundet · rigtig Linux-shell", exact=True)).to_be_visible(timeout=45000)
        a.get_by_label("Dit flag", exact=True).fill(data["flag"])
        a.get_by_role("button", name="Validér flag", exact=True).click()
        expect(a.get_by_text("Flaget er korrekt. Du har optjent 100 point.", exact=True)).to_be_visible()
        api(a, "/course/days/1/notebook", {"revision": 0, "body": "Synthetic preserved notes", "reflection": "Synthetic method", "theory_read": True}, "PUT")
        teacher.get_by_role("button", name="◎ Command Center", exact=True).click()
        expect(teacher.get_by_role("heading", name="Elever og opgaver", exact=True)).to_be_visible()
        teacher.get_by_label("Vælg elev", exact=True).select_option(data["accounts"][1]["id"])
        editor = teacher.get_by_label("Navn på lærerskærmen", exact=True)
        editor.fill("Testnavn A")
        teacher.wait_for_timeout(6000)
        expect(editor).to_have_value("Testnavn A")
        teacher.get_by_role("button", name="Gem visningsnavn", exact=True).click()
        expect(teacher.get_by_role("status").filter(has_text="Visningsnavnet er gemt")).to_be_visible()
        assert api(a, "/auth/me")["username"] == data["accounts"][1]["username"]
        teacher.get_by_role("button", name="Frigiv opgave 2 til Testnavn A", exact=True).click()
        expect(a.get_by_role("button", name="Åbn opgave 2", exact=True)).to_be_enabled(timeout=15000)
        expect(b.get_by_role("button", name="Åbn opgave 2", exact=True)).to_be_disabled()
        api(b, "/labs", {"mission_id": "first-contact", "mission_version": 1}, "POST", 403)
        original = api(a, "/labs/" + data["lab_id"])
        assert original["state"] == "READY" and original["generation"] == 1
        assert api(teacher, "/command-center/overview")["labs"][0]["owner_display_name"] == "Testnavn A"
        teacher.screenshot(path="/var/lib/cra-browser/individual-assignments-teacher.png", full_page=True)
        teacher.set_viewport_size({"width": 390, "height": 844})
        assert teacher.evaluate("document.documentElement.scrollWidth <= innerWidth")
        teacher.screenshot(path="/var/lib/cra-browser/individual-assignments-mobile.png", full_page=True)
        print("PASS: sandbox, teacher alias/draft preservation, mobile layout, individual release without reload, other student denied", flush=True)
        a.get_by_role("button", name="Åbn opgave 2", exact=True).click()
        expect(a.get_by_role("region", name="Skift opgave")).to_be_visible()
        a.get_by_role("button", name="Skift til Første kontakt", exact=True).click()
        a.get_by_role("button", name="Behold miljøet", exact=True).click()
        assert api(a, "/labs/" + data["lab_id"])["state"] == "READY"
        a.get_by_role("button", name="Skift til Første kontakt", exact=True).click()
        a.get_by_role("button", name="Fjern gammelt miljø", exact=True).click()
        expect(a.get_by_role("button", name="Start mission →", exact=True)).to_be_visible(timeout=180000)
        assert api(a, "/labs/" + data["lab_id"])["state"] == "DESTROYED"
        a.get_by_role("button", name="Start mission →", exact=True).click()
        expect(a.get_by_text("Forbundet · rigtig Linux-shell", exact=True)).to_be_visible(timeout=240000)
        labs = [lab for lab in api(a, "/labs") if lab["state"] != "DESTROYED"]
        assert len(labs) == 1 and labs[0]["mission_id"] == "first-contact" and labs[0]["state"] == "READY"
        lab = labs[0]
        output.clear()
        a.locator(".xterm-helper-textarea").focus()
        a.keyboard.insert_text("nmap -sT -p 8080 " + lab["target_ip"] + " | grep -q '8080/tcp open' && curl --fail --max-time 5 -s http://" + lab["target_ip"] + ":8080/ >/dev/null && printf 'ASSIGNMENT_%s\\n' VERIFIED")
        a.keyboard.press("Enter")
        deadline = time.monotonic() + 30
        while b"ASSIGNMENT_VERIFIED" not in output:
            assert time.monotonic() < deadline, "Real terminal evidence absent"
            a.wait_for_timeout(100)
        first = api(a, "/assignments")[0]
        assert first["completed"] and first["earned_points"] == 100
        assert api(a, "/course/days/1/notebook")["body"] == "Synthetic preserved notes"
        assert not api(b, "/assignments")[1]["available"]
        a.screenshot(path="/var/lib/cra-browser/individual-assignments-student.png", full_page=True)
        print("PASS: real VM transition with cancel/explicit removal, second-mission shell nmap/curl, preserved first-mission points and notes", flush=True)
        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("FAIL:", type(error).__name__, "lines", ",".join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)), flush=True)
        sys.exit(1)
