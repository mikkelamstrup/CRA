from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cra.auth import COOKIE, PASSWORDS, digest
from cra.db import connect
from cra.main import app

ORIGIN = {"Origin": "http://127.0.0.1:28000"}
# Explicitly synthetic test-only credential, never an installed account.
PASSWORD = "synthetic-test-only-password"


@pytest.fixture
def identities(database):
    first, second, student, teacher, outsider = [uuid4() for _ in range(5)]
    password_hash = PASSWORDS.hash(PASSWORD)
    with connect() as db:
        for org in [first, second]:
            db.execute("INSERT INTO organizations VALUES (%s,'synthetic')", (org,))
        for uid, username, org, role in [
            (student, "student", first, "student"),
            (teacher, "teacher", first, "teacher"),
            (outsider, "outsider", second, "teacher"),
        ]:
            db.execute(
                "INSERT INTO users(id,username,password_hash) VALUES (%s,%s,%s)",
                (uid, username, password_hash),
            )
            db.execute("INSERT INTO memberships VALUES (%s,%s,%s)", (uid, org, role))
    return first, second, student, teacher, outsider


def logged_in(username="student"):
    client = TestClient(app, base_url="http://127.0.0.1:28000")
    response = client.post(
        "/api/v1/auth/login", headers=ORIGIN, json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    me = client.get("/api/v1/auth/me").json()
    return client, {**ORIGIN, "X-CRA-CSRF": me["csrf_token"]}


def test_login_origin_csrf_logout_and_expiration(identities):
    client = TestClient(app, base_url="http://127.0.0.1:28000")
    assert client.get("/api/v1/auth/me").status_code == 401
    body = {"username": "student", "password": PASSWORD}
    for headers in [{}, {"Origin": "https://foreign.invalid"}]:
        assert client.post("/api/v1/auth/login", headers=headers, json=body).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/login", headers=ORIGIN, json={**body, "password": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", headers=ORIGIN, json={**body, "role": "teacher"}
        ).status_code
        == 422
    )
    client, headers = logged_in()
    old_token = client.cookies.get(COOKIE)
    assert client.post("/api/v1/auth/logout", headers=ORIGIN).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/logout", headers={**headers, "Origin": "https://foreign.invalid"}
        ).status_code
        == 403
    )
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    client.cookies.set(COOKIE, old_token)
    assert client.get("/api/v1/auth/me").status_code == 401
    client, _ = logged_in()
    with connect() as db:
        db.execute("UPDATE auth_sessions SET expires_at=now()-interval '1 second'")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_server_roles_organization_and_live_revocation(identities):
    first, second, _student, teacher, _ = identities
    client, _ = logged_in()
    assert client.get(f"/api/v1/organizations/{first}/members").status_code == 403
    assert client.get(f"/api/v1/organizations/{second}/members").status_code == 404
    client, _ = logged_in("teacher")
    members = client.get(f"/api/v1/organizations/{first}/members")
    assert members.status_code == 200
    assert {r["username"] for r in members.json()} == {"student", "teacher"}
    assert "password_hash" not in members.text
    assert client.get(f"/api/v1/organizations/{second}/members").status_code == 404
    with connect() as db:
        db.execute("UPDATE memberships SET role='student' WHERE user_id=%s", (teacher,))
    assert client.get(f"/api/v1/organizations/{first}/members").status_code == 403
    with connect() as db:
        db.execute("UPDATE users SET active=false WHERE id=%s", (teacher,))
    assert client.get("/api/v1/auth/me").status_code == 401


def test_rate_limits_and_only_hashed_sessions(identities):
    client, _ = logged_in()
    token = client.cookies.get(COOKIE)
    with connect() as db:
        assert db.execute("SELECT token_hash FROM auth_sessions").fetchone()[
            "token_hash"
        ] == digest(token)
    for _ in range(7):
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"username": "student", "password": "wrong"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/auth/login", headers=ORIGIN, json={"username": "student", "password": PASSWORD}
        ).status_code
        == 429
    )


def test_rotation_invalid_input_and_host_boundary(identities):
    client, _ = logged_in()
    old_token = client.cookies.get(COOKIE)
    response = client.post(
        "/api/v1/auth/login", headers=ORIGIN, json={"username": "student", "password": PASSWORD}
    )
    assert response.status_code == 200
    with connect() as db:
        assert (
            db.execute(
                "SELECT revoked_at FROM auth_sessions WHERE token_hash=%s", (digest(old_token),)
            ).fetchone()["revoked_at"]
            is not None
        )
    response = client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"username": "student", "password": PASSWORD, "unexpected": PASSWORD},
    )
    assert response.status_code == 422 and PASSWORD not in response.text
    assert (
        client.post("/api/v1/auth/login", content=b"x" * 65537, headers=ORIGIN).status_code == 413
    )
    assert client.get("/api/v1/auth/me", headers={"Host": "foreign.invalid"}).status_code == 400
    assert client.get("/api/v1/auth/me").headers["Cache-Control"] == "no-store"


def test_classroom_logins_share_gateway_without_sharing_sessions(identities, monkeypatch):
    org = identities[0]
    password_hash = PASSWORDS.hash(PASSWORD)
    names = [f"classroom{i:02d}" for i in range(12)]
    with connect() as db:
        for name in names:
            uid = uuid4()
            db.execute(
                "INSERT INTO users(id,username,password_hash) VALUES (%s,%s,%s)",
                (uid, name, password_hash),
            )
            db.execute("INSERT INTO memberships VALUES (%s,%s,'student')", (uid, org))
    origin = "https://classroom.example.invalid"
    monkeypatch.setenv("CRA_ORIGINS", origin)
    tokens = set()
    for name in names:
        # Host is rewritten by cloudflared; the browser Origin remains HTTPS.
        client = TestClient(app, base_url="https://localhost:28000")
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": origin},
            json={"username": name, "password": PASSWORD},
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        tokens.add(client.cookies.get(COOKIE))
        assert client.get("/api/v1/auth/me").json()["username"] == name
        assert client.get("/api/v1/command-center/overview").status_code == 403
    assert len(tokens) == 12


def test_shared_gateway_limit_still_blocks_rotating_usernames(identities):
    client = TestClient(app, base_url="http://127.0.0.1:28000")
    with connect() as db:
        db.execute("INSERT INTO login_limits VALUES (%s,now(),60)", (digest("client:testclient"),))
    response = client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"username": "new_unknown_user", "password": PASSWORD},
    )
    assert response.status_code == 429
