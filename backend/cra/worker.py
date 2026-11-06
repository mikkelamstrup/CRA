"""Durable lifecycle worker. VM effects happen only via the authenticated node."""

import os
import re
import signal
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg

from .contracts import Event, LabState, LifecyclePayload
from .db import connect
from .jobs import DB, LostLease, claim, fail, finish, heartbeat, publish
from .labs import mission_by_id, with_desktop
from .node_client import NodeRejected, NodeUnavailable, request
from .node_contracts import NodeCommand

STOP = threading.Event()


def reconcile_failed_jobs() -> int:
    # The current operation pointer fences older failures and is set with each intent.
    # Queue exhaustion can happen in claim(), without process() being entered.
    with connect() as db:
        return db.execute(
            "UPDATE labs l SET state='ERROR',error_code=j.error_code,updated_at=now() "
            "FROM jobs j WHERE l.current_job_id=j.id AND j.status='FAILED' "
            "AND l.state IN ('QUEUED','PROVISIONING','STARTING','STOPPING','RESETTING','DESTROYING') "
            "AND j.organization_id=l.organization_id AND j.resource_owner_id=l.user_id "
            "AND j.payload->>'lab_id'=l.id::text "
            "AND j.payload->>'generation'=l.generation::text AND COALESCE(j.payload->>'node_id','1')=l.node_id::text"
        ).rowcount


def process(job: dict[str, Any]) -> None:
    if job["kind"] == "orphan-destroy":
        from .orphans import process as cleanup

        cleanup(job)
        return
    if job["kind"] not in ("create", "start", "stop", "reset", "destroy"):
        fail(job["id"], job["lease_token"], "node_rejected", retry=False)
        return
    with connect() as db:
        lab = db.execute(
            "SELECT * FROM labs WHERE id=%s AND organization_id=%s AND user_id=%s",
            (job["payload"]["lab_id"], job["organization_id"], job["resource_owner_id"]),
        ).fetchone()
        if (
            not lab
            or lab["generation"] != job["payload"]["generation"]
            or lab["node_id"] != job["payload"].get("node_id", 1)
        ):
            fail(job["id"], job["lease_token"], "generation_changed", retry=False)
            return
        if job["kind"] == "create":
            db.execute(
                "UPDATE labs SET state='PROVISIONING',updated_at=now() WHERE id=%s AND state='QUEUED'",
                (lab["id"],),
            )
        participants = db.execute(
            "SELECT user_id FROM lab_participants WHERE lab_id=%s ORDER BY ordinal", (lab["id"],)
        ).fetchall()
    mission = with_desktop(
        mission_by_id(lab["mission_id"], lab["mission_version"]), lab["desktop_enabled"]
    )
    command = NodeCommand.model_validate(
        {
            "schema_version": 1,
            "node_id": lab["node_id"],
            "job_id": job["id"],
            "lab_id": lab["id"],
            "organization_id": lab["organization_id"],
            "user_id": lab["user_id"],
            "operation": job["kind"],
            "generation": lab["generation"],
            "slot": job["payload"]["slot"],
            "mission_id": mission.id,
            "mission_version": mission.version,
            "resources": mission.resources,
            "operator_user_ids": [p["user_id"] for p in participants],
        }
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
            raise LostLease("Worker lost lease")
        expected = {
            "create": "READY",
            "start": "READY",
            "stop": "STOPPED",
            "reset": "READY",
            "destroy": "DESTROYED",
        }[job["kind"]]
        if (
            observed.get("node_id", 1) != lab["node_id"]
            or observed["id"] != str(lab["id"])
            or observed["organization_id"] != str(lab["organization_id"])
            or observed["user_id"] != str(lab["user_id"])
            or observed["generation"] != lab["generation"]
            or observed["state"] != expected
            or (expected == "READY" and observed.get("capture_ready") is not True)
        ):
            raise NodeRejected("Node response does not match operation")

        if participants and observed.get("operator_user_ids") != [
            str(p["user_id"]) for p in participants
        ]:
            raise NodeRejected("Node operator assignment mismatch")
        verifiers = observed.get("objective_verifiers", {})
        if job["kind"] in ("create", "reset"):
            if not isinstance(verifiers, dict) or set(verifiers) != {
                item.id for item in mission.objectives
            }:
                raise NodeRejected("Missing objective verifiers")
            if any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in verifiers.values()
            ):
                raise NodeRejected("Invalid objective verifier")

        def apply(db: DB, row: dict[str, Any]) -> None:
            current = db.execute(
                "SELECT * FROM labs WHERE id=%s FOR UPDATE", (lab["id"],)
            ).fetchone()
            if (
                not current
                or current["node_id"] != command.node_id
                or current["generation"] != command.generation
                or current["desired_state"] != expected
                or current["current_job_id"] != job["id"]
            ):
                raise LostLease("Lab intent changed")
            db.execute(
                "UPDATE labs SET state=%s,target_ip=%s,slot=%s,last_observed_at=now(),error_code=NULL,updated_at=now() WHERE id=%s",
                (
                    expected,
                    observed["target_ip"],
                    None if expected == "DESTROYED" else observed["slot"],
                    lab["id"],
                ),
            )
            if job["kind"] in ("create", "reset"):
                for objective_id, verifier in verifiers.items():
                    db.execute(
                        "INSERT INTO lab_flags(lab_id,generation,objective_id,verifier) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (lab["id"], lab["generation"], objective_id, verifier),
                    )
            publish(
                db,
                Event(
                    schema_version=1,
                    event_id=uuid4(),
                    organization_id=lab["organization_id"],
                    lab_id=lab["id"],
                    generation=lab["generation"],
                    actor_id=job["actor_id"],
                    initiator_type="user",
                    occurred_at=datetime.now(UTC),
                    source="control",
                    evidence_level="system",
                    payload=LifecyclePayload(kind="lab.lifecycle", state=LabState(expected)),
                ),
            )

        finish(job["id"], job["lease_token"], apply)
    except LostLease:
        pass  # Reconciliation/reclaim owns the next decision, never an old worker.
    except (NodeRejected, NodeUnavailable) as error:
        code = "node_rejected" if isinstance(error, NodeRejected) else "node_unavailable"
        try:
            fail(job["id"], job["lease_token"], code, retry=code == "node_unavailable")
            reconcile_failed_jobs()
        except LostLease:
            pass
    finally:
        done.set()
        thread.join(timeout=2)


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    node_id = int(os.environ.get("CRA_WORKER_NODE", "1"))
    if node_id not in (1, 2):
        raise ValueError("Invalid worker node")
    owner = "worker-" + str(uuid4())
    while not STOP.is_set():
        try:
            job = claim(owner, node_id=node_id)
            reconcile_failed_jobs()
            if job:
                process(job)
            else:
                STOP.wait(1)
        except (psycopg.Error, OSError, ValueError, KeyError, LostLease):
            # Detailed exception text may contain secrets; durable state drives recovery.
            print("worker_iteration_failed", flush=True)
            STOP.wait(2)


if __name__ == "__main__":
    main()
