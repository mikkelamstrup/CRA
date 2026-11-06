"""Fictional Nordtele portal. Intentionally vulnerable only inside a CRA target VM."""
import argparse
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

STOLEN_SESSION = "nt-support-legacy"
DEFAULT_POLICY = {
    "emergency_lockdown": False,
    "enforce_report_ownership": False,
    "expose_support_bundle": True,
    "revoked_sessions": [],
}
ACCOUNTS = {"kunde-a": ("nordtele-lab-a", "A", "8201"),
            "kunde-b": ("nordtele-lab-b", "B", "8202")}
REPORTS = {"8201": {"customer": "A", "body": "Fiktiv kunde A: fiberforbindelse etableret."},
           "8202": {"customer": "B", "body": "Fiktiv kunde B: intern netplan og serviceaftale."}}


def read_policy(path):
    value = json.loads(Path(path).read_text())
    if set(value) != set(DEFAULT_POLICY):
        raise ValueError("Ukendte eller manglende politikfelter")
    for key in ("emergency_lockdown", "enforce_report_ownership", "expose_support_bundle"):
        if type(value[key]) is not bool:
            raise ValueError("Politikfelter skal være true eller false")
    revoked = value["revoked_sessions"]
    if not isinstance(revoked, list) or len(revoked) > 100 or any(
        not isinstance(x, str) or len(x) > 100 for x in revoked
    ):
        raise ValueError("Ugyldig liste over tilbagekaldte sessioner")
    return value


class Portal(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, policy, log, shared=None, audit=None):
        super().__init__(address, Handler)
        self.policy = policy
        self.sessions, self.lock = shared or ({STOLEN_SESSION: "kunde-a"}, threading.Lock())
        self.log_handler = None
        self.audit = audit
        if self.audit is None:
            self.audit = logging.getLogger("cybercon-" + str(id(self)))
            self.audit.setLevel(logging.INFO)
            self.audit.propagate = False
            self.log_handler = RotatingFileHandler(log, maxBytes=2_000_000, backupCount=3)
            self.audit.addHandler(self.log_handler)

    def server_close(self):
        super().server_close()
        if self.log_handler is not None:
            self.log_handler.close()
            self.audit.removeHandler(self.log_handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "NordtelePortal/1"

    def log_message(self, *args):
        pass

    def send(self, status, value, cookie=None, kind="application/json"):
        data = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)
        self.server.audit.info(json.dumps({
            "time": datetime.now(timezone.utc).isoformat(), "source": self.client_address[0],
            "method": self.command, "path": self.path[:300], "status": status,
            "user": getattr(self, "user", None),
            "session": "legacy-support" if getattr(self, "token", "") == STOLEN_SESSION else "other",
            "bytes": len(data),
            "error": value.get("error") if isinstance(value, dict) else None,
        }))

    def policy(self):
        try:
            return read_policy(self.server.policy)
        except (OSError, ValueError, TypeError):
            self.send(503, {"error": "invalid_security_policy"})
            return None

    def identity(self, policy):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            self.token = cookie["nt_session"].value if "nt_session" in cookie else ""
        except Exception:
            self.token = ""
        with self.server.lock:
            self.user = self.server.sessions.get(self.token)
        if self.token in policy["revoked_sessions"]:
            self.user = None
        return self.user

    def do_GET(self):
        parsed = urlsplit(self.path)
        policy = self.policy()
        if policy is None:
            return
        self.identity(policy)
        if parsed.path == "/":
            self.send(200, """<!doctype html><html lang="da"><meta charset="utf-8">
<title>Nordtele · Kundeportal</title><style>body{font:18px system-ui;max-width:760px;margin:60px auto;padding:24px;background:#edf2f7;color:#14253c}a{color:#145f9e}input,button{font:inherit;margin:8px;padding:8px}</style>
<h1>Nordtele</h1><p>Forbindelser til hele Danmark · Fiktiv CRA-øvelse</p>
<h2>Erhvervskunders serviceportal</h2><form action="/login" method="post">
<label>Bruger<input name="username"></label><label>Kode<input name="password" type="password"></label><button>Log ind</button></form>
<p><a href="/api/me">Min profil</a> · <a href="/api/reports?id=8201">Servicerapport 8201</a> · <a href="/api/reports?id=8202">Servicerapport 8202</a></p>
<p>Drift: servicevinduer fremgår af Cybercons overdragelse.</p></html>""", kind="text/html")
            return
        if policy["emergency_lockdown"]:
            self.send(503, {"error": "incident_containment", "message": "Midlertidig begrænsning"})
            return
        if parsed.path == "/support/diagnostics.json":
            if not policy["expose_support_bundle"]:
                self.send(404, {"error": "not_found"})
            else:
                self.send(200, {"export": "support-debug", "session_cookie": STOLEN_SESSION,
                                "account": "kunde-a", "note": "Fiktiv sessionskopi fra supporttest"})
            return
        if parsed.path not in ("/api/me", "/api/reports"):
            self.send(404, {"error": "not_found"})
            return
        if not self.user:
            self.send(401, {"error": "login_required"})
            return
        account = ACCOUNTS[self.user]
        if parsed.path == "/api/me":
            self.send(200, {"user": self.user, "customer": account[1], "allowed_reports": [account[2]]})
            return
        query = parse_qs(parsed.query)
        if set(query) != {"id"} or len(query["id"]) != 1:
            self.send(400, {"error": "one_report_id_required"})
            return
        report_id = query["id"][0]
        if report_id not in REPORTS:
            self.send(404, {"error": "not_found"})
        elif policy["enforce_report_ownership"] and REPORTS[report_id]["customer"] != account[1]:
            self.send(403, {"error": "report_access_denied"})
        else:
            self.send(200, {"id": report_id, **REPORTS[report_id]})

    def do_POST(self):
        if self.path != "/login":
            self.send(404, {"error": "not_found"})
            return
        policy = self.policy()
        if policy is None:
            return
        if policy["emergency_lockdown"]:
            self.send(503, {"error": "incident_containment"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2048:
                raise ValueError()
            fields = parse_qs(self.rfile.read(size).decode())
            user = fields.get("username", [""])[0]
            password = fields.get("password", [""])[0]
        except (ValueError, UnicodeError):
            self.send(400, {"error": "invalid_login_request"})
            return
        if user not in ACCOUNTS or not secrets.compare_digest(password, ACCOUNTS[user][0]):
            self.send(403, {"error": "invalid_credentials"})
            return
        with self.server.lock:
            if len(self.server.sessions) >= 1000:
                self.send(429, {"error": "session_limit"})
                return
            token = secrets.token_hex(24)
            self.server.sessions[token] = user
        self.user = user
        self.send(200, {"user": user}, cookie="nt_session=" + token + "; HttpOnly; SameSite=Strict; Path=/")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    shared = ({STOLEN_SESSION: "kunde-a"}, threading.Lock())
    servers = []
    for ip in config["addresses"]:
        servers.append(Portal((ip, 8081), "/etc/cybercon/policy.json",
                              "/var/log/cybercon/access.jsonl", shared,
                              audit=servers[0].audit if servers else None))
    for server in servers[:-1]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[-1].serve_forever()


if __name__ == "__main__":
    main()
