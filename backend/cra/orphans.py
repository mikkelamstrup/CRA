"""Durable cleanup of historically authorized, precisely owned orphan labs."""

import threading
from typing import Any

import psycopg

from .contracts import missions
from .db import connect
from .jobs import DB, LostLease, fail, finish, heartbeat
from .node_client import NodeRejected, NodeUnavailable, request
from .node_contracts import NodeCommand


def process(job: dict[str, Any]) -> None:
    # The durable QUEUED tombstone also survives connection loss/worker death.
    # The trigger rejects active restoration after reservation, even if DB restarts.
    with connect() as fence:
        fence.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230005))",
            (job["payload"]["lab_id"],),
        )
        _process(job)


def _process(job: dict[str, Any]) -> None:
    node_id = job["payload"].get("node_id", 1)
    with connect() as db:
        orphan = db.execute(
            "SELECT * FROM node_orphans WHERE lab_id=%s AND node_id=%s",
            (job["payload"]["lab_id"], node_id),
        ).fetchone()
        current = db.execute(
            "SELECT state,node_id FROM labs WHERE id=%s", (job["payload"]["lab_id"],)
        ).fetchone()
        if (
            not orphan
            or orphan["status"] != "QUEUED"
            or not orphan["cleanup_reserved"]
            or orphan["job_id"] != job["id"]
            or orphan["organization_id"] != job["organization_id"]
            or orphan["user_id"] != job["resource_owner_id"]
            or orphan["generation"] != job["payload"]["generation"]
            or (current and (current["state"] != "DESTROYED" or current["node_id"] != node_id))
        ):
            fail(job["id"], job["lease_token"], "generation_changed", retry=False)
            return
        authorized = db.execute(
            "SELECT id FROM jobs WHERE organization_id=%s AND resource_owner_id=%s AND payload->>'lab_id'=%s "
            "AND payload->>'generation'=%s AND kind IN ('create','reset','start','stop','destroy') "
            "AND COALESCE(payload->>'node_id','1')=%s AND status IN ('SUCCEEDED','FAILED') LIMIT 1",
            (
                orphan["organization_id"],
                orphan["user_id"],
                str(orphan["lab_id"]),
                str(orphan["generation"]),
                str(node_id),
            ),
        ).fetchone()
        if not authorized:
            fail(job["id"], job["lease_token"], "node_rejected", retry=False)
            return
    command = NodeCommand(
        schema_version=1,
        node_id=node_id,
        job_id=job["id"],
        lab_id=orphan["lab_id"],
        organization_id=orphan["organization_id"],
        user_id=orphan["user_id"],
        operation="destroy",
        generation=orphan["generation"],
        slot=orphan["slot"],
        mission_id="first-contact",
        mission_version=1,
        resources=missions()[0].resources,
    )
    done = threading.Event()
    lost = threading.Event()

    def renew() -> None:
        while not done.wait(10):
            try:
                if not heartbeat(job["id"], job["lease_token"]):
                    lost.set()
                    return
            except (psycopg.Error, OSError):
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        observed = request("/v1/operations", command)
        if lost.is_set():
            raise LostLease("Cleanup lease lost")
        if observed.get("node_id", 1) != node_id or any(
            str(observed.get(key)) != str(value)
            for key, value in {
                "id": orphan["lab_id"],
                "organization_id": orphan["organization_id"],
                "user_id": orphan["user_id"],
                "generation": orphan["generation"],
                "state": "DESTROYED",
                "slot": None,
            }.items()
        ):
            raise NodeRejected("Cleanup response mismatch")

        def apply(db: DB, _: dict[str, Any]) -> None:
            row = db.execute(
                "SELECT * FROM node_orphans WHERE lab_id=%s AND node_id=%s FOR UPDATE",
                (orphan["lab_id"], node_id),
            ).fetchone()
            current = db.execute(
                "SELECT state,node_id FROM labs WHERE id=%s", (orphan["lab_id"],)
            ).fetchone()
            if (
                not row
                or row["job_id"] != job["id"]
                or row["status"] != "QUEUED"
                or (current and (current["state"] != "DESTROYED" or current["node_id"] != node_id))
            ):
                raise LostLease("Cleanup intent changed")
            db.execute(
                "UPDATE node_orphans SET status='RESOLVED',error_code=NULL,last_seen_at=now() WHERE lab_id=%s AND node_id=%s",
                (orphan["lab_id"], node_id),
            )

        finish(job["id"], job["lease_token"], apply)
    except LostLease:
        pass
    except (NodeRejected, NodeUnavailable) as error:
        try:
            fail(
                job["id"],
                job["lease_token"],
                "node_rejected" if isinstance(error, NodeRejected) else "node_unavailable",
                retry=isinstance(error, NodeUnavailable),
            )
        except LostLease:
            pass
    finally:
        done.set()
        thread.join(timeout=2)
