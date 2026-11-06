"""Classroom bounds retain legacy isolation and fail closed beyond capacity."""

import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from cra.capacity import MAX_ACTIVE_LABS, RESERVATION_SLOTS, lab_link, target_ip
from cra.contracts import missions
from cra.labs import admit
from cra.node_client import NodeUnavailable
from cra.node_contracts import NodeCommand, NodeInventory, NodeLab


def test_all_network_links_are_disjoint_and_legacy_addresses_are_stable():
    links = [lab_link(s, i) for s in range(RESERVATION_SLOTS) for i in range(4)]
    ports = [p for link in links for p in (link["port"], link["port"] + 200)]
    assert len(set(ports)) == RESERVATION_SLOTS * 8
    assert len({link["subnet"] for link in links}) == RESERVATION_SLOTS * 4
    assert all(23000 <= p <= 23999 for p in ports)
    assert all(70 <= link["subnet"] <= 199 for link in links)
    for s in range(6):
        for i in range(4):
            assert lab_link(s, i) == {"port": 23200 + s + 10 * i, "subnet": 82 + s + 10 * i}
    for s, i in [(RESERVATION_SLOTS, 0), (-1, 0), (0, 4), (0, -1), (True, 0), (0, False)]:
        with pytest.raises(ValueError):
            lab_link(s, i)


def test_last_slot_validates_and_next_slot_fails():
    lab_id = uuid4()
    prefix = "cra-" + lab_id.hex[:10] + "-1"
    payload = {
        "id": lab_id,
        "organization_id": uuid4(),
        "user_id": uuid4(),
        "generation": 1,
        "slot": RESERVATION_SLOTS - 1,
        "state": "READY",
        "operator": prefix + "o",
        "target": prefix + "t",
        "target_ip": target_ip(RESERVATION_SLOTS - 1),
        "operator_running": True,
        "target_running": True,
        "operator_present": True,
        "target_present": True,
        "ownership_valid": True,
    }
    assert NodeLab.model_validate(payload).target_ip == "192.168.166.10"
    for changes in ({"slot": RESERVATION_SLOTS}, {"target_ip": "192.168.97.10"}):
        with pytest.raises(ValidationError):
            NodeLab.model_validate({**payload, **changes})
    command = {
        "schema_version": 1,
        "job_id": uuid4(),
        "lab_id": lab_id,
        "organization_id": payload["organization_id"],
        "user_id": payload["user_id"],
        "operation": "create",
        "generation": 1,
        "slot": RESERVATION_SLOTS - 1,
        "mission_id": "first-files",
        "mission_version": 1,
        "resources": missions()[0].resources,
    }
    NodeCommand.model_validate(command)
    with pytest.raises(ValidationError):
        NodeCommand.model_validate({**command, "slot": RESERVATION_SLOTS})
    inventory = {
        "schema_version": 1,
        "architecture": "x86_64",
        "connected": True,
        "labs": [payload],
        "available_memory_mib": 64000,
        "physical_memory_mib": 64000,
        "running_declared_memory_mib": 0,
        "free_disk_gib": 700,
        "max_labs": MAX_ACTIVE_LABS,
        "observed_at": time.time(),
    }
    NodeInventory.model_validate(inventory)
    with pytest.raises(ValidationError):
        NodeInventory.model_validate({**inventory, "max_labs": MAX_ACTIVE_LABS + 1})


class Reservations:
    def __init__(self, count):
        self.rows = [
            {
                "id": uuid4(),
                "slot": i,
                "state": "READY",
                "operator_count": 1,
                "mission_id": "first-contact",
                "mission_version": 1,
                "desktop_enabled": False,
            }
            for i in range(count)
        ]

    def execute(self, *args):
        return self

    def fetchall(self):
        return self.rows


def test_last_active_admitted_next_rejected_including_node_outage(monkeypatch):
    inventory = {
        "schema_version": 1,
        "architecture": "x86_64",
        "connected": True,
        "labs": [],
        "available_memory_mib": 64000,
        "physical_memory_mib": 64000,
        "running_declared_memory_mib": 0,
        "free_disk_gib": 700,
        "max_labs": MAX_ACTIVE_LABS,
        "observed_at": time.time(),
    }
    monkeypatch.setattr("cra.labs.node_request", lambda path: inventory)
    assert admit(Reservations(MAX_ACTIVE_LABS - 1), missions()[0], 1) == MAX_ACTIVE_LABS - 1
    with pytest.raises(HTTPException) as caught:
        admit(Reservations(MAX_ACTIVE_LABS), missions()[0], 1)
    assert caught.value.status_code == 409

    def offline(path):
        raise NodeUnavailable()

    monkeypatch.setattr("cra.labs.node_request", offline)
    with pytest.raises(HTTPException) as caught:
        admit(
            Reservations(MAX_ACTIVE_LABS),
            missions()[0],
            1,
            existing={"id": uuid4(), "slot": RESERVATION_SLOTS - 1, "node_id": 1},
        )
    assert caught.value.status_code == 409


def test_database_slot_boundaries(database):
    from psycopg.errors import CheckViolation

    from cra.db import connect

    org, user, lab = uuid4(), uuid4(), uuid4()
    with connect() as db:
        db.execute("INSERT INTO organizations VALUES (%s,'synthetic')", (org,))
        db.execute(
            "INSERT INTO users(id,username,password_hash) VALUES (%s,'capacity-fixture','disabled-synthetic')",
            (user,),
        )
        db.execute("INSERT INTO memberships VALUES (%s,%s,'student')", (user, org))
        db.execute(
            "INSERT INTO labs(id,organization_id,user_id,mission_id,mission_version,state,slot) VALUES (%s,%s,%s,'first-files',1,'STOPPED',17)",
            (lab, org, user),
        )
        db.execute(
            "INSERT INTO node_orphans(lab_id,organization_id,user_id,generation,slot) VALUES (%s,%s,%s,1,17)",
            (lab, org, user),
        )
    for table in ("labs", "node_orphans"):
        with pytest.raises(CheckViolation), connect() as db:
            db.execute("UPDATE " + table + " SET slot=18")
