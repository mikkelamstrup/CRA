import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_identity
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_identity import logged_in

from cra.contracts import missions
from cra.db import connect
from cra.main import app
from cra.node_contracts import NodeCommand

identities = test_identity.identities


@pytest.fixture
def inventory(monkeypatch):
    # Explicit API-policy fixture. Real KVM acceptance is smoke_m2_lifecycle.py.
    data = {
        "schema_version": 1,
        "connected": True,
        "max_labs": 2,
        "observed_at": time.time(),
        "architecture": "x86_64",
        "available_memory_mib": 24000,
        "physical_memory_mib": 32000,
        "running_declared_memory_mib": 4096,
        "free_disk_gib": 100,
        "labs": [],
    }
    monkeypatch.setattr("cra.labs.node_request", lambda path: data)
    return data


def create(client, headers, key=None):
    return client.post(
        "/api/v1/labs",
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json={"mission_id": "first-contact", "mission_version": 1},
    )


def test_concurrent_create_is_scoped_idempotent_and_capacity_bounded(identities, inventory):
    client, headers = logged_in()
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: create(client, headers, key), range(8)))
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["lab_id"] for r in responses}) == 1
    assert len({r.json()["operation_id"] for r in responses}) == 1
    assert create(client, headers).status_code == 409
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM labs").fetchone()["n"] == 1
        assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1


def test_ownership_lifecycle_and_operation_visibility(identities, inventory):
    client, headers = logged_in()
    created = create(client, headers).json()
    lab_id = created["lab_id"]
    other, _ = logged_in("outsider")
    teacher, _ = logged_in("teacher")
    for stranger in [other, teacher]:
        assert stranger.get("/api/v1/labs/" + lab_id).status_code == 404
        assert stranger.get("/api/v1/operations/" + created["operation_id"]).status_code == 404
        assert stranger.get("/api/v1/labs").json() == []
    assert (
        client.post(
            "/api/v1/labs/" + lab_id + "/stop", headers={**headers, "Idempotency-Key": str(uuid4())}
        ).status_code
        == 409
    )
    with connect() as db:
        db.execute("UPDATE labs SET state='READY' WHERE id=%s", (lab_id,))
    key = str(uuid4())
    first = client.post(
        "/api/v1/labs/" + lab_id + "/stop", headers={**headers, "Idempotency-Key": key}
    )
    assert first.status_code == 202
    assert (
        client.post(
            "/api/v1/labs/" + lab_id + "/stop", headers={**headers, "Idempotency-Key": key}
        ).json()
        == first.json()
    )
    assert (
        client.post(
            "/api/v1/labs/" + lab_id + "/destroy", headers={**headers, "Idempotency-Key": key}
        ).status_code
        == 409
    )
    assert "slot" not in client.get("/api/v1/labs/" + lab_id).json()


def test_invalid_mission_architecture_capacity_and_browser_host_are_rejected(identities, inventory):
    client, headers = logged_in()
    assert TestClient(app, base_url="http://127.0.0.1:28000").get("/api/v1/labs").status_code == 401
    assert (
        client.post(
            "/api/v1/labs",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"mission_id": "absent", "mission_version": 1},
        ).status_code
        == 404
    )
    inventory["architecture"] = "aarch64"
    assert create(client, headers).json()["detail"] == "architecture_mismatch"
    inventory["architecture"] = "x86_64"
    inventory["available_memory_mib"] = 4096
    assert create(client, headers).json()["detail"] == "capacity_unavailable"
    assert (
        client.post(
            "/api/v1/labs",
            headers=headers,
            json={"mission_id": "first-contact", "mission_version": 1, "host": "arbitrary"},
        ).status_code
        == 422
    )
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0


def test_node_contract_forbids_arbitrary_paths_commands_slots_and_architecture():
    body = {
        "schema_version": 1,
        "job_id": uuid4(),
        "lab_id": uuid4(),
        "organization_id": uuid4(),
        "user_id": uuid4(),
        "operation": "create",
        "generation": 1,
        "slot": 0,
        "mission_id": "first-contact",
        "mission_version": 1,
        "resources": missions()[0].resources.model_dump(),
    }
    assert NodeCommand.model_validate(body).slot == 0
    for change in [
        {"slot": 18},
        {"slot": -1},
        {"slot": True},
        {"shell": "sh"},
        {"image_path": "/private"},
        {"operation": "arbitrary"},
        {"generation": 0},
        {"schema_version": 2},
    ]:
        with pytest.raises(ValidationError):
            NodeCommand.model_validate({**body, **change})


def test_exhausted_worker_is_recoverable_and_old_failure_cannot_override_new_intent(
    identities, inventory
):
    from cra.jobs import claim
    from cra.worker import reconcile_failed_jobs

    client, headers = logged_in()
    created = create(client, headers).json()
    job = claim("synthetic-worker")
    assert job["id"] == __import__("uuid").UUID(created["operation_id"])
    with connect() as db:
        db.execute("UPDATE labs SET state='PROVISIONING'")
        db.execute("UPDATE jobs SET attempts=max_attempts,lease_until=now()-interval '1 second'")
    assert claim("recovery-worker") is None
    assert reconcile_failed_jobs() == 1
    lab = client.get("/api/v1/labs/" + created["lab_id"]).json()
    assert lab["state"] == "ERROR" and lab["error_code"] == "retry_exhausted"
    assert (
        client.post(
            "/api/v1/labs/" + created["lab_id"] + "/destroy",
            headers={**headers, "Idempotency-Key": str(uuid4())},
        ).status_code
        == 202
    )
    assert reconcile_failed_jobs() == 0
    assert client.get("/api/v1/labs/" + created["lab_id"]).json()["state"] == "DESTROYING"
