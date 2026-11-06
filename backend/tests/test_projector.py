"""Real PostgreSQL projection boundary; synthetic lab/traffic fixtures are explicit."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
import test_identity
import test_labs
import test_terminal
from fastapi.testclient import TestClient
from test_identity import ORIGIN, logged_in
from test_reconcile import now, synthetic_snapshot
from test_traffic import add_window

from cra.auth import digest
from cra.db import connect
from cra.main import app
from cra.projector import COOKIE, COOKIE_PATH
from cra.reconcile import apply_inventory

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab
BASE = "/api/v1/command-center/projectors"


def blank():
    return TestClient(app, base_url="http://127.0.0.1:28000")


def launch(lab):
    teacher, headers = logged_in("teacher")
    response = teacher.post(BASE, headers=headers, json={"lab_ids": [lab]})
    assert response.status_code == 201
    return teacher, headers, response.json()


def screen(value):
    client = blank()
    response = client.post(
        "/api/v1/projector/" + value["grant"]["id"] + "/exchange",
        headers=ORIGIN,
        json={"token": value["token"]},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert (
        "httponly" in cookie
        and "samesite=strict" in cookie
        and "path=" + COOKIE_PATH + "/" + value["grant"]["id"] in cookie
    )
    client.projector_id = value["grant"]["id"]
    return client


def project_feed(client):
    return client.get("/api/v1/projector/" + client.projector_id + "/feed")


def test_scope_csrf_selected_labs_and_cookie_separation(ready_lab):
    student, headers, lab, _ = ready_lab
    assert student.post(BASE, headers=headers, json={"lab_ids": [lab]}).status_code == 403
    teacher, th = logged_in("teacher")
    foreign, fh = logged_in("outsider")
    assert foreign.post(BASE, headers=fh, json={"lab_ids": [lab]}).status_code == 404
    assert teacher.post(BASE, headers=ORIGIN, json={"lab_ids": [lab]}).status_code == 403
    for ids in ([], [lab, lab], [lab, str(uuid4())]):
        assert teacher.post(BASE, headers=th, json={"lab_ids": ids}).status_code in (404, 422)
    assert teacher.get("/api/v1/projector/" + str(uuid4()) + "/feed").status_code == 401
    value = teacher.post(BASE, headers=th, json={"lab_ids": [lab]}).json()
    projector = screen(value)
    assert projector.get("/api/v1/command-center/overview").status_code == 401
    assert projector.get("/api/v1/auth/me").status_code == 401
    assert project_feed(projector).status_code == 200
    assert (
        foreign.post(BASE + "/" + value["grant"]["id"] + "/revoke", headers=fh, json={}).status_code
        == 404
    )
    with connect() as db:
        row = db.execute(
            "SELECT * FROM projector_grants WHERE id=%s", (value["grant"]["id"],)
        ).fetchone()
        assert row["exchange_hash"] == digest(value["token"]) and row["session_hash"] == digest(
            projector.cookies.get(COOKIE)
        )
        assert value["token"] not in str(row) and projector.cookies.get(COOKIE) not in str(row)


def test_one_use_concurrent_exchange_and_origin(ready_lab):
    _, _, lab, _ = ready_lab
    _, _, value = launch(lab)
    client = blank()
    assert (
        client.post(
            "/api/v1/projector/" + value["grant"]["id"] + "/exchange",
            json={"token": value["token"]},
        ).status_code
        == 403
    )

    def exchange(_):
        with blank() as c:
            return c.post(
                "/api/v1/projector/" + value["grant"]["id"] + "/exchange",
                headers=ORIGIN,
                json={"token": value["token"]},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(exchange, range(2))) == [200, 401]


def test_exchange_expiry_checked_after_lock_wait(ready_lab):
    _, _, lab, _ = ready_lab
    _, _, value = launch(lab)
    with connect() as db:
        db.execute(
            "UPDATE projector_grants SET exchange_until=clock_timestamp()+interval '0.2 seconds' WHERE id=%s",
            (value["grant"]["id"],),
        )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connect() as locked:
            locked.execute(
                "SELECT id FROM projector_grants WHERE id=%s FOR UPDATE", (value["grant"]["id"],)
            )
            future = pool.submit(
                lambda: (
                    blank()
                    .post(
                        "/api/v1/projector/" + value["grant"]["id"] + "/exchange",
                        headers=ORIGIN,
                        json={"token": value["token"]},
                    )
                    .status_code
                )
            )
            locked.execute("SELECT pg_sleep(0.3)")
        assert future.result(timeout=5) == 401


@pytest.mark.parametrize("change", ["revoke", "expire", "demote", "deactivate", "membership"])
def test_live_access_revocation(ready_lab, identities, change):
    _, _, lab, _ = ready_lab
    teacher, headers, value = launch(lab)
    projector = screen(value)
    if change == "revoke":
        assert (
            teacher.post(
                BASE + "/" + value["grant"]["id"] + "/revoke", headers=headers, json={}
            ).status_code
            == 200
        )
        assert (
            teacher.post(
                BASE + "/" + value["grant"]["id"] + "/revoke", headers=headers, json={}
            ).status_code
            == 200
        )
    else:
        with connect() as db:
            if change == "expire":
                db.execute(
                    "UPDATE projector_grants SET expires_at=clock_timestamp()-interval '1 second'"
                )
            elif change == "demote":
                db.execute(
                    "UPDATE memberships SET role='student' WHERE user_id=%s", (identities[3],)
                )
            elif change == "deactivate":
                db.execute("UPDATE users SET active=false WHERE id=%s", (identities[3],))
            else:
                db.execute("DELETE FROM auth_sessions WHERE user_id=%s", (identities[3],))
                db.execute("DELETE FROM memberships WHERE user_id=%s", (identities[3],))
    response = project_feed(projector)
    assert response.status_code == 401 and set(response.json()) == {"detail"}


def test_allowlist_activity_generation_staleness_and_stable_aliases(ready_lab):
    _, _, lab, _ = ready_lab
    snap = add_window(synthetic_snapshot(lab))
    apply_inventory(snap, now())
    _, _, value = launch(lab)
    projector = screen(value)
    response = project_feed(projector)
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    row = body["labs"][0]
    assert set(body) == {"schema_version", "server_time", "expires_at", "day", "phase", "labs"}
    assert set(row) == {
        "ordinal",
        "alias",
        "state",
        "stale",
        "generation",
        "completed",
        "objectives",
        "points",
        "activity",
        "observation_incomplete",
    }
    assert row["alias"] == "Hold 01" and row["activity"] == "observed" and row["points"] == 0
    assert not any(
        secret in response.text
        for secret in ("student", "teacher", "192.168", "8080", lab, "operator_user_id", "CRA{")
    )
    with connect() as db:
        db.execute("UPDATE flow_history SET observed_at=%s", (now() - timedelta(seconds=11),))
    assert project_feed(projector).json()["labs"][0]["activity"] == "quiet"
    with connect() as db:
        db.execute(
            "UPDATE lab_capture_snapshots SET received_at=clock_timestamp()-interval '21 seconds'"
        )
    assert project_feed(projector).json()["labs"][0]["activity"] == "unconfirmed"
    with connect() as db:
        db.execute("UPDATE labs SET generation=2 WHERE id=%s", (lab,))
    row = project_feed(projector).json()["labs"][0]
    assert row["generation"] == 2 and row["activity"] == "unconfirmed" and row["alias"] == "Hold 01"
    with connect() as db:
        db.execute("UPDATE labs SET state='DESTROYED' WHERE id=%s", (lab,))
    assert project_feed(projector).json()["labs"] == []


def test_active_grant_bound_and_expired_cleanup(ready_lab):
    _, _, lab, _ = ready_lab
    teacher, headers = logged_in("teacher")
    for _ in range(5):
        assert teacher.post(BASE, headers=headers, json={"lab_ids": [lab]}).status_code == 201
    assert teacher.post(BASE, headers=headers, json={"lab_ids": [lab]}).status_code == 409
    with connect() as db:
        db.execute("UPDATE projector_grants SET expires_at=clock_timestamp()-interval '2 days'")
    assert teacher.post(BASE, headers=headers, json={"lab_ids": [lab]}).status_code == 201
    assert len(teacher.get(BASE).json()) == 1
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM projector_labs").fetchone()["n"] == 1


def test_two_grants_in_same_cookie_jar_keep_selected_scope(ready_lab):
    _, _, lab, _ = ready_lab
    teacher, headers = logged_in("teacher")
    other = test_labs.create(teacher, headers).json()["lab_id"]
    a = teacher.post(BASE, headers=headers, json={"lab_ids": [lab]}).json()
    b = teacher.post(BASE, headers=headers, json={"lab_ids": [other]}).json()
    client = blank()

    def path(value, suffix):
        return "/api/v1/projector/" + value["grant"]["id"] + "/" + suffix

    assert (
        client.post(path(b, "exchange"), headers=ORIGIN, json={"token": a["token"]}).status_code
        == 401
    )
    for value in (a, b):
        assert (
            client.post(
                path(value, "exchange"), headers=ORIGIN, json={"token": value["token"]}
            ).status_code
            == 200
        )
    assert client.get(path(a, "feed")).json()["labs"][0]["state"] == "READY"
    assert client.get(path(b, "feed")).json()["labs"][0]["state"] == "QUEUED"
    cookie_a = next(
        c.value for c in client.cookies.jar if c.path == COOKIE_PATH + "/" + a["grant"]["id"]
    )
    assert (
        blank().get(path(b, "feed"), headers={"Cookie": COOKIE + "=" + cookie_a}).status_code == 401
    )
    teacher.post(BASE + "/" + b["grant"]["id"] + "/revoke", headers=headers, json={})
    assert client.get(path(b, "feed")).status_code == 401
    assert client.get(path(a, "feed")).status_code == 200


def test_revoked_churn_cannot_hide_active_revoke_control(ready_lab):
    _, _, lab, _ = ready_lab
    teacher, headers, active = launch(lab)
    for _ in range(24):
        value = teacher.post(BASE, headers=headers, json={"lab_ids": [lab]}).json()
        assert (
            teacher.post(
                BASE + "/" + value["grant"]["id"] + "/revoke", headers=headers, json={}
            ).status_code
            == 200
        )
    listed = teacher.get(BASE).json()
    assert (
        len(listed) == 25 and listed[0]["id"] == active["grant"]["id"] and not listed[0]["revoked"]
    )
