"""Third assignment is independently released and uses an approved node recipe."""

from uuid import UUID

import test_assignments
from test_assignments import add_student, create, grant
from test_identity import logged_in
from test_teaching import create_session

from cra.contracts import missions
from cra.node_contracts import NodeCommand

identities = test_assignments.identities
inventory = test_assignments.inventory


def test_third_mission_individual_release_and_node_contract(identities, inventory):
    org, _, uid, _, _ = identities
    add_student(org, uid)
    teacher, th = logged_in("teacher")
    student, sh = logged_in()
    second, hh = logged_in("secondstudent")
    create_session(teacher, th)
    assignments = student.get("/api/v1/assignments").json()
    assert [a["mission_id"] for a in assignments[:3]] == ["first-files", "first-contact", "web-trail"]
    assert not assignments[2]["available"]
    assert create(student, sh, "web-trail").status_code == 403
    assert grant(student, sh, uid, mission="web-trail").status_code == 403
    assert grant(teacher, th, uid, mission="web-trail", version=2).status_code == 404
    assert grant(teacher, th, uid, mission="web-trail").status_code == 200
    after = student.get("/api/v1/assignments").json()
    assert after[2]["available"] and after[2]["release_source"] == "individual"
    assert not after[1]["available"]
    assert not second.get("/api/v1/assignments").json()[2]["available"]
    assert create(second, hh, "web-trail").status_code == 403
    operation = create(student, sh, "web-trail")
    assert operation.status_code == 202
    mission = next(m for m in missions() if m.id == "web-trail")
    command = NodeCommand(
        schema_version=1,
        job_id=UUID(operation.json()["operation_id"]),
        lab_id=UUID(operation.json()["lab_id"]),
        organization_id=org,
        user_id=uid,
        operation="create",
        generation=1,
        slot=0,
        mission_id=mission.id,
        mission_version=1,
        resources=mission.resources,
    )
    assert command.mission_id == "web-trail"
    assert mission.objectives[0].id == "find-report"


def test_third_mission_retains_existing_class_release_policy(identities):
    from cra.db import connect
    from cra.mission_access import release_source

    org, _, uid, _, _ = identities
    with connect() as db:
        assert release_source(db, org, uid, "student", "web-trail", 1) == "class"
    teacher, th = logged_in("teacher")
    create_session(teacher, th)
    with connect() as db:
        assert release_source(db, org, uid, "student", "web-trail", 1) == "locked"
        db.execute("UPDATE teaching_sessions SET day=2 WHERE organization_id=%s", (org,))
        assert release_source(db, org, uid, "student", "web-trail", 1) == "locked"
        db.execute("UPDATE teaching_sessions SET day=3 WHERE organization_id=%s", (org,))
        assert release_source(db, org, uid, "student", "web-trail", 1) == "class"
