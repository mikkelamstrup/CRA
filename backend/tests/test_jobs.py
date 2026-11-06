import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cra.contracts import Event
from cra.db import connect
from cra.jobs import (
    IdempotencyConflict,
    LostLease,
    acknowledge_event,
    claim,
    claim_event,
    consume_once,
    enqueue,
    fail,
    finish,
    heartbeat,
    publish,
)


@pytest.fixture
def queue(database):
    org, actor = uuid4(), uuid4()
    with connect() as db:
        db.execute("INSERT INTO organizations VALUES (%s,'synthetic-queue')", (org,))
        db.execute(
            "INSERT INTO users(id,username,password_hash) VALUES (%s,'synthetic-worker','not-a-login')",
            (actor,),
        )
    return org, actor


def put(queue, key="same", payload=None):
    with connect() as db:
        return enqueue(db, *queue, key, "test-probe", payload or {"fixture": True})


def event_for(queue):
    return Event.model_validate(
        {
            "schema_version": 1,
            "event_id": uuid4(),
            "organization_id": queue[0],
            "lab_id": uuid4(),
            "generation": 1,
            "actor_id": queue[1],
            "occurred_at": datetime.now(UTC),
            "source": "control",
            "evidence_level": "system",
            "payload": {"kind": "lab.lifecycle", "state": "READY"},
        }
    )


def test_concurrent_idempotency_and_claims(queue):
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: put(queue), range(8)))
    assert len({r["id"] for r in rows}) == 1
    with pytest.raises(IdempotencyConflict):
        put(queue, payload={"fixture": "different"})
    for i in range(7):
        put(queue, str(i))
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda i: claim("worker-" + str(i)), range(8)))
    assert all(jobs)
    assert len({j["id"] for j in jobs}) == 8
    assert claim("extra") is None


def test_actual_worker_exit_lease_retry_and_fencing(queue):
    job = put(queue)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; from cra.jobs import claim; assert claim('crashing-worker',1); os._exit(23)",
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 23
    with connect() as db:
        old = db.execute("SELECT * FROM jobs WHERE id=%s", (job["id"],)).fetchone()
    assert old["status"] == "RUNNING" and old["attempts"] == 1
    time.sleep(1.1)
    current = claim("replacement")
    assert current["id"] == job["id"] and current["attempts"] == 2
    assert current["lease_token"] != old["lease_token"]
    assert not heartbeat(job["id"], old["lease_token"])
    with pytest.raises(LostLease):
        finish(job["id"], old["lease_token"], lambda db, row: None)
    assert heartbeat(job["id"], current["lease_token"])
    finish(job["id"], current["lease_token"], lambda db, row: None)
    assert claim("third") is None


def test_retry_exhaustion_and_controlled_errors(queue):
    job = put(queue)
    with connect() as db:
        db.execute("UPDATE jobs SET max_attempts=1 WHERE id=%s", (job["id"],))
    leased = claim("test", 1)
    assert leased
    time.sleep(1.1)
    assert claim("replacement") is None
    with connect() as db:
        row = db.execute("SELECT status,error_code FROM jobs WHERE id=%s", (job["id"],)).fetchone()
    assert row == {"status": "FAILED", "error_code": "retry_exhausted"}
    put(queue, "retry")
    leased = claim("retry")
    fail(leased["id"], leased["lease_token"], "node_unavailable")
    assert claim("too-early") is None
    with connect() as db:
        db.execute("UPDATE jobs SET available_at=now()-interval '1 second' WHERE status='QUEUED'")
    leased = claim("retry")
    assert leased["attempts"] == 2
    with pytest.raises(ValueError):
        fail(leased["id"], leased["lease_token"], "arbitrary-exception-secret")
    fail(leased["id"], leased["lease_token"], "node_rejected", retry=False)


def test_transactional_outbox_retry_and_consumer_deduplication(queue):
    job = put(queue)
    leased = claim("test")
    event = event_for(queue)

    def broken(db, row):
        db.execute("INSERT INTO platform_metadata VALUES ('atomic-proof','created')")
        publish(db, event)
        raise RuntimeError("synthetic rollback")

    with pytest.raises(RuntimeError):
        finish(job["id"], leased["lease_token"], broken)
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
        assert (
            db.execute("SELECT value FROM platform_metadata WHERE name='atomic-proof'").fetchone()
            is None
        )

    def complete(db, row):
        db.execute("INSERT INTO platform_metadata VALUES ('atomic-proof','created')")
        publish(db, event)

    finish(job["id"], leased["lease_token"], complete)
    with connect() as db:
        publish(db, event)
    first = claim_event(1)
    assert first["event_id"] == event.event_id

    def apply(db):
        db.execute("UPDATE platform_metadata SET value='consumed' WHERE name='atomic-proof'")

    with connect() as db:
        assert consume_once(db, "synthetic", event.event_id, apply)
    # Simulate consumer success followed by lost ack: delivery happens again.
    time.sleep(1.1)
    second = claim_event()
    assert second["event_id"] == first["event_id"] and second["lease_token"] != first["lease_token"]
    with connect() as db:
        assert not consume_once(db, "synthetic", event.event_id, apply)
    assert not acknowledge_event(event.event_id, first["lease_token"])
    assert acknowledge_event(event.event_id, second["lease_token"])
    assert claim_event() is None
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM event_receipts").fetchone()["n"] == 1


def test_waiting_for_row_lock_does_not_revive_expired_lease(queue):
    job = put(queue)
    leased = claim("short", 1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connect() as db:
            db.execute("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (job["id"],))
            pending = pool.submit(heartbeat, job["id"], leased["lease_token"])
            time.sleep(1.1)
        assert pending.result(timeout=5) is False
    replacement = claim("replacement")
    assert replacement["attempts"] == 2


def test_ack_waiting_on_row_lock_cannot_accept_expired_delivery(queue):
    event = event_for(queue)
    with connect() as db:
        publish(db, event)
    delivery = claim_event(1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connect() as db:
            db.execute(
                "SELECT event_id FROM outbox WHERE event_id=%s FOR UPDATE", (event.event_id,)
            )
            pending = pool.submit(acknowledge_event, event.event_id, delivery["lease_token"])
            time.sleep(1.1)
        assert pending.result(timeout=5) is False
    replacement = claim_event()
    assert replacement["event_id"] == event.event_id
    assert acknowledge_event(event.event_id, replacement["lease_token"])


def test_locked_exhausted_job_does_not_block_other_claims(queue):
    expired = put(queue, "expired")
    claim("expired")
    with connect() as db:
        db.execute(
            "UPDATE jobs SET attempts=max_attempts,lease_until=now()-interval '1 second' WHERE id=%s",
            (expired["id"],),
        )
    available = put(queue, "available")
    with ThreadPoolExecutor(max_workers=1) as pool, connect() as db:
        db.execute("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (expired["id"],))
        pending = pool.submit(claim, "unblocked")
        assert pending.result(timeout=2)["id"] == available["id"]
    assert claim("cleanup") is None
    with connect() as db:
        assert (
            db.execute("SELECT status FROM jobs WHERE id=%s", (expired["id"],)).fetchone()["status"]
            == "FAILED"
        )
