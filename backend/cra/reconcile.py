"""Observe owned VM state; durable cleanup requires independent control history."""

import signal
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
from pydantic import ValidationError

from .compute_nodes import nodes
from .contracts import Event, LabState, LifecyclePayload
from .db import connect
from .jobs import DB, enqueue, publish
from .node_client import NodeRejected, NodeUnavailable, request
from .node_contracts import NodeInventory, NodeLab
from .traffic import ingest as ingest_traffic
from .traffic import prune as prune_traffic

STOP = threading.Event()
LOCK = 1415230004


def same_owner(row: dict[str, Any], observed: NodeLab) -> bool:
    return all(
        str(row[key]) == str(getattr(observed, key))
        for key in ("organization_id", "user_id", "generation")
    )


def pending(db: DB, lab_id: object) -> bool:
    return bool(
        db.execute(
            "SELECT id FROM jobs WHERE payload->>'lab_id'=%s AND status IN ('QUEUED','RUNNING') LIMIT 1",
            (str(lab_id),),
        ).fetchone()
    )


def history(db: DB, observed: NodeLab, node_id: int = 1) -> bool:
    return bool(
        db.execute(
            "SELECT id FROM jobs WHERE organization_id=%s AND resource_owner_id=%s "
            "AND payload->>'lab_id'=%s AND payload->>'generation'=%s "
            "AND COALESCE(payload->>'node_id','1')=%s AND kind IN ('create','reset','start','stop','destroy') AND status IN ('SUCCEEDED','FAILED') LIMIT 1",
            (
                observed.organization_id,
                observed.user_id,
                str(observed.id),
                str(observed.generation),
                str(node_id),
            ),
        ).fetchone()
    )


def state_for(row: dict[str, Any], observed: NodeLab | None) -> tuple[str, str | None]:
    if observed is None:
        return "ERROR", "resource_missing"
    if (
        not same_owner(row, observed)
        or not observed.ownership_valid
        or observed.slot != row["slot"]
    ):
        return "ERROR", "ownership_mismatch"
    expected_operators = row.get("operator_user_ids")
    if expected_operators is not None and expected_operators != [
        str(op.user_id) for op in observed.operators
    ]:
        return "ERROR", "ownership_mismatch"
    if (
        not observed.operator_present
        or not observed.target_present
        or any(not op.present for op in observed.operators)
    ):
        return "ERROR", "resource_missing"
    expected = row["desired_state"]
    matching = (
        expected == "READY"
        and observed.state == "READY"
        and observed.operator_running
        and observed.target_running
        and observed.capture_ready
        and all(op.running for op in observed.operators)
    ) or (
        expected == "STOPPED"
        and observed.state == "STOPPED"
        and not observed.operator_running
        and not observed.target_running
        and not any(op.running for op in observed.operators)
    )
    if not matching:
        return "ERROR", "runtime_drift"
    # A failed lifecycle is not silently completed without its fenced worker transaction.
    if row["state"] == "ERROR" and row["error_code"] not in (
        "observation_stale",
        "node_unavailable",
        "runtime_drift",
    ):
        return "ERROR", row["error_code"]
    return expected, None


def orphan(db: DB, observed: NodeLab, before: datetime, node_id: int = 1) -> None:
    if observed.state == "DESTROYED" or observed.slot is None or pending(db, observed.id):
        return
    db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230005))", (str(observed.id),))
    current = db.execute("SELECT * FROM labs WHERE id=%s FOR UPDATE", (observed.id,)).fetchone()
    foreign = bool(current and current["node_id"] != node_id)
    if (
        current
        and not foreign
        and (
            current["state"] != "DESTROYED"
            or current["updated_at"] > before
            or not same_owner(current, observed)
        )
    ):
        return
    record = db.execute(
        "SELECT * FROM node_orphans WHERE lab_id=%s AND node_id=%s FOR UPDATE",
        (observed.id, node_id),
    ).fetchone()
    valid = not foreign and observed.ownership_valid and history(db, observed, node_id)
    if record and (not same_owner(record, observed) or record["slot"] != observed.slot):
        db.execute(
            "UPDATE node_orphans SET status='QUARANTINED',error_code='ownership_mismatch' WHERE lab_id=%s AND node_id=%s",
            (observed.id, node_id),
        )
        return
    if not record:
        db.execute(
            "INSERT INTO node_orphans(lab_id,organization_id,user_id,generation,slot,status,error_code,node_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                observed.id,
                observed.organization_id,
                observed.user_id,
                observed.generation,
                observed.slot,
                "OBSERVED" if valid else "QUARANTINED",
                None if valid else "unverified_resource",
                node_id,
            ),
        )
        return
    db.execute(
        "UPDATE node_orphans SET last_seen_at=now(),observations=observations+1 WHERE lab_id=%s AND node_id=%s",
        (observed.id, node_id),
    )
    if not valid:
        db.execute(
            "UPDATE node_orphans SET status='QUARANTINED',error_code='unverified_resource' WHERE lab_id=%s AND node_id=%s",
            (observed.id, node_id),
        )
        return
    if record["status"] != "OBSERVED":
        return
    now = db.execute("SELECT clock_timestamp() AS current").fetchone()
    assert now
    if record["first_seen_at"] > now["current"] - timedelta(seconds=30):
        return
    job = enqueue(
        db,
        observed.organization_id,
        observed.user_id,
        f"orphan:{node_id}:{observed.id}:{observed.generation}",
        "orphan-destroy",
        {"lab_id": str(observed.id), "generation": observed.generation, "node_id": node_id},
    )
    db.execute(
        "UPDATE node_orphans SET status='QUEUED',cleanup_reserved=true,job_id=%s WHERE lab_id=%s AND node_id=%s",
        (job["id"], observed.id, node_id),
    )


