"""Bounded teacher replay of recorded evidence, never a reconstructed terminal."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from .auth import CurrentUser, require_scope
from .command_center import ACTIONS, STATES
from .contracts import Contract, Event, LabState, missions
from .db import connect
from .flow_contracts import FlowWindow

router = APIRouter(prefix="/api/v1/command-center/replay")


class ReplayLab(Contract):
    id: UUID
    label: str
    mission_title: str
    generation: int
    state: LabState


class ReplayLabs(Contract):
    labs: list[ReplayLab]
    truncated: bool


class ReplayItem(Contract):
    event_id: UUID
    schema_version: int
    generation: int
    source: Literal["control", "score", "lab_link"]
    evidence_level: Literal["system", "validated", "observed"]
    occurred_at: datetime
    received_at: datetime
    receipt_basis: Literal["recorded", "transaction_start_legacy"]
    summary: str
    operator_number: int | None = None
    window: FlowWindow | None = None


class ReplayView(Contract):
    lab_id: UUID
    generation: int
    generations: list[int]
    from_time: datetime
    to_time: datetime
    events: list[ReplayItem]
    domain_truncated: bool
    flow_truncated: bool
    flow_retention_hours: Literal[24] = 24
    domain_limit: Literal[100] = 100
    flow_limit: Literal[100] = 100
    historical: Literal[True] = True


@router.get("/labs", response_model=ReplayLabs)
def labs(user: CurrentUser) -> ReplayLabs:
    require_scope(user, user.organization_id, teacher=True)
    definitions = {(m.id, m.version): m for m in missions()}
    with connect() as db:
        rows = db.execute(
            "SELECT l.id,l.generation,l.state,l.mission_id,l.mission_version,coalesce(t.name,u.username) AS label FROM labs l JOIN users u ON u.id=l.user_id LEFT JOIN teams t ON t.id=l.team_id WHERE l.organization_id=%s ORDER BY l.updated_at DESC,l.id LIMIT 101",
            (user.organization_id,),
        ).fetchall()
    return ReplayLabs(
        labs=[
            ReplayLab(
                id=r["id"],
                label=r["label"],
                mission_title=definitions[(r["mission_id"], r["mission_version"])].title,
                generation=r["generation"],
                state=r["state"],
            )
            for r in rows[:100]
        ],
        truncated=len(rows) > 100,
    )


@router.get("/labs/{lab_id}", response_model=ReplayView)
def replay(
    lab_id: UUID,
    user: CurrentUser,
    generation: int | None = Query(None, ge=1, le=10000),
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> ReplayView:
    require_scope(user, user.organization_id, teacher=True)
    end = to_time or datetime.now(UTC)
    try:
        start = from_time or end - timedelta(hours=1)
    except OverflowError:
        raise HTTPException(422, "replay_time_range") from None
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or not timedelta(0) < end - start <= timedelta(hours=24)
    ):
        raise HTTPException(422, "replay_time_range")
    with connect() as db:
        lab = db.execute(
            "SELECT * FROM labs WHERE id=%s AND organization_id=%s", (lab_id, user.organization_id)
        ).fetchone()
        if not lab:
            raise HTTPException(404, "not_found")
        selected = generation or lab["generation"]
        known = db.execute(
            "SELECT DISTINCT (event->>'generation')::integer AS generation FROM outbox WHERE organization_id=%s AND event->>'lab_id'=%s UNION SELECT DISTINCT generation FROM flow_history WHERE organization_id=%s AND lab_id=%s",
            (user.organization_id, str(lab_id), user.organization_id, lab_id),
        ).fetchall()
        generations = sorted({lab["generation"], *(r["generation"] for r in known)}, reverse=True)
        if selected not in generations:
            raise HTTPException(404, "generation_not_recorded")
        domain = db.execute(
            "SELECT event,created_at,recorded_at FROM outbox WHERE organization_id=%s AND event->>'lab_id'=%s AND event->>'generation'=%s AND event_occurred_at>=%s AND event_occurred_at<=%s ORDER BY event_occurred_at DESC,sequence DESC LIMIT 101",
            (user.organization_id, str(lab_id), str(selected), start, end),
        ).fetchall()
        flows = db.execute(
            "SELECT * FROM flow_history WHERE organization_id=%s AND lab_id=%s AND generation=%s AND observed_at>=%s AND observed_at<=%s ORDER BY observed_at DESC,sequence DESC LIMIT 101",
            (user.organization_id, lab_id, selected, start, end),
        ).fetchall()
        operators = db.execute(
            "SELECT user_id FROM lab_participants WHERE lab_id=%s ORDER BY ordinal", (lab_id,)
        ).fetchall()
        actor_order = [r["user_id"] for r in operators] or [lab["user_id"]]
    items = []
    for row in domain[:100]:
        event = Event.model_validate(row["event"])
        if event.payload.kind == "objective.completed":
            summary = f"Opgavemål valideret · {event.payload.points_delta} nye point"
        elif event.payload.kind == "lab.lifecycle":
            summary = STATES.get(event.payload.state, "Miljøstatus ændret")
        else:
            summary = ACTIONS[event.payload.action]
        items.append(
            ReplayItem(
                event_id=event.event_id,
                schema_version=event.schema_version,
                generation=event.generation,
                source=event.source,
                evidence_level=event.evidence_level,
                occurred_at=event.occurred_at,
                received_at=row["recorded_at"] or row["created_at"],
                receipt_basis="recorded" if row["recorded_at"] else "transaction_start_legacy",
                summary=summary,
            )
        )
    for row in flows[:100]:
        # Ordinal follows the immutable lab assignment, never a packet address.
        actor = row["operator_user_id"]
        ordinal = actor_order.index(actor) + 1 if actor in actor_order else None
        items.append(
            ReplayItem(
                event_id=row["event_id"],
                schema_version=row["schema_version"],
                generation=row["generation"],
                source="lab_link",
                evidence_level="observed",
                occurred_at=row["observed_at"],
                received_at=row["received_at"],
                receipt_basis="recorded",
                summary="IP-trafik observeret",
                operator_number=ordinal,
                window=FlowWindow.model_validate(row["payload"]),
            )
        )
    items.sort(key=lambda e: (e.occurred_at, e.received_at, str(e.event_id)))
    return ReplayView(
        lab_id=lab_id,
        generation=selected,
        generations=generations,
        from_time=start,
        to_time=end,
        events=items,
        domain_truncated=len(domain) > 100,
        flow_truncated=len(flows) > 100,
    )
