"""UI regression with explicitly synthetic API fixtures; no live accounts or VM calls.

Run in the existing control-VM browser environment with the built dist as argv[1].
"""
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def main():
    dist = Path(sys.argv[1]).resolve()
    missions = [dict(id=key, version=1, title=title, summary="Synthetic mission",
                     estimated_minutes=30, briefing="Synthetic briefing", hints=[],
                     objectives=[dict(id="flag", title="Find flag", description="Synthetic", points=100)])
                for key, title in [("first-files", "Det mistede signal"), ("first-contact", "Første kontakt")]]
    lab = dict(id="synthetic-lab", generation=1, mission_id="first-files", team_id=None,
               state="READY", can_manage=True, can_operate=False, desktop_enabled=False,
               last_observed_at="2026-10-22T12:00:00Z", target_ip="192.0.2.1", error_code=None)
    calls = []
    failures = {"destroy": True, "refresh": False}

    def route_request(route):
        path = urlparse(route.request.url).path
        if path.startswith("/api/"):
            status = 200
            if route.request.method == "POST":
                calls.append((path, route.request.post_data_json, route.request.headers))
                if path == "/api/v1/labs/synthetic-lab/destroy":
                    if failures["destroy"]:
                        status, data = 503, {"detail": "node_unavailable"}
                        failures["destroy"] = False
                    else:
                        lab["state"] = "DESTROYING"
                        data = lab
                elif path == "/api/v1/labs":
                    lab.update(id="synthetic-new", mission_id=route.request.post_data_json["mission_id"], state="QUEUED")
                    data = lab
                else:
                    raise AssertionError("Unexpected mutation")
            elif path == "/api/v1/auth/me":
                data = dict(id="synthetic-user", username="UI fixture", role="student", csrf_token="synthetic-csrf")
            elif path == "/api/v1/missions":
                data = missions
            elif path == "/api/v1/assignments":
                data = [dict(mission_id=m["id"], mission_version=1, title=m["title"], summary=m["summary"],
                             estimated_minutes=30, completed=False, earned_points=0, available=True) for m in missions]
            elif path == "/api/v1/labs":
                status, data = (503, {}) if failures["refresh"] else (200, [lab])
            elif path.endswith("/progress"):
                data = dict(completed_objectives=[], total_points=0)
            elif path == "/api/v1/teaching/session":
                data = None
            elif path == "/api/v1/course/days":
                data = []
            else:
                raise AssertionError("Unexpected fixture API route")
            route.fulfill(status=status, content_type="application/json", body=json.dumps(data))
        else:
            file = dist / (path.lstrip("/") or "index.html")
            assert file.is_relative_to(dist) and file.is_file()
            route.fulfill(path=str(file))

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/cra-browser/chrome-linux64/chrome", chromium_sandbox=True, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto("chrome://sandbox")
        assert "You are adequately sandboxed." in page.locator("body").inner_text()
        page.route("**/*", route_request)
        page.goto("https://mission-switch.test/")
        page.get_by_role("button", name="Åbn opgave 2", exact=True).click()
        panel = page.get_by_role("region", name="Skift opgave")
        switch = panel.get_by_role("button", name="Skift til Første kontakt", exact=True)
        remove = panel.get_by_role("button", name="Fjern gammelt miljø", exact=True)
        expect(switch).to_be_visible()
        expect(panel).to_be_focused()
        assert not calls
        switch.click()
        expect(panel).to_contain_text("Alle filer i det gamle miljø slettes.")
        panel.get_by_role("button", name="Behold miljøet", exact=True).click()
        expect(remove).to_have_count(0)
        assert not calls
        switch.click()
        page.get_by_role("button", name="Åbn opgave 1", exact=True).click()
        expect(panel).to_have_count(0)
        page.get_by_role("button", name="Åbn opgave 2", exact=True).click()
        expect(remove).to_have_count(0)
        switch.click()
        lab["generation"] = 2
        expect(remove).to_have_count(0, timeout=10000)
        expect(switch).to_be_visible()
        lab["state"] = "STOPPING"
        expect(switch).to_have_count(0, timeout=10000)
        expect(panel).to_contain_text("Vent på")
        lab["state"] = "STOPPED"
        expect(switch).to_be_visible(timeout=10000)
        failures["refresh"] = True
        expect(switch).to_be_disabled(timeout=10000)
        failures["refresh"] = False
        expect(switch).to_be_enabled(timeout=10000)
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Åbn opgave 1", exact=True).click()
        page.get_by_role("button", name="Åbn opgave 2", exact=True).click()
        expect(panel).to_be_focused()
        expect(switch).to_be_in_viewport()
        switch.click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path="/var/lib/cra-browser/mission-switch-mobile.png", full_page=True)
        remove.click()
        expect(page.get_by_role("alert")).to_contain_text("Labserveren svarer ikke")
        assert len(calls) == 1 and lab["state"] == "STOPPED"
        switch.click()
        remove.click()
        expect(panel).to_contain_text("Det gamle miljø fjernes.")
        assert len(calls) == 2
        assert calls[0][2]["idempotency-key"] == calls[1][2]["idempotency-key"]
        assert calls[1][2]["x-cra-csrf"] == "synthetic-csrf"
        start = page.get_by_role("button", name="Start mission →", exact=True)
        expect(start).to_have_count(0)
        lab["state"] = "DESTROYED"
        expect(start).to_be_visible(timeout=10000)
        expect(panel).to_have_count(0)
        assert len(calls) == 2
        start.click()
        expect(page.get_by_text("I kø", exact=True).first).to_be_visible()
        assert len(calls) == 3 and calls[-1][1]["mission_id"] == "first-contact"
        print("PASS: synthetic UI fixtures: cancel, navigation/generation reset, busy state, refresh failure, mobile, destroy retry/idempotency, wait for removal, explicit correct mission create")
        browser.close()


if __name__ == "__main__":
    main()
