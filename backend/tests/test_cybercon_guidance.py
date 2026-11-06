"""Real PostgreSQL: help dispatch is tied to the selected current solo lab."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import test_identity
import test_labs
import test_terminal
from test_assignments import add_student, grant
from test_identity import logged_in

from cra.db import connect

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab
ALERT = "/api/v1/assignments/alert"
RELEASE = "/api/v1/command-center/assignments/cybercon-incident/guidance"
MISSION = "cybercon-incident"


def body():
    return {
        "request_key": str(uuid4()),
        "title": "Hjælp til opgave 9",
        "message": "Læs sporene. Ret politikken. Kør cybercon-check.",
    }


def test_targeted_help_after_original_ack_and_independent_ack(ready_lab, identities):
    student, sh, lab_id, _ = ready_lab
    org, _, uid, _, _ = identities
    add_student(org, uid)
    second, hh = logged_in("secondstudent")
    teacher, th = logged_in("teacher")
    outsider, oh = logged_in("outsider")
    assert grant(teacher, th, uid, MISSION).status_code == 200
    assert (
        student.post(
            "/api/v1/assignments/cybercon-incident/acknowledge", headers=sh, json={}
        ).status_code
        == 200
    )
    assert student.get(ALERT).json() is None
    with connect() as db:
        db.execute("UPDATE labs SET mission_id=%s WHERE id=%s", (MISSION, lab_id))
    payload = body()
    assert student.post(RELEASE, headers=sh, json=payload).status_code == 403
    assert teacher.post(RELEASE, json=payload).status_code == 403
    assert outsider.post(RELEASE, headers=oh, json=payload).json() == {"released_count": 0}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: teacher.post(RELEASE, headers=th, json=payload), range(2))
        )
    assert all(r.status_code == 200 and r.json() == {"released_count": 1} for r in results)
    assert (
        teacher.post(RELEASE, headers=th, json={**payload, "message": "changed"}).status_code == 409
    )
    alert = student.get(ALERT).json()
    assert alert["guidance_id"] and alert["message"] == payload["message"]
    assert second.get(ALERT).json() is None
    assert teacher.get(ALERT).json() is None
    assert outsider.get(ALERT).json() is None
    ack = "/api/v1/assignments/guidance/" + alert["guidance_id"] + "/acknowledge"
    assert student.post(ack, json={}).status_code == 403
    assert second.post(ack, headers=hh, json={}).status_code == 404
    assert outsider.post(ack, headers=oh, json={}).status_code == 403
    assert teacher.post(ack, headers=th, json={}).status_code == 403
    # A stale acknowledgement of the initial incident must not swallow the new help.
    student.post("/api/v1/assignments/cybercon-incident/acknowledge", headers=sh, json={})
    assert student.get(ALERT).json()["guidance_id"] == alert["guidance_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(
            r.status_code == 200
            for r in pool.map(lambda _: student.post(ack, headers=sh, json={}), range(2))
        )
    fresh, _ = logged_in()
    assert fresh.get(ALERT).json() is None
    with connect() as db:
        assert (
            db.execute(
                "SELECT count(*) n FROM teaching_events WHERE kind='assignment.guidance.released'"
            ).fetchone()["n"]
            == 1
        )
        assert (
            db.execute(
                "SELECT count(*) n FROM teaching_events WHERE kind='assignment.guidance.acknowledged'"
            ).fetchone()["n"]
            == 1
        )
    # Another separately released message is still delivered after the first help was read.
    assert teacher.post(RELEASE, headers=th, json=body()).json() == {"released_count": 1}
    assert fresh.get(ALERT).json()["guidance_id"] != alert["guidance_id"]


def test_other_mission_stopped_later_started_and_reset_are_excluded(ready_lab):
    student, sh, lab_id, _ = ready_lab
    teacher, th = logged_in("teacher")
    assert teacher.post(RELEASE, headers=th, json=body()).json() == {"released_count": 0}
    assert student.get(ALERT).json() is None
    with connect() as db:
        db.execute("UPDATE labs SET mission_id=%s,state='STOPPED' WHERE id=%s", (MISSION, lab_id))
    payload = body()
    assert teacher.post(RELEASE, headers=th, json=payload).json() == {"released_count": 0}
    with connect() as db:
        db.execute("UPDATE labs SET state='READY' WHERE id=%s", (lab_id,))
    assert teacher.post(RELEASE, headers=th, json=payload).json() == {"released_count": 0}
    assert student.get(ALERT).json() is None
    assert teacher.post(RELEASE, headers=th, json=body()).json() == {"released_count": 1}
    alert = student.get(ALERT).json()
    with connect() as db:
        db.execute("UPDATE labs SET desired_state='STOPPED' WHERE id=%s", (lab_id,))
    assert student.get(ALERT).json() is None
    with connect() as db:
        db.execute(
            "UPDATE labs SET desired_state='READY',mission_id='first-contact' WHERE id=%s",
            (lab_id,),
        )
    assert student.get(ALERT).json() is None
    with connect() as db:
        db.execute(
            "UPDATE labs SET mission_id=%s,generation=generation+1 WHERE id=%s", (MISSION, lab_id)
        )
    assert student.get(ALERT).json() is None
    assert (
        student.post(
            "/api/v1/assignments/guidance/" + alert["guidance_id"] + "/acknowledge",
            headers=sh,
            json={},
        ).status_code
        == 404
    )
