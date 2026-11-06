"""Actual PostgreSQL purpose isolation; inventory and physical node are explicit fixtures."""

import hashlib
import json
from uuid import uuid4

import pytest
import test_identity
import test_labs
import test_terminal
from pydantic import ValidationError
from test_identity import logged_in

from cra.contracts import Resources, missions
from cra.db import connect
from cra.jobs import claim
from cra.worker import process

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab


def test_desktop_grants_are_purpose_bound_and_independent(ready_lab):
    client, headers, lab_id, _ = ready_lab
    url = "/api/v1/labs/" + lab_id
    assert client.post(url + "/desktop-grants", headers=headers).status_code == 409
    with connect() as db:
        db.execute("UPDATE labs SET desktop_enabled=true WHERE id=%s", (lab_id,))
    terminal = test_terminal.issue(client, headers, lab_id)
    gui = client.post(url + "/desktop-grants", headers=headers).json()
    desktop = {"lab_id": lab_id, "grant": gui["grant"], "purpose": "desktop"}
    assert (
        client.post(
            "/internal/terminal/consume", headers=headers, json={**desktop, "purpose": "terminal"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/internal/terminal/consume", headers=headers, json={**terminal, "purpose": "desktop"}
        ).status_code
        == 403
    )
    claimed = client.post("/internal/terminal/consume", headers=headers, json=desktop)
    assert (
        claimed.status_code == 200 and json.loads(claimed.json()["payload"])["purpose"] == "desktop"
    )
    assert (
        client.post("/internal/terminal/consume", headers=headers, json=desktop).status_code == 403
    )
    assert (
        client.post("/internal/terminal/consume", headers=headers, json=terminal).status_code == 200
    )
    assert (
        client.post("/internal/terminal/check", headers=headers, json=terminal).status_code == 200
    )
    client.post(url + "/desktop-grants", headers=headers)
    assert client.post("/internal/terminal/check", headers=headers, json=desktop).status_code == 403
    assert (
        client.post("/internal/terminal/check", headers=headers, json=terminal).status_code == 200
    )
    outsider, oh = logged_in("outsider")
    assert outsider.post(url + "/desktop-grants", headers=oh).status_code == 404


def test_desktop_choice_is_immutable_idempotent_and_reserves_real_memory(
    identities, inventory, monkeypatch
):
    client, headers = logged_in()
    key = str(uuid4())
    body = {"mission_id": "first-contact", "mission_version": 1, "desktop_enabled": True}
    inventory["available_memory_mib"] = 6000
    assert (
        client.post(
            "/api/v1/labs", headers={**headers, "Idempotency-Key": key}, json=body
        ).status_code
        == 409
    )
    inventory["available_memory_mib"] = 7000
    created = client.post("/api/v1/labs", headers={**headers, "Idempotency-Key": key}, json=body)
    assert created.status_code == 202
    assert (
        client.post("/api/v1/labs", headers={**headers, "Idempotency-Key": key}, json=body).json()
        == created.json()
    )
    assert (
        client.post(
            "/api/v1/labs",
            headers={**headers, "Idempotency-Key": key},
            json={**body, "desktop_enabled": False},
        ).status_code
        == 409
    )
    lab_id = created.json()["lab_id"]
    assert client.get("/api/v1/labs/" + lab_id).json()["desktop_enabled"]
    commands = []

    def node(path, command):
        command = command.model_dump(mode="json")
        commands.append(command)
        assert (
            command["resources"]["operator_profile"] == "desktop"
            and command["resources"]["operator_memory_mib"] == 2048
        )
        return {
            "id": lab_id,
            "organization_id": command["organization_id"],
            "user_id": command["user_id"],
            "generation": 1,
            "state": "READY",
            "capture_ready": True,
            "target_ip": "192.168.82.10",
            "slot": 0,
            "operator_user_ids": [],
            "objective_verifiers": {"find-service": hashlib.sha256(b"synthetic-desktop-flag").hexdigest()},
        }

    monkeypatch.setattr("cra.worker.request", node)
    process(claim("desktop-fixture-worker"))
    assert len(commands) == 1
    with connect() as db:
        assert (
            db.execute("SELECT state FROM labs WHERE id=%s", (lab_id,)).fetchone()["state"]
            == "READY"
        )


def test_desktop_resource_profile_is_not_silent_overallocation():
    resources = missions()[0].resources.model_dump()
    for update in [
        {"operator_profile": "desktop"},
        {"operator_memory_mib": 2048},
        {"operator_profile": "desktop", "operator_memory_mib": 4096},
    ]:
        with pytest.raises(ValidationError):
            Resources.model_validate({**resources, **update})
    assert (
        Resources.model_validate(
            {**resources, "operator_profile": "desktop", "operator_memory_mib": 2048}
        ).operator_profile
        == "desktop"
    )
