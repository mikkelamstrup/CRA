"""PostgreSQL queue with expiring leases and fencing for every completion."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from .contracts import Event
from .db import connect

DB = psycopg.Connection[dict[str, Any]]


class IdempotencyConflict(ValueError):
    pass


class LostLease(RuntimeError):
    pass


def enqueue(
    db: DB,
    organization_id: UUID,
    actor_id: UUID,
    request_key: str,
    kind: str,
    payload: dict[str, Any],
    *,
    resource_owner_id: UUID | None = None,
) -> dict[str, Any]:
    owner = resource_owner_id or actor_id
    row = db.execute(
        "INSERT INTO jobs(id,organization_id,actor_id,request_key,kind,payload,resource_owner_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(organization_id,actor_id,request_key) "
        "DO NOTHING RETURNING *",
        (uuid4(), organization_id, actor_id, request_key, kind, Jsonb(payload), owner),
    ).fetchone()
    if row:
        return row
    old = db.execute(
        "SELECT * FROM jobs WHERE organization_id=%s AND actor_id=%s AND request_key=%s",
        (organization_id, actor_id, request_key),
    ).fetchone()
    if (
        not old
        or old["kind"] != kind
        or old["payload"] != payload
        or old["resource_owner_id"] != owner
    ):
        raise IdempotencyConflict("Request key was used for a different operation")
    return old


def claim(owner: str, seconds: int = 60, *, node_id: int | None = None) -> dict[str, Any] | None:
    if not owner or len(owner) > 100 or not 1 <= seconds <= 300:
        raise ValueError("Invalid lease")
    if node_id is not None and (type(node_id) is not int or node_id not in (1, 2)):
        raise ValueError("Invalid worker node")
    with connect() as db:
        db.execute(
            "WITH expired AS (SELECT id FROM jobs WHERE status='RUNNING' "
            "AND lease_until<=now() AND attempts>=max_attempts "
            "FOR UPDATE SKIP LOCKED LIMIT 64) "
            "UPDATE jobs j SET status='FAILED',error_code='retry_exhausted',updated_at=now(), "
            "lease_token=NULL,lease_owner=NULL,lease_until=NULL "
            "FROM expired WHERE j.id=expired.id"
        )
        return db.execute(
            "WITH candidate AS (SELECT id FROM jobs WHERE attempts<max_attempts AND "
            "(%s::text IS NULL OR COALESCE(payload->>'node_id','1')=%s) AND "
            "((status='QUEUED' AND available_at<=now()) OR "
            "(status='RUNNING' AND lease_until<=now())) "
            "ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1) "
            "UPDATE jobs j SET status='RUNNING',attempts=attempts+1,lease_token=%s, "
            "lease_owner=%s,lease_until=now()+make_interval(secs=>%s),updated_at=now() "
            "FROM candidate WHERE j.id=candidate.id RETURNING j.*",
            (
                str(node_id) if node_id is not None else None,
                str(node_id) if node_id is not None else None,
                uuid4(),
                owner,
                seconds,
            ),
        ).fetchone()


def heartbeat(job_id: UUID, token: UUID, seconds: int = 60) -> bool:
    if not 1 <= seconds <= 300:
        raise ValueError("Invalid lease duration")
    try:
        with connect() as db:
            locked_lease(db, job_id, token)
            db.execute(
                "UPDATE jobs SET lease_until=clock_timestamp()+make_interval(secs=>%s),updated_at=now() WHERE id=%s",
                (seconds, job_id),
            )
        return True
    except LostLease:
        return False


def locked_lease(db: DB, job_id: UUID, token: UUID) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM jobs WHERE id=%s AND lease_token=%s AND status='RUNNING' FOR UPDATE",
        (job_id, token),
    ).fetchone()
    # Validate time after obtaining the lock; waiting on a row may outlive the lease.
    current = db.execute("SELECT clock_timestamp() AS current").fetchone()
    if not row or not current or row["lease_until"] <= current["current"]:
        raise LostLease("Job lease is no longer current")
    return row


def publish(db: DB, event: Event) -> None:
    # Serialize sequence allocation through commit: readers using a cursor cannot
    # skip an earlier sequence which is still uncommitted in another transaction.
    db.execute("SELECT pg_advisory_xact_lock(1415230002)")
    encoded = event.model_dump(mode="json")
    old = db.execute("SELECT event FROM outbox WHERE event_id=%s", (event.event_id,)).fetchone()
    if old:
        if Event.model_validate(old["event"]).model_dump(mode="json") != encoded:
            raise IdempotencyConflict("Event ID has conflicting content")
        return
    db.execute(
        "INSERT INTO outbox(event_id,organization_id,event,event_occurred_at) VALUES (%s,%s,%s,%s)",
        (event.event_id, event.organization_id, Jsonb(encoded), event.occurred_at),
    )


def finish(job_id: UUID, token: UUID, apply: Callable[[DB, dict[str, Any]], None]) -> None:
    with connect() as db:
        row = locked_lease(db, job_id, token)
        apply(db, row)
        db.execute(
            "UPDATE jobs SET status='SUCCEEDED',lease_token=NULL,lease_owner=NULL, "
            "lease_until=NULL,error_code=NULL,updated_at=now() WHERE id=%s",
            (job_id,),
        )


def fail(job_id: UUID, token: UUID, code: str, retry: bool = True) -> None:
    # Store a controlled code, never exception text that may contain credentials.
    if code not in ("node_unavailable", "node_rejected", "operation_failed", "generation_changed"):
        raise ValueError("Unknown error code")
    with connect() as db:
        job = locked_lease(db, job_id, token)
        again = retry and job["attempts"] < job["max_attempts"]
        db.execute(
            "UPDATE jobs SET status=%s,lease_token=NULL,lease_owner=NULL,lease_until=NULL, "
            "error_code=%s,available_at=now()+make_interval(secs=>%s),updated_at=now() WHERE id=%s",
            ("QUEUED" if again else "FAILED", code, min(60, 2 ** job["attempts"]), job_id),
        )


def claim_event(seconds: int = 60) -> dict[str, Any] | None:
    if not 1 <= seconds <= 300:
        raise ValueError("Invalid delivery lease")
    with connect() as db:
        return db.execute(
            "WITH candidate AS (SELECT sequence FROM outbox WHERE delivered_at IS NULL "
            "AND (lease_until IS NULL OR lease_until<=now()) "
            "ORDER BY sequence FOR UPDATE SKIP LOCKED LIMIT 1) "
            "UPDATE outbox e SET lease_token=%s,lease_until=now()+make_interval(secs=>%s) "
            "FROM candidate WHERE e.sequence=candidate.sequence RETURNING e.*",
            (uuid4(), seconds),
        ).fetchone()


def acknowledge_event(event_id: UUID, token: UUID) -> bool:
    with connect() as db:
        row = db.execute(
            "SELECT lease_until FROM outbox WHERE event_id=%s AND lease_token=%s "
            "AND delivered_at IS NULL FOR UPDATE",
            (event_id, token),
        ).fetchone()
        current = db.execute("SELECT clock_timestamp() AS current").fetchone()
        if not row or not current or row["lease_until"] <= current["current"]:
            return False
        db.execute(
            "UPDATE outbox SET delivered_at=clock_timestamp(),lease_token=NULL,lease_until=NULL "
            "WHERE event_id=%s",
            (event_id,),
        )
        return True


def consume_once(db: DB, consumer: str, event_id: UUID, apply: Callable[[DB], None]) -> bool:
    inserted = db.execute(
        "INSERT INTO event_receipts(consumer,event_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
        (consumer, event_id),
    ).rowcount
    if inserted:
        apply(db)
    return bool(inserted)
