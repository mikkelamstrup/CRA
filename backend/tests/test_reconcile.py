"""Actual PostgreSQL transactions; node snapshots/effects explicitly synthetic."""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_identity
import test_labs
import test_scoring
import test_terminal

from cra.db import connect
from cra.jobs import claim
from cra.node_contracts import NodeInventory
from cra.reconcile import apply_inventory
from cra.worker import process

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab


def synthetic_snapshot(lab_id):
    with connect() as db:
        row = db.execute("SELECT * FROM labs WHERE id=%s", (lab_id,)).fetchone()
        assert row
        db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=%s", (row["current_job_id"],))
        participants = db.execute(
            "SELECT user_id FROM lab_participants WHERE lab_id=%s ORDER BY ordinal", (lab_id,)
        ).fetchall()
    actors = [p["user_id"] for p in participants] or [row["user_id"]]
    prefix = "cra-" + row["id"].hex[:10] + "-1"
    observed = {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "user_id": row["user_id"],
        "generation": 1,
        "slot": row["slot"],
        "state": "READY",
        "operator": prefix + "o",
        "target": prefix + "t",
        "target_ip": f"192.168.{82 + row['slot']}.10",
        "operator_running": True,
        "target_running": True,
        "operator_present": True,
        "target_present": True,
        "ownership_valid": True,
        "operators": [
            {"user_id": actor, "name": prefix + "opqr"[i], "present": True, "running": True}
            for i, actor in enumerate(actors)
        ],
        "capture_ready": True,
        "captures": [
            {
                "operator_user_id": actor,
                "epoch": uuid4(),
                "connected": True,
                "observed_at": time.time(),
                "last_window": 0,
                "metadata_dropped": 0,
                "unknown_packets": 0,
                "flow_overflow": 0,
                "sequence_gaps": 0,
                "partial_windows": 0,
                "ring_overwrites": 0,
                "packets": 0,
                "bytes": 0,
                "windows": [],
            }
            for actor in actors
        ],
    }
    return NodeInventory.model_validate(
        {
            "schema_version": 1,
            "architecture": "x86_64",
            "connected": True,
            "available_memory_mib": 24000,
            "physical_memory_mib": 32000,
            "running_declared_memory_mib": 4096,
            "free_disk_gib": 100,
            "max_labs": 2,
            "observed_at": time.time(),
            "labs": [observed],
        }
    )


def now():
    with connect() as db:
        return db.execute("SELECT clock_timestamp() AS current").fetchone()["current"]


def test_stale_readiness_denies_grant_live_session_and_new_score(ready_lab):
    client, headers, lab_id, _ = ready_lab
    test_scoring.verifier(lab_id)
    body = test_terminal.issue(client, headers, lab_id)
    assert client.post("/internal/terminal/consume", headers=headers, json=body).status_code == 200
    with connect() as db:
        db.execute("UPDATE labs SET last_observed_at=now()-interval '21 seconds'")
    lab = client.get("/api/v1/labs/" + lab_id).json()
    assert lab["state"] == "ERROR" and lab["error_code"] == "observation_stale"
    assert (
        client.post("/api/v1/labs/" + lab_id + "/terminal-grants", headers=headers).status_code
        == 409
    )
    assert client.post("/internal/terminal/check", headers=headers, json=body).status_code == 403
    assert test_scoring.submit(client, headers, lab_id).status_code == 409


def test_outage_recovers_but_failed_lifecycle_is_never_promoted(ready_lab):
    client, _, lab_id, _ = ready_lab
    snapshot = synthetic_snapshot(lab_id)
    assert apply_inventory(None, now()) == 1
    assert client.get("/api/v1/labs/" + lab_id).json()["error_code"] == "node_unavailable"
    assert apply_inventory(snapshot, now()) == 1
    assert client.get("/api/v1/labs/" + lab_id).json()["state"] == "READY"
    with connect() as db:
        db.execute("UPDATE jobs SET status='FAILED',error_code='node_unavailable'")
        db.execute("UPDATE labs SET state='ERROR',error_code='node_unavailable'")
    apply_inventory(snapshot, now())
    assert client.get("/api/v1/labs/" + lab_id).json()["state"] == "ERROR"
    assert client.get("/api/v1/labs/" + lab_id).json()["error_code"] == "lifecycle_incomplete"


