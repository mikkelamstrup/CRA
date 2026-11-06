import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_identity
import test_labs
from test_identity import logged_in

from cra.db import connect

identities = test_identity.identities
inventory = test_labs.inventory


@pytest.fixture
def ready_lab(identities, inventory, monkeypatch, tmp_path):
    key = tmp_path / "synthetic-signing-key"
    key.write_bytes(b"synthetic-test-key-only-not-installed")
    monkeypatch.setenv("CRA_TERMINAL_SIGNING_KEY", str(key))
    client, headers = logged_in()
    created = test_labs.create(client, headers).json()
    with connect() as db:
        db.execute("UPDATE labs SET state='READY',last_observed_at=now() WHERE id=%s", (created["lab_id"],))
    return client, headers, created["lab_id"], key.read_bytes()


def issue(client, headers, lab_id):
    response = client.post("/api/v1/labs/" + lab_id + "/terminal-grants", headers=headers)
    assert response.status_code == 201
    return {"lab_id": lab_id, "grant": response.json()["grant"]}


def test_grant_is_atomic_single_use_and_signs_exact_owner(ready_lab):
    client, headers, lab_id, key = ready_lab
    body = issue(client, headers, lab_id)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: client.post("/internal/terminal/consume", headers=headers, json=body),
                range(8),
            )
        )
    assert sorted(r.status_code for r in results) == [200] + [403] * 7
    signed = next(r.json() for r in results if r.status_code == 200)
    assert hmac.compare_digest(
        signed["signature"], hmac.new(key, signed["payload"].encode(), hashlib.sha256).hexdigest()
    )
    claims = json.loads(signed["payload"])
    assert claims["lab_id"] == lab_id and claims["generation"] == 1
    assert "private_key" not in signed and body["grant"] not in signed["payload"]
    assert client.post("/internal/terminal/check", headers=headers, json=body).status_code == 200


def test_wrong_scope_session_expiration_and_generation_are_denied(ready_lab):
    client, headers, lab_id, _ = ready_lab
    body = issue(client, headers, lab_id)
    other, other_headers = logged_in("outsider")
    assert (
        other.post("/api/v1/labs/" + lab_id + "/terminal-grants", headers=other_headers).status_code
        == 404
    )
    assert (
        other.post("/internal/terminal/consume", headers=other_headers, json=body).status_code
        == 403
    )
    assert (
        client.post(
            "/internal/terminal/consume", headers=headers, json={**body, "lab_id": str(uuid4())}
        ).status_code
        == 403
    )
    fresh, fresh_headers = logged_in()
    assert (
        fresh.post("/internal/terminal/consume", headers=fresh_headers, json=body).status_code
        == 403
    )
    with connect() as db:
        db.execute("UPDATE terminal_grants SET expires_at=now()-interval '1 second'")
    assert client.post("/internal/terminal/consume", headers=headers, json=body).status_code == 403
    body = issue(client, headers, lab_id)
    with connect() as db:
        db.execute("UPDATE labs SET generation=generation+1")
    assert client.post("/internal/terminal/consume", headers=headers, json=body).status_code == 403


def test_live_state_new_grant_and_logout_revoke_stream_authorization(ready_lab):
    client, headers, lab_id, _ = ready_lab
    first = issue(client, headers, lab_id)
    assert client.post("/internal/terminal/consume", headers=headers, json=first).status_code == 200
    second = issue(client, headers, lab_id)
    assert client.post("/internal/terminal/check", headers=headers, json=first).status_code == 403
    assert (
        client.post("/internal/terminal/consume", headers=headers, json=second).status_code == 200
    )
    with connect() as db:
        db.execute("UPDATE labs SET state='STOPPING'")
    assert client.post("/internal/terminal/check", headers=headers, json=second).status_code == 403
    assert (
        client.post("/api/v1/labs/" + lab_id + "/terminal-grants", headers=headers).status_code
        == 409
    )
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.post("/internal/terminal/check", headers=headers, json=second).status_code == 401
