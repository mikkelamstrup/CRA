"""Best-effort observed headers, independently fenced from objective scoring."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb

from .auth import CurrentUser, require_scope
from .contracts import Contract
from .db import connect
from .flow_contracts import CaptureStatus, FlowWindow
from .jobs import DB
from .node_contracts import NodeLab

router = APIRouter(prefix="/api/v1/command-center")
PER_LAB_LIMIT = 20000
GLOBAL_LIMIT = 120000


class TrafficEvent(Contract):
    event_id: UUID
    sequence: int
    schema_version: Literal[1] = 1
    lab_id: UUID
    generation: int
    operator_user_id: UUID
    epoch: UUID
    source: Literal["lab_link"] = "lab_link"
    evidence_level: Literal["observed"] = "observed"
    observed_at: datetime
    received_at: datetime
    window: FlowWindow


class TrafficView(Contract):
    lab_id: UUID
    generation: int
    stale: bool
    received_at: datetime | None
    captures: list[CaptureStatus]
    events: list[TrafficEvent]
    retention_hours: Literal[24] = 24
    per_lab_limit: Literal[20000] = 20000


def ingest(db: DB, row: dict[str, Any], observed: NodeLab, before: datetime) -> int:
    """Called with the lab row locked inside reconciliation's before fence."""
    if row["updated_at"] > before or row["state"] == "DESTROYED":
        return 0
    if (
        observed.id != row["id"]
        or not observed.ownership_valid
        or any(
            str(row[key]) != str(getattr(observed, key))
            for key in ("organization_id", "user_id", "generation")
        )
    ):
        return 0
    actors = (
        [
            p["user_id"]
            for p in db.execute(
                "SELECT user_id FROM lab_participants WHERE lab_id=%s ORDER BY ordinal",
                (row["id"],),
            ).fetchall()
        ]
        if row["team_id"] is not None
        else [row["user_id"]]
    )
    if observed.captures and [c.operator_user_id for c in observed.captures] != actors:
        raise ValueError("Capture assignment mismatch")
    previous = db.execute(
        "SELECT * FROM lab_capture_snapshots WHERE lab_id=%s", (row["id"],)
    ).fetchone()
    if previous and previous["fence_at"] >= before:
        return 0
    prior = (
        {str(c["operator_user_id"]): c for c in previous["captures"]}
        if previous and previous["generation"] == row["generation"]
        else {}
    )
    inserted = 0
    statuses = []
    for capture in observed.captures:
        old = prior.get(str(capture.operator_user_id))
        if old and old["epoch"] == str(capture.epoch):
            for name in (
                "last_window",
                "metadata_dropped",
                "unknown_packets",
                "flow_overflow",
                "sequence_gaps",
                "partial_windows",
                "ring_overwrites",
                "packets",
                "bytes",
            ):
                if getattr(capture, name) < old[name]:
                    raise ValueError("Regressive capture snapshot")
        status = capture.model_dump(mode="json", exclude={"windows"})
        prior_window = old["last_window"] if old and old["epoch"] == str(capture.epoch) else 0
        missed = max(0, capture.windows[0].sequence - prior_window - 1) if capture.windows else 0
        status["poll_missed_windows"] = missed + (
            old.get("poll_missed_windows", 0) if old and old["epoch"] == str(capture.epoch) else 0
        )
        statuses.append(status)
        for window in capture.windows:
            # Empty healthy windows maintain liveness without filling history.
            if not window.flows and not window.partial and not window.omitted_packets:
                continue
            event_id = uuid5(
                capture.epoch,
                f"{row['id']}:{row['generation']}:{capture.operator_user_id}:{window.sequence}",
            )
            encoded = window.model_dump(mode="json")
            existing = db.execute(
                "SELECT payload FROM flow_history WHERE event_id=%s", (event_id,)
            ).fetchone()
            if existing:
                if existing["payload"] != encoded:
                    raise ValueError("Changed capture window")
                continue
            # Node ring loss is best-effort telemetry. Older windows already seen
            # but pruned must not be reinserted on every subsequent inventory.
            if old and old["epoch"] == str(capture.epoch) and window.sequence <= old["last_window"]:
                continue
            db.execute(
                "INSERT INTO flow_history(event_id,schema_version,organization_id,lab_id,generation,operator_user_id,epoch,window_sequence,observed_at,payload) VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    event_id,
                    row["organization_id"],
                    row["id"],
                    row["generation"],
                    capture.operator_user_id,
                    capture.epoch,
                    window.sequence,
                    datetime.fromtimestamp(window.observed_at, UTC),
                    Jsonb(encoded),
                ),
            )
            inserted += 1
    db.execute(
        "INSERT INTO lab_capture_snapshots(lab_id,generation,fence_at,captures) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT(lab_id) DO UPDATE SET generation=excluded.generation,fence_at=excluded.fence_at,captures=excluded.captures,received_at=clock_timestamp()",
        (row["id"], row["generation"], before, Jsonb(statuses)),
    )
    if inserted:
        db.execute(
            "DELETE FROM flow_history WHERE sequence IN (SELECT sequence FROM flow_history WHERE lab_id=%s ORDER BY sequence DESC OFFSET %s)",
            (row["id"], PER_LAB_LIMIT),
        )
    return inserted


def prune(db: DB) -> None:
    db.execute("DELETE FROM flow_history WHERE received_at < now()-interval '24 hours'")
    db.execute(
        "DELETE FROM flow_history WHERE sequence IN (SELECT sequence FROM flow_history ORDER BY sequence DESC OFFSET %s)",
        (GLOBAL_LIMIT,),
    )


@router.get("/labs/{lab_id}/traffic", response_model=TrafficView)
def traffic(lab_id: UUID, user: CurrentUser) -> TrafficView:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        lab = db.execute(
            "SELECT * FROM labs WHERE id=%s AND organization_id=%s", (lab_id, user.organization_id)
        ).fetchone()
        if not lab:
            raise HTTPException(404, "Miljøet findes ikke")
        snapshot = db.execute(
            "SELECT * FROM lab_capture_snapshots WHERE lab_id=%s AND generation=%s",
            (lab_id, lab["generation"]),
        ).fetchone()
        captures = (
            [CaptureStatus.model_validate(c) for c in snapshot["captures"]] if snapshot else []
        )
        # Live data is restricted to the current generation AND active epochs.
        epochs = [c.epoch for c in captures]
        rows = (
            db.execute(
                "SELECT * FROM flow_history WHERE organization_id=%s AND lab_id=%s AND generation=%s AND epoch=ANY(%s) ORDER BY sequence DESC LIMIT 100",
                (user.organization_id, lab_id, lab["generation"], epochs),
            ).fetchall()
            if epochs
            else []
        )
        return TrafficView(
            lab_id=lab_id,
            generation=lab["generation"],
            stale=lab["state"] != "READY"
            or not snapshot
            or datetime.now(UTC) - snapshot["received_at"] > timedelta(seconds=20),
            received_at=snapshot["received_at"] if snapshot else None,
            captures=captures,
            events=[
                TrafficEvent.model_validate(
                    {
                        key: value
                        for key, value in row.items()
                        if key not in ("organization_id", "window_sequence", "payload")
                    }
                    | {"window": row["payload"]}
                )
                for row in reversed(rows)
            ],
        )