def test_old_snapshot_and_pending_jobs_cannot_override_new_intent(ready_lab):
    client, headers, lab_id, _ = ready_lab
    snapshot = synthetic_snapshot(lab_id)
    before = now()
    response = client.post(
        "/api/v1/labs/" + lab_id + "/stop", headers={**headers, "Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 202
    assert apply_inventory(snapshot, before) == 0
    assert apply_inventory(snapshot, now()) == 0
    assert apply_inventory(None, now()) == 0
    assert client.get("/api/v1/labs/" + lab_id).json()["state"] == "STOPPING"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"operator_running": False}, "runtime_drift"),
        ({"operator_running": False, "operator_present": False}, "resource_missing"),
        ({"ownership_valid": False}, "ownership_mismatch"),
        ({"user_id": uuid4()}, "ownership_mismatch"),
    ],
)
def test_observed_failure_is_explicit(ready_lab, change, code):
    client, _, lab_id, _ = ready_lab
    snapshot = synthetic_snapshot(lab_id)
    snapshot.labs[0] = snapshot.labs[0].model_copy(update=change)
    assert apply_inventory(snapshot, now()) == 1
    lab = client.get("/api/v1/labs/" + lab_id).json()
    assert lab["state"] == "ERROR" and lab["error_code"] == code


def orphan_fixture(ready_lab):
    snapshot = synthetic_snapshot(ready_lab[2])
    with connect() as db:
        db.execute("DELETE FROM labs WHERE id=%s", (ready_lab[2],))
    return snapshot


def test_orphan_requires_history_two_observations_and_delay_then_one_durable_cleanup(
    ready_lab, monkeypatch
):
    snapshot = orphan_fixture(ready_lab)
    apply_inventory(snapshot, now())
    with connect() as db:
        assert db.execute("SELECT status FROM node_orphans").fetchone()["status"] == "OBSERVED"
    apply_inventory(snapshot, now())
    with connect() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM jobs WHERE kind='orphan-destroy'").fetchone()["n"]
            == 0
        )
        db.execute("UPDATE node_orphans SET first_seen_at=now()-interval '31 seconds'")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: apply_inventory(snapshot, now()), range(4)))
    with connect() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM jobs WHERE kind='orphan-destroy'").fetchone()["n"]
            == 1
        )
    job = claim("synthetic-orphan-worker")
    assert job["kind"] == "orphan-destroy"
    calls = []

    def response(path, command):
        calls.append(command)
        return {
            "id": str(command.lab_id),
            "organization_id": str(command.organization_id),
            "user_id": str(command.user_id),
            "generation": command.generation,
            "state": "DESTROYED",
            "slot": None,
        }

    monkeypatch.setattr("cra.orphans.request", response)
    process(job)
    assert (
        len(calls) == 1
        and calls[0].operation == "destroy"
        and calls[0].lab_id == snapshot.labs[0].id
    )
    with connect() as db:
        assert db.execute("SELECT status FROM node_orphans").fetchone()["status"] == "RESOLVED"
        assert (
            db.execute("SELECT status FROM jobs WHERE id=%s", (job["id"],)).fetchone()["status"]
            == "SUCCEEDED"
        )


def test_unverified_orphan_is_quarantined_without_any_cleanup_job(ready_lab):
    snapshot = orphan_fixture(ready_lab)
    with connect() as db:
        db.execute("DELETE FROM jobs")
    apply_inventory(snapshot, now())
    with connect() as db:
        db.execute("UPDATE node_orphans SET first_seen_at=now()-interval '1 hour'")
    apply_inventory(snapshot, now())
    with connect() as db:
        assert db.execute("SELECT status FROM node_orphans").fetchone()["status"] == "QUARANTINED"
        assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0


