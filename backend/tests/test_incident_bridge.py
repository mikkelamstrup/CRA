"""Real PG release/scoring boundaries; VM behaviour is exercised separately in KVM."""

import hashlib
from uuid import uuid4

import pytest
import test_identity
import test_labs
from pydantic import ValidationError
from test_assignments import create, grant
from test_identity import logged_in

from cra.db import connect
from cra.mission_access import catalog
from cra.node_contracts import NodeCommand

identities = test_identity.identities
inventory = test_labs.inventory
BRIDGE = ["incident-http", "incident-evidence", "incident-containment", "incident-ownership"]


def test_fourteen_assignments_keep_old_ids_and_individual_node_profiles():
    missions = catalog()
    assert [m.id for m in missions][8:] == BRIDGE + ["cybercon-incident", "request-forge"]
    assert len(missions) == 14
    owner = uuid4()
    for mission in missions[8:12]:
        body = {
            "schema_version": 1, "job_id": uuid4(), "lab_id": uuid4(),
            "organization_id": uuid4(), "user_id": owner, "operation": "create",
            "generation": 1, "slot": 0, "mission_id": mission.id, "mission_version": 1,
            "resources": mission.resources.model_dump(),
        }
        NodeCommand.model_validate(body)
        with pytest.raises(ValidationError, match="individual"):
            NodeCommand.model_validate({**body, "operator_user_ids": [owner, uuid4()]})


@pytest.mark.parametrize("mission", BRIDGE)
def test_manual_release_solo_access_and_generation_bound_score(identities, inventory, mission):
    student, sh = logged_in()
    teacher, th = logged_in("teacher")
    outsider, oh = logged_in("outsider")
    entry = lambda: next(
        x for x in student.get("/api/v1/assignments").json() if x["mission_id"] == mission
    )
    assert not entry()["available"]
    assert create(student, sh, mission).status_code == 403
    assert grant(student, sh, identities[2], mission).status_code == 403
    assert grant(teacher, th, identities[2], mission).status_code == 200
    assert entry()["release_source"] == "individual"
    assert student.get("/api/v1/assignments/alert").json() is None
    assert (
        student.post(
            "/api/v1/labs",
            headers={**sh, "Idempotency-Key": str(uuid4())},
            json={"mission_id": mission, "mission_version": 1, "team_id": str(uuid4())},
        ).status_code
        == 422
    )
    result = create(student, sh, mission).json()
    lab = result["lab_id"]
    definition = next(m for m in catalog() if m.id == mission)
    objective = definition.objectives[0]
    flag = "CRA{synthetic-" + mission + "-generation-1}"
    with connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=%s", (result["operation_id"],))
        db.execute("UPDATE labs SET state='READY',last_observed_at=now() WHERE id=%s", (lab,))
        db.execute(
            "INSERT INTO lab_flags VALUES (%s,1,%s,%s)",
            (lab, objective.id, hashlib.sha256(flag.encode()).hexdigest()),
        )

    def submit(value, generation=1, client=student, headers=sh):
        return client.post(
            f"/api/v1/labs/{lab}/objectives/{objective.id}/attempts",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"flag": value, "generation": generation},
        )

    assert submit(flag, client=outsider, headers=oh).status_code == 404
    assert not submit("wrong").json()["accepted"]
    assert submit(flag).json()["points_awarded"] == objective.points
    assert submit(flag).json()["points_awarded"] == 0
    assert entry()["completed"]
    assert submit(flag, generation=2).status_code == 409