def discard_absent_observations(db: DB, inventory: NodeInventory, before: datetime) -> None:
    """Discard only an unreserved observation contradicted by a later full inventory.

    No tombstone is created: a real reappearance must start a fresh 30s period.
    Permanent cleanup fences, historical jobs and unknown resources are retained.
    """
    node_id = inventory.node_id
    seen = {lab.id for lab in inventory.labs}
    candidates = db.execute(
        "SELECT lab_id FROM node_orphans WHERE node_id=%s AND status='OBSERVED' AND NOT cleanup_reserved AND job_id IS NULL ORDER BY lab_id",
        (node_id,),
    ).fetchall()
    for candidate in candidates:
        lab_id = candidate["lab_id"]
        if lab_id in seen:
            continue
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230005))", (str(lab_id),))
        current = db.execute("SELECT * FROM labs WHERE id=%s FOR UPDATE", (lab_id,)).fetchone()
        record = db.execute(
            "SELECT * FROM node_orphans WHERE lab_id=%s AND node_id=%s FOR UPDATE",
            (lab_id, node_id),
        ).fetchone()
        if (
            not current
            or current["state"] != "DESTROYED"
            or current["updated_at"] > before
            or not record
            or record["status"] != "OBSERVED"
            or record["cleanup_reserved"]
            or record["job_id"] is not None
            or record["last_seen_at"] > before
            or any(
                record[k] != current[k]
                for k in ("node_id", "organization_id", "user_id", "generation")
            )
            or pending(db, lab_id)
        ):
            continue
        historical = db.execute(
            "SELECT id FROM jobs WHERE kind='orphan-destroy' AND payload->>'lab_id'=%s LIMIT 1",
            (str(lab_id),),
        ).fetchone()
        if not historical:
            db.execute("DELETE FROM node_orphans WHERE lab_id=%s AND node_id=%s", (lab_id, node_id))


