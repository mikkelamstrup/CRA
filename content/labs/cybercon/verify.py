"""Root-owned fixed verifier. Checks HTTP behaviour before releasing a lab-scoped flag."""
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


def verify(base="http://127.0.0.1:8081"):
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    failures = []

    def request(path, cookie="", data=None):
        req = urllib.request.Request(base + path, data=data, headers={"Cookie": cookie})
        try:
            response = client.open(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(16384)
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            return response.code, body, response.headers.get("Set-Cookie", "").split(";")[0]

    def expect(label, result, code, report=None):
        status, body, _ = result
        if status != code or (report and (not isinstance(body, dict) or body.get("id") != report)):
            failures.append(label + ": forventet HTTP " + str(code) + ", fik " + str(status))

    expect("Portalen er tilgængelig", request("/"), 200)
    expect("Anonym adgang afvist", request("/api/reports?id=8201"), 401)
    expect("Supporteksport fjernet", request("/support/diagnostics.json"), 404)
    expect("Lækket session tilbagekaldt", request("/api/reports?id=8201", "nt_session=nt-support-legacy"), 401)
    for user, password, own, other in [("kunde-a", "nordtele-lab-a", "8201", "8202"),
                                        ("kunde-b", "nordtele-lab-b", "8202", "8201")]:
        login = request("/login", data=urllib.parse.urlencode({"username": user, "password": password}).encode())
        expect(user + " kan logge ind", login, 200)
        cookie = login[2]
        expect(user + " kan læse egen rapport", request("/api/reports?id=" + own, cookie), 200, own)
        expect(user + " afvises på anden kundes rapport", request("/api/reports?id=" + other, cookie), 403)
    return failures


def main():
    for service in ("cybercon-portal", "cybercon-attack"):
        if subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", service], check=False).returncode:
            print("IKKE GODKENDT: " + service + " skal køre. En slukket service er ikke en reparation.")
            return 1
    try:
        failures = verify()
    except (OSError, ValueError):
        print("IKKE GODKENDT: Portalen kunne ikke kontrolleres.")
        return 1
    if failures:
        print("IKKE GODKENDT\n" + "\n".join(failures))
        return 1
    print("TEKNISK EFTERPRØVNING GODKENDT: Normal kundeadgang virker; de prøvede adgangsveje er lukket.")
    print("Aflever også hændelsesrapporten. Denne kontrol vurderer ikke din undersøgelse eller forklaring.")
    print(Path("/etc/cybercon/completion.txt").read_text().strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
