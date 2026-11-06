"""Mission 14: fixed order, explicit individual release and generation-bound scoring."""

import hashlib
from uuid import uuid4

import pytest
import test_identity
import test_labs
from pydantic import ValidationError
from test_assignments import add_student, create, grant
from test_identity import logged_in
from test_teaching import create_session, patch

from cra.db import connect
from cra.mission_access import catalog, release_source
from cra.node_contracts import NodeCommand

identities = test_identity.identities
inventory = test_labs.inventory
MISSION = "request-forge"
OBJECTIVE = "extract-database-proof"


def assignment(client):
    return next(
        item for item in client.get("/api/v1/assignments").json() if item["mission_id"] == MISSION
    )


def test_assignment_fourteen_preserves_existing_order_and_node_contract():
    items = catalog()
    assert [m.id for m in items] == [
        "first-files",
        "first-contact",
        "web-trail",
        "web-trail-dns",
        "access-granted",
        "packet-hunter",
        "vulnerable-app",
        "operation-shadow",
        "incident-http",
        "incident-evidence",
        "incident-containment",
        "incident-ownership",
        "cybercon-incident",
        MISSION,
    ]
    assert items[-1].objectives[0].points == 700
    owner = uuid4()
    body = {
        "schema_version": 1,
        "job_id": uuid4(),
        "lab_id": uuid4(),
        "organization_id": uuid4(),
        "user_id": owner,
        "operation": "create",
        "generation": 1,
        "slot": 0,
        "mission_id": MISSION,
        "mission_version": 1,
        "resources": items[-1].resources.model_dump(),
    }
    for operators in ([], [owner]):
        assert NodeCommand.model_validate({**body, "operator_user_ids": operators})
    for operators in ([uuid4()], [owner, uuid4()]):
        with pytest.raises(ValidationError, match="individual"):
            NodeCommand.model_validate({**body, "operator_user_ids": operators})


def test_release_is_individual_even_without_class_and_cannot_dispatch_alarm(identities, inventory):
    org, _, uid, _, outsider_id = identities
    add_student(org, uid)
    student, sh = logged_in()
    second, hh = logged_in("secondstudent")
    teacher, th = logged_in("teacher")
    outsider, oh = logged_in("outsider")
    assert not assignment(student)["available"]
    assert create(student, sh, MISSION).status_code == 403
    assert grant(student, sh, uid, MISSION).status_code == 403
    assert grant(outsider, oh, uid, MISSION).status_code == 404
    assert grant(teacher, th, outsider_id, MISSION).status_code == 404
    for _ in range(2):
        assert grant(teacher, th, uid, MISSION).status_code == 200
    assert assignment(student)["release_source"] == "individual"
    assert not assignment(second)["available"]
    assert student.get("/api/v1/assignments/alert").json() is None
    assert create(second, hh, MISSION).status_code == 403
    with connect() as db:
        assert release_source(db, org, uid, "student", MISSION, 2) == "locked"
        assert db.execute("SELECT count(*) AS n FROM labs").fetchone()["n"] == 0
        assert db.execute("SELECT count(*) AS n FROM mission_releases").fetchone()["n"] == 1
    rejected = student.post(
        "/api/v1/labs",
        headers={**sh, "Idempotency-Key": str(uuid4())},
        json={"mission_id": MISSION, "mission_version": 1, "team_id": str(uuid4())},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "individual_mission_required"
    assert create(student, sh, MISSION).status_code == 202


def test_class_day_does_not_automatically_release_mission_ten(identities):
    teacher, th = logged_in("teacher")
    student, _ = logged_in()
    _, response = create_session(teacher, th)
    session = response.json()
    assert (
        teacher.put(
            "/api/v1/teaching/sessions/" + session["id"], headers=th, json=patch(session, day=8)
        ).status_code
        == 200
    )
    assert not assignment(student)["available"]


def test_scoring_wrong_foreign_old_and_repeated_flags(identities, inventory):
    student, sh = logged_in()
    teacher, th = logged_in("teacher")
    outsider, oh = logged_in("outsider")
    grant(teacher, th, identities[2], MISSION)
    result = create(student, sh, MISSION).json()
    lab = result["lab_id"]
    first = "CRA{synthetic-request-forge-generation-1}"
    second = "CRA{synthetic-request-forge-generation-2}"
    with connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=%s", (result["operation_id"],))
        db.execute("UPDATE labs SET state='READY',last_observed_at=now() WHERE id=%s", (lab,))
        db.execute(
            "INSERT INTO lab_flags VALUES (%s,1,%s,%s)",
            (lab, OBJECTIVE, hashlib.sha256(first.encode()).hexdigest()),
        )

    def submit(flag, generation=1, client=student, headers=sh):
        return client.post(
            f"/api/v1/labs/{lab}/objectives/{OBJECTIVE}/attempts",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"flag": flag, "generation": generation},
        )

    assert submit(first, client=outsider, headers=oh).status_code == 404
    assert not submit("wrong").json()["accepted"]
    assert submit(first).json()["points_awarded"] == 700
    assert submit(first).json()["points_awarded"] == 0
    assert assignment(student)["completed"]
    reset = student.post(
        f"/api/v1/labs/{lab}/reset", headers={**sh, "Idempotency-Key": str(uuid4())}
    )
    assert reset.status_code == 202
    # Explicit API fixture; this is not a VM reset acceptance test.
    with connect() as db:
        db.execute("UPDATE labs SET state='READY' WHERE id=%s", (lab,))
        db.execute(
            "INSERT INTO lab_flags VALUES (%s,2,%s,%s)",
            (lab, OBJECTIVE, hashlib.sha256(second.encode()).hexdigest()),
        )
    assert submit(first, 1).status_code == 409
    assert not submit(first, 2).json()["accepted"]
    answer = submit(second, 2).json()
    assert answer["accepted"] and answer["points_awarded"] == 0
    assert assignment(student)["earned_points"] == 700