def apply_inventory(inventory: NodeInventory | None, before: datetime, node_id: int = 1) -> int:
    if (
        type(node_id) is not int
        or node_id not in (1, 2)
        or (inventory and inventory.node_id != node_id)
    ):
        raise ValueError("Inventory node mismatch")
    with connect() as db:
        locked = db.execute("SELECT pg_try_advisory_xact_lock(%s) AS locked", (LOCK,)).fetchone()
        if not locked or not locked["locked"]:
            return 0
        if inventory:
            db.execute(
                "INSERT INTO node_status(id,connected,available_memory_mib,free_disk_gib,active_labs,reservations,observed_at,physical_memory_mib,running_declared_memory_mib) VALUES (%s,true,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET connected=true,available_memory_mib=excluded.available_memory_mib,free_disk_gib=excluded.free_disk_gib,active_labs=excluded.active_labs,reservations=excluded.reservations,observed_at=excluded.observed_at,received_at=now(),physical_memory_mib=excluded.physical_memory_mib,running_declared_memory_mib=excluded.running_declared_memory_mib",
                (
                    node_id,
                    inventory.available_memory_mib,
                    inventory.free_disk_gib,
                    sum(
                        lab.operator_running
                        or lab.target_running
                        or lab.state == "PROVISIONING"
                        or any(op.running for op in lab.operators)
                        for lab in inventory.labs
                    ),
                    sum(lab.slot is not None for lab in inventory.labs),
                    datetime.fromtimestamp(inventory.observed_at, UTC),
                    inventory.physical_memory_mib,
                    inventory.running_declared_memory_mib,
                ),
            )
        else:
            db.execute("UPDATE node_status SET connected=false WHERE id=%s", (node_id,))
        rows = db.execute(
            "SELECT * FROM labs WHERE node_id=%s ORDER BY id FOR UPDATE", (node_id,)
        ).fetchall()
        by_id = {lab.id: lab for lab in inventory.labs} if inventory else {}
        changed = 0
        for row in rows:
            if row["state"] == "DESTROYED" or row["updated_at"] > before or pending(db, row["id"]):
                continue
            if row["state"] not in ("READY", "STOPPED", "ERROR"):
                continue
            if inventory:
                if row["team_id"] is not None:
                    row["operator_user_ids"] = [
                        str(p["user_id"])
                        for p in db.execute(
                            "SELECT user_id FROM lab_participants WHERE lab_id=%s ORDER BY ordinal",
                            (row["id"],),
                        ).fetchall()
                    ]
                state, error = state_for(row, by_id.get(row["id"]))
                current_job = db.execute(
                    "SELECT status FROM jobs WHERE id=%s", (row["current_job_id"],)
                ).fetchone()
                if state in ("READY", "STOPPED") and (
                    not current_job or current_job["status"] != "SUCCEEDED"
                ):
                    state, error = "ERROR", "lifecycle_incomplete"
            else:
                state, error = "ERROR", "node_unavailable"
                if row["state"] == "ERROR" and row["error_code"] not in (
                    "node_unavailable",
                    "observation_stale",
                ):
                    continue  # Preserve the actual lifecycle failure diagnosis.
            observed_lab = by_id.get(row["id"])
            if inventory and observed_lab is not None:
                ingest_traffic(db, row, observed_lab, before)
            changed_state = state != row["state"] or error != row["error_code"]
            # Inventory counts as a new observation even when it confirms a failure.
            db.execute(
                "UPDATE labs SET state=%s,error_code=%s,last_observed_at=CASE WHEN %s THEN now() ELSE last_observed_at END,updated_at=now() WHERE id=%s",
                (state, error, inventory is not None, row["id"]),
            )
            if changed_state:
                changed += 1
                publish(
                    db,
                    Event(
                        schema_version=1,
                        event_id=uuid4(),
                        organization_id=row["organization_id"],
                        lab_id=row["id"],
                        generation=row["generation"],
                        actor_id=row["user_id"],
                        occurred_at=datetime.now(UTC),
                        source="control",
                        evidence_level="system",
                        payload=LifecyclePayload(kind="lab.lifecycle", state=LabState(state)),
                    ),
                )
        if inventory:
            known = {row["id"]: row for row in rows}
            for observed in inventory.labs:
                known_row = known.get(observed.id)
                if known_row is None or (
                    known_row["state"] == "DESTROYED" and same_owner(known_row, observed)
                ):
                    orphan(db, observed, before, node_id)
            discard_absent_observations(db, inventory, before)
            db.execute(
                "UPDATE node_orphans o SET status='FAILED',error_code=j.error_code FROM jobs j WHERE o.job_id=j.id AND o.status='QUEUED' AND j.status='FAILED' AND o.node_id=%s",
                (node_id,),
            )
        prune_traffic(db)
        return changed


def once() -> int:
    with connect() as db:
        persisted = {
            row["node_id"]
            for row in db.execute(
                "SELECT DISTINCT node_id FROM labs UNION SELECT DISTINCT node_id FROM node_orphans"
            ).fetchall()
        }
    try:
        configured = set(nodes())
    except (ValueError, OSError):
        configured = set()
    changed = 0
    for identity in sorted(configured | persisted):
        with connect() as db:
            row = db.execute("SELECT clock_timestamp() AS current").fetchone()
            assert row
            before = row["current"]
        try:
            if identity not in configured:
                raise NodeUnavailable("Missing node configuration")
            raw = (
                request("/v1/inventory")
                if identity == 1
                else request("/v1/inventory", node_id=identity)
            )
            inventory = NodeInventory.model_validate(raw)
            if (
                inventory.node_id != identity
                or abs(before.timestamp() - inventory.observed_at) > 20
            ):
                raise NodeUnavailable("Invalid node observation")
        except (NodeUnavailable, NodeRejected, ValidationError):
            inventory = None
        changed += apply_inventory(inventory, before, identity)
    return changed


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    while not STOP.is_set():
        try:
            once()
        except (psycopg.Error, OSError, ValueError, KeyError):
            print("reconciliation_iteration_failed", flush=True)
        STOP.wait(5)


if __name__ == "__main__":
    main()
