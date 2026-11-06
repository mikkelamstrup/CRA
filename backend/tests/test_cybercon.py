"""Real PostgreSQL: explicit individual dispatch, durable acknowledgements and isolation."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import test_identity
import test_labs
from test_assignments import add_student, create, grant
from test_identity import logged_in

from cra.db import connect

identities = test_identity.identities
inventory = test_labs.inventory

MISSION = "cybercon-incident"
ALERT = "/api/v1/assignments/alert"
ACK = "/api/v1/assignments/cybercon-incident/acknowledge"


def test_manual_release_without_session_and_ack_across_login(identities, inventory):
    org, _other, uid, _teacher_id, _outsider_id = identities
    add_student(org, uid)
    student, sh = logged_in()
    second, hh = logged_in("secondstudent")
    teacher, th = logged_in("teacher")
    outsider, oh = logged_in("outsider")
    items = student.get("/api/v1/assignments").json()
    assert items[12]["mission_id"] == MISSION
    assert not items[12]["available"]
    assert student.get(ALERT).json() is None
    assert student.post(ACK, headers=sh, json={}).status_code == 404
    assert create(student, sh, MISSION).status_code == 403
    assert grant(student, sh, uid, MISSION).status_code == 403
    assert grant(outsider, oh, uid, MISSION).status_code == 404
    assert grant(teacher, th, uid, MISSION).status_code == 200
    assert grant(teacher, th, uid, MISSION).status_code == 200
    alert = student.get(ALERT).json()
    assert alert["mission_id"] == MISSION and "Nordtele" in alert["message"]
    assert student.get(ALERT).json() == alert
    assert second.get(ALERT).json() is None
    assert teacher.get(ALERT).json() is None
    assert second.post(ACK, headers=hh, json={}).status_code == 404
    assert student.post(ACK, json={}).status_code == 403
    fresh, fh = logged_in()
    assert fresh.get(ALERT).json() == alert
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: fresh.post(ACK, headers=fh, json={}), range(2)))
    assert all(a.status_code == 200 for a in answers)
    assert fresh.get(ALERT).json() is None
    again, _ = logged_in()
    assert again.get(ALERT).json() is None
    with connect() as db:
        assert (
            db.execute(
                "SELECT count(*) AS n FROM teaching_events WHERE kind='assignment.alert.acknowledged'"
            ).fetchone()["n"]
            == 1
        )
    assert create(second, hh, MISSION).status_code == 403
    # Reject team requests on the server even after a valid release.
    result = student.post(
        "/api/v1/labs",
        headers={**sh, "Idempotency-Key": str(uuid4())},
        json={"mission_id": MISSION, "mission_version": 1, "team_id": str(uuid4())},
    )
    assert result.status_code == 422 and result.json()["detail"] == "individual_mission_required"
    assert create(student, sh, MISSION).status_code == 202


def test_class_day_never_dispatches_incident(identities):
    from test_teaching import create_session, patch

    teacher, th = logged_in("teacher")
    student, _ = logged_in()
    _, response = create_session(teacher, th)
    session = response.json()
    result = teacher.put(
        "/api/v1/teaching/sessions/" + session["id"], headers=th, json=patch(session, day=8)
    )
    assert result.status_code == 200
    assert not next(
        item for item in student.get("/api/v1/assignments").json() if item["mission_id"] == MISSION
    )["available"]
    assert student.get(ALERT).json() is None
