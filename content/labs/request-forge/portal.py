"""Intentional boolean-blind SQL injection. Isolated CRA target VM only.

Real SQLite, synthetic rows, bounded SELECTs and no filesystem/network SQL.
Only the search expression is intentionally vulnerable; login and proof are not.
"""

import argparse
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
USERNAME = "auditor"
PASSWORD = "nordtele-lab-only"


def proof_code(flag):
    return hmac.new(
        flag.encode(), b"request-forge-proof-v1", hashlib.sha256
    ).hexdigest()[:16]


def authorize(action, first, second, database, source):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and second in {
        "length",
        "substr",
        "substring",
        "unicode",
        "hex",
        "lower",
        "upper",
        "count",
        "min",
        "max",
        "coalesce",
        "like",
        "glob",
        "sqlite_version",
        "typeof",
        "quote",
        "trim",
        "printf",
        "abs",
        "round",
        "group_concat",
    }:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class Portal(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    session_limit = 512

    def __init__(self, address, flag):
        # Load only fixed assets; HTTP paths never become filesystem paths.
        self.assets = {
            "/": (ROOT / "index.html").read_text(),
            "/client.js": (ROOT / "client.js").read_text(),
        }
        self.flag = flag
        self.code = proof_code(flag)
        self.sessions = {}
        self.lock = threading.Lock()
        self.workers = threading.BoundedSemaphore(64)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE orders (reference TEXT, tenant TEXT, visible INTEGER);
            INSERT INTO orders VALUES ('NT-1042','demo',1), ('NT-1043','demo',1),
                                      ('NT-9000','internal',0);
            CREATE TABLE audit_vault (id INTEGER PRIMARY KEY, proof_code TEXT);
        """)
        self.db.execute("INSERT INTO audit_vault VALUES (1, ?)", (self.code,))
        self.db.commit()
        self.db.execute("PRAGMA query_only=ON")
        self.db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16384)
        self.db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 4096)
        self.db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 40)
        self.db.set_authorizer(authorize)
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def process_request(self, request, address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.workers.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.workers.release()

    def search(self, reference):
        # INTENTIONAL LAB VULNERABILITY. Never copy this interpolation to CRA APIs.
        sql = (
            "SELECT 1 FROM orders WHERE tenant='demo' AND reference='"
            + reference
            + "' AND visible=1"
        )
        with self.lock:
            ticks = 0

            def budget():
                nonlocal ticks
                ticks += 1
                return ticks > 100

            self.db.set_progress_handler(budget, 100)
            try:
                return self.db.execute(sql).fetchone() is not None
            finally:
                self.db.set_progress_handler(None, 0)

    def server_close(self):
        super().server_close()
        with self.lock:
            self.db.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "NordteleOrders/2"

    def log_message(self, *args):
        pass  # Requests may contain synthetic session tokens; do not persist them.

    def send(self, status, body, kind="application/json", cookie=None):
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if not getattr(self, "head_only", False):
            self.wfile.write(data)

    def session(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookies["nt_session"].value if "nt_session" in cookies else ""
        except CookieError:
            return None
        with self.server.lock:
            value = self.server.sessions.get(token)
            return value if value and value["expires"] > time.monotonic() else None

    def do_HEAD(self):
        self.head_only = True
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in self.server.assets:
            kind = "text/html" if path == "/" else "text/javascript"
            self.send(200, self.server.assets[path], kind)
        elif path == "/api/session":
            session = self.session()
            if session:
                self.send(200, {"user": USERNAME, "csrf": session["csrf"]})
            else:
                self.send(401, {"error": "login_required"})
        elif path in ("/api/search", "/api/proof", "/api/login"):
            self.send(405, {"error": "post_required"})
        else:
            self.send(404, {"error": "not_found"})

    def body(self):
        self.close_connection = True
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            self.send(400, {"error": "content_length_required"})
            return None
        try:
            length = int(lengths[0])
        except ValueError:
            length = -1
        if length < 0 or length > 4096:
            self.send(413, {"error": "body_limit"})
            return None
        if length == 0:
            self.send(400, {"error": "invalid_json"})
            return None
        if (
            self.headers.get("Content-Type", "").split(";")[0].strip()
            != "application/json"
        ):
            self.send(415, {"error": "json_required"})
            return None
        try:
            raw = self.rfile.read(length)
        except (TimeoutError, OSError):
            self.send(400, {"error": "truncated_body"})
            return None
        if len(raw) != length:
            self.send(400, {"error": "truncated_body"})
            return None
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TypeError("object required")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            self.send(400, {"error": "invalid_json"})
            return None
        return data

    def do_POST(self):
        if self.path not in ("/api/login", "/api/search", "/api/proof"):
            self.send(404, {"error": "not_found"})
            return
        data = self.body()
        if data is None:
            return
        if self.path == "/api/login":
            if data != {"username": USERNAME, "password": PASSWORD}:
                self.send(403, {"error": "invalid_login"})
                return
            now = time.monotonic()
            with self.server.lock:
                self.server.sessions = {
                    key: value
                    for key, value in self.server.sessions.items()
                    if value["expires"] > now
                }
                if len(self.server.sessions) >= self.server.session_limit:
                    oldest = min(
                        self.server.sessions,
                        key=lambda key: self.server.sessions[key]["expires"],
                    )
                    del self.server.sessions[oldest]
                token, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
                self.server.sessions[token] = {"csrf": csrf, "expires": now + 3600}
            self.send(
                200,
                {"csrf": csrf, "user": USERNAME},
                cookie="nt_session=" + token + "; Path=/; HttpOnly; SameSite=Strict",
            )
            return
        session = self.session()
        if not session:
            self.send(401, {"error": "login_required"})
            return
        csrf = self.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(csrf.encode(), session["csrf"].encode()):
            self.send(403, {"error": "csrf_required"})
            return
        key, limit = ("reference", 1024) if self.path == "/api/search" else ("code", 64)
        if (
            set(data) != {key}
            or not isinstance(data[key], str)
            or not 0 < len(data[key]) <= limit
        ):
            self.send(400, {"error": "invalid_field"})
            return
        try:
            data[key].encode("utf-8")
        except UnicodeError:
            self.send(400, {"error": "invalid_field"})
            return
        if self.path == "/api/search":
            try:
                found = self.server.search(data["reference"])
            except sqlite3.Error:
                self.send(400, {"error": "invalid_search"})
                return
            self.send(200, {"matched": found})
        elif hmac.compare_digest(data["code"].encode(), self.server.code.encode()):
            self.send(200, {"flag": self.server.flag})
        else:
            self.send(403, {"error": "incorrect_proof"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    # Solo mission: exactly one target lab NIC, never a host/control listener.
    with Portal((config["address"], 8081), config["flag"]) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
