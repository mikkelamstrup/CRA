from uuid import uuid4

import test_identity
import test_labs
import test_terminal
from test_identity import logged_in

from cra.db import connect

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab


def create_session(client, headers):
    body = {"request_key": str(uuid4()), "title": "Synthetic class", "day": 1}
    result = client.post("/api/v1/teaching/sessions", headers=headers, json=body)
    return body, result


def patch(session, **kwargs):
    return {
        key: kwargs.get(key, session[key])
        for key in ("revision", "day", "phase", "terminal_enabled", "announcement", "closed")
    }


def test_session_roles_scope_revision_history_and_release(identities, inventory):
    student, sh = logged_in()
    teacher, th = logged_in("teacher")
    outsider, oh = logged_in("outsider")
    assert create_session(student, sh)[1].status_code == 403
    body, result = create_session(teacher, th)
    assert result.status_code == 201
    session = result.json()
    path = "/api/v1/teaching/sessions/" + session["id"]
    assert teacher.post("/api/v1/teaching/sessions", headers=th, json=body).json() == session
    assert create_session(teacher, th)[1].status_code == 409
    assert outsider.get("/api/v1/teaching/session").json() is None
    assert student.put(path, headers=sh, json=patch(session, phase="solo")).status_code == 403
    assert outsider.put(path, headers=oh, json=patch(session, phase="solo")).status_code == 404
    changed = teacher.put(
        path, headers=th, json=patch(session, phase="solo", announcement="<script>text</script>")
    ).json()
    assert (
        changed["revision"] == 2
        and student.get("/api/v1/teaching/session").json()["phase"] == "solo"
    )
    assert teacher.put(path, headers=th, json=patch(session, phase="team")).status_code == 409
    assert (
        student.post(
            "/api/v1/labs",
            headers={**sh, "Idempotency-Key": str(uuid4())},
            json={"mission_id": "first-contact", "mission_version": 1},
        ).status_code
        == 403
    )
    assert teacher.put(path, headers=th, json=patch(changed, closed=True)).status_code == 200
    assert student.get("/api/v1/teaching/session").json() is None
    assert create_session(teacher, th)[1].status_code == 201
    with connect() as db:
        assert (
            db.execute(
                "SELECT count(*) AS n FROM teaching_events WHERE kind LIKE 'session.%'"
            ).fetchone()["n"]
            == 4
        )


def test_help_retries_private_scope_conflicts_and_no_silent_resolution(identities):
    student, sh = logged_in()
    teacher, th = logged_in("teacher")
    other, oh = logged_in("outsider")
    body = {
        "request_key": str(uuid4()),
        "message": "I tried pwd, then need help with a relative path.",
    }
    r = student.post("/api/v1/teaching/help", headers=sh, json=body)
    assert r.status_code == 201
    assert student.post("/api/v1/teaching/help", headers=sh, json=body).json() == r.json()
    assert (
        student.post(
            "/api/v1/teaching/help", headers=sh, json={**body, "request_key": str(uuid4())}
        ).status_code
        == 409
    )
    assert other.get("/api/v1/teaching/help").json() == []
    assert len(teacher.get("/api/v1/teaching/help").json()) == 1
    path = "/api/v1/teaching/help/" + r.json()["id"]
    answer = {"revision": 1, "reply": "Use pwd to check where you are.", "status": "answered"}
    assert student.put(path, headers=sh, json=answer).status_code == 403
    assert other.put(path, headers=oh, json=answer).status_code == 404
    assert teacher.put(path, headers=th, json=answer).status_code == 200
    assert teacher.put(path, headers=th, json=answer).status_code == 409
    assert student.get("/api/v1/teaching/help").json()[0]["reply"] == answer["reply"]
    assert (
        teacher.put(
            path, headers=th, json={**answer, "revision": 2, "status": "resolved"}
        ).status_code
        == 200
    )
    assert (
        student.post(
            "/api/v1/teaching/help", headers=sh, json={**body, "request_key": str(uuid4())}
        ).status_code
        == 201
    )
    with connect() as db:
        assert (
            db.execute(
                "SELECT count(*) AS n FROM teaching_events WHERE evidence_level='submitted'"
            ).fetchone()["n"]
            == 2
        )


def test_teacher_terminal_block_revokes_live_grants_without_stopping_vm(ready_lab):
    student, sh, lab_id, _ = ready_lab
    grant = test_terminal.issue(student, sh, lab_id)
    assert student.post("/internal/terminal/consume", headers=sh, json=grant).status_code == 200
    teacher, th = logged_in("teacher")
    _, r = create_session(teacher, th)
    session = r.json()
    path = "/api/v1/teaching/sessions/" + session["id"]
    changed = teacher.put(path, headers=th, json=patch(session, terminal_enabled=False)).json()
    assert student.post("/internal/terminal/check", headers=sh, json=grant).status_code == 403
    assert (
        student.post("/api/v1/labs/" + lab_id + "/terminal-grants", headers=sh).status_code == 403
    )
    assert student.get("/api/v1/labs/" + lab_id).json()["state"] == "READY"
    teacher.put(path, headers=th, json=patch(changed, terminal_enabled=True))
    assert (
        student.post("/api/v1/labs/" + lab_id + "/terminal-grants", headers=sh).status_code == 201
    )


def test_old_open_help_remains_visible_after_more_than_100_resolved(identities):
    student, sh = logged_in()
    teacher, _ = logged_in("teacher")
    response = student.post(
        "/api/v1/teaching/help",
        headers=sh,
        json={"request_key": str(uuid4()), "message": "Old but still waiting"},
    )
    old = response.json()["id"]
    with connect() as db:
        for _ in range(105):
            db.execute(
                "INSERT INTO help_requests(id,organization_id,user_id,request_key,message,status) VALUES (%s,%s,%s,%s,'Later resolved','resolved')",
                (uuid4(), identities[0], identities[2], uuid4()),
            )
    queue = teacher.get("/api/v1/teaching/help").json()
    assert queue[0]["id"] == old and len(queue) == 21