def test_restored_active_lab_fences_queued_orphan_cleanup(ready_lab, monkeypatch):
    snapshot = synthetic_snapshot(ready_lab[2])
    observed = snapshot.labs[0]
    from cra.jobs import enqueue

    with connect() as db:
        job = enqueue(
            db,
            observed.organization_id,
            observed.user_id,
            "synthetic-orphan",
            "orphan-destroy",
            {"lab_id": str(observed.id), "generation": 1},
        )
        db.execute(
            "INSERT INTO node_orphans(lab_id,organization_id,user_id,generation,slot,status,job_id) VALUES (%s,%s,%s,1,%s,'QUEUED',%s)",
            (observed.id, observed.organization_id, observed.user_id, observed.slot, job["id"]),
        )
    job = claim("synthetic-worker")

    def forbidden(*args):
        raise AssertionError("Must not contact node for active lab")

    monkeypatch.setattr("cra.orphans.request", forbidden)
    process(job)
    with connect() as db:
        assert db.execute(
            "SELECT status,error_code FROM jobs WHERE id=%s", (job["id"],)
        ).fetchone() == {"status": "FAILED", "error_code": "generation_changed"}


def test_restore_after_preflight_waits_for_cleanup_fence_then_tombstone_rejects_it(
    ready_lab, monkeypatch
):
    import queue
    import threading
    import time

    from psycopg.errors import CheckViolation

    snapshot = orphan_fixture(ready_lab)
    observed = snapshot.labs[0]
    apply_inventory(snapshot, now())
    with connect() as db:
        db.execute("UPDATE node_orphans SET first_seen_at=now()-interval '31 seconds'")
    apply_inventory(snapshot, now())
    job = claim("synthetic-cleanup")
    reached = threading.Event()
    release = threading.Event()
    pids = queue.Queue()

    def node_effect(path, command):
        reached.set()
        assert release.wait(10)
        return {
            "id": str(command.lab_id),
            "organization_id": str(command.organization_id),
            "user_id": str(command.user_id),
            "generation": command.generation,
            "state": "DESTROYED",
            "slot": None,
        }

    monkeypatch.setattr("cra.orphans.request", node_effect)

    def restore():
        try:
            with connect() as db:
                pids.put(db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
                db.execute(
                    "INSERT INTO labs(id,organization_id,user_id,mission_id,mission_version,state,slot) VALUES (%s,%s,%s,'first-contact',1,'READY',%s)",
                    (observed.id, observed.organization_id, observed.user_id, observed.slot),
                )
        except CheckViolation:
            return "fenced"
        return "unsafe restoration"

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(process, job)
        assert reached.wait(5)
        restoring = pool.submit(restore)
        pid = pids.get(timeout=5)
        try:
            end = time.monotonic() + 5
            while True:
                with connect() as db:
                    waiting = db.execute(
                        "SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted",
                        (pid,),
                    ).fetchone()
                if waiting:
                    break
                assert time.monotonic() < end
                time.sleep(0.02)
            assert not restoring.done()
        finally:
            release.set()
        worker.result(timeout=5)
        assert restoring.result(timeout=5) == "fenced"
    with connect() as db:
        assert not db.execute("SELECT id FROM labs WHERE id=%s", (observed.id,)).fetchone()
        assert db.execute("SELECT status FROM node_orphans").fetchone()["status"] == "RESOLVED"


@pytest.mark.parametrize("reserved_status", ["QUEUED", "RESOLVED", "FAILED"])
def test_cleanup_reservation_survives_later_invalid_observation(ready_lab, reserved_status):
    from psycopg.errors import CheckViolation

    snapshot = orphan_fixture(ready_lab)
    observed = snapshot.labs[0]
    apply_inventory(snapshot, now())
    with connect() as db:
        db.execute("UPDATE node_orphans SET first_seen_at=now()-interval '31 seconds'")
    apply_inventory(snapshot, now())
    with connect() as db:
        db.execute("UPDATE jobs SET status='FAILED' WHERE kind='orphan-destroy'")
        db.execute("UPDATE node_orphans SET status=%s", (reserved_status,))
    observed.ownership_valid = False
    apply_inventory(snapshot, now())
    with connect() as db:
        row = db.execute("SELECT status,cleanup_reserved FROM node_orphans").fetchone()
        assert row == {"status": "QUARANTINED", "cleanup_reserved": True}
    with pytest.raises(CheckViolation), connect() as db:
        db.execute(
            "INSERT INTO labs(id,organization_id,user_id,mission_id,mission_version,state,slot) VALUES (%s,%s,%s,'first-contact',1,'READY',%s)",
            (observed.id, observed.organization_id, observed.user_id, observed.slot),
        )
    with pytest.raises(CheckViolation), connect() as db:
        db.execute("UPDATE node_orphans SET cleanup_reserved=false")


@pytest.mark.parametrize("previous_status,expected", [("SUCCEEDED", 202), ("FAILED", 409)])
def test_recovery_start_preserves_files_only_after_completed_lifecycle(
    ready_lab, previous_status, expected
):
    client, headers, lab_id, _ = ready_lab
    synthetic_snapshot(lab_id)
    with connect() as db:
        db.execute("UPDATE jobs SET status=%s", (previous_status,))
        db.execute("UPDATE labs SET state='ERROR',error_code='runtime_drift'")
    response = client.post(
        "/api/v1/labs/" + lab_id + "/start", headers={**headers, "Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == expected
    if expected == 202:
        with connect() as db:
            row = db.execute("SELECT generation,state FROM labs").fetchone()
            assert row == {"generation": 1, "state": "STARTING"}


def test_destroy_commit_fences_delayed_orphan_and_absence_allows_fresh_reappearance(ready_lab):
    snapshot = synthetic_snapshot(ready_lab[2])
    before = now()
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='DESTROYED',slot=NULL,updated_at=clock_timestamp() WHERE id=%s",
            (ready_lab[2],),
        )
    apply_inventory(snapshot, before)
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM node_orphans").fetchone()["n"] == 0
    # A subsequent complete snapshot really reports a resource still present.
    apply_inventory(snapshot, now())
    with connect() as db:
        assert db.execute("SELECT observations FROM node_orphans").fetchone()["observations"] == 1
    absent = snapshot.model_copy(update={"labs": []})
    apply_inventory(None, now())
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM node_orphans").fetchone()["n"] == 1
    apply_inventory(absent, before)  # Too old to contradict the newer observation.
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM node_orphans").fetchone()["n"] == 1
    apply_inventory(absent, now())
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM node_orphans").fetchone()["n"] == 0
    apply_inventory(snapshot, now())
    with connect() as db:
        row = db.execute("SELECT observations,first_seen_at FROM node_orphans").fetchone()
        assert row["observations"] == 1
        assert (now() - row["first_seen_at"]).total_seconds() < 5
        assert (
            db.execute("SELECT count(*) AS n FROM jobs WHERE kind='orphan-destroy'").fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize(
    "protection", ["reserved", "historical", "quarantined", "newer", "generation"]
)
def test_absent_inventory_never_discards_protected_observation(ready_lab, protection):
    snapshot = synthetic_snapshot(ready_lab[2])
    lab_id = ready_lab[2]
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='DESTROYED',slot=NULL,updated_at=clock_timestamp() WHERE id=%s",
            (lab_id,),
        )
    apply_inventory(snapshot, now())
    before = now()
    with connect() as db:
        if protection == "reserved":
            db.execute("UPDATE node_orphans SET cleanup_reserved=true")
        elif protection == "quarantined":
            db.execute("UPDATE node_orphans SET status='QUARANTINED'")
        elif protection == "newer":
            db.execute("UPDATE node_orphans SET last_seen_at=clock_timestamp()+interval '1 second'")
        elif protection == "generation":
            db.execute("UPDATE labs SET generation=2 WHERE id=%s", (lab_id,))
        else:
            from cra.jobs import enqueue

            op = snapshot.labs[0]
            job = enqueue(
                db,
                op.organization_id,
                op.user_id,
                "synthetic-prior-cleanup",
                "orphan-destroy",
                {"lab_id": str(op.id), "generation": 1},
            )
            db.execute("UPDATE jobs SET status='FAILED' WHERE id=%s", (job["id"],))
    apply_inventory(snapshot.model_copy(update={"labs": []}), before)
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM node_orphans").fetchone()["n"] == 1
