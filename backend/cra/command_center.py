"""Teacher projections of actual state/events, without terminal access or raw payloads."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field

from .auth import CurrentUser, require_scope
from .capacity import MAX_ACTIVE_LABS, RESERVATION_SLOTS
from .compute_nodes import nodes
from .contracts import Contract, Event, LabState, missions
from .db import connect
from .freshness import fresh
from .labs import lifecycle
from .scoring import earned_rows
from .teaching import SessionView, session_view

router = APIRouter(prefix="/api/v1/command-center")


class CenterLab(Contract):
    node_id: int = Field(ge=1, le=2)
    id: UUID
    owner_id: UUID
    owner_name: str
    owner_display_name: str
    team_id: UUID | None
    team_name: str | None
    mission_title: str
    mission_id: str
    generation: int
    state: LabState
    desired_state: str
    stale: bool
    error_code: str | None
    observed_at: datetime | None
    updated_at: datetime
    operator_count: int
    desktop_enabled: bool
    completed_objectives: list[str]
    objective_count: int
    earned_points: int


class CapacityView(Contract):
    node_id: int = Field(default=1, ge=1, le=2)
    accept_new: bool = False
    physical_memory_mib: int | None = None
    running_declared_memory_mib: int | None = None
    connected: bool
    stale: bool
    observed_at: datetime | None
    received_at: datetime | None
    available_memory_mib: int | None
    free_disk_gib: int | None
    active_labs: int | None
    reservations: int | None
    active_limit: int = MAX_ACTIVE_LABS
    reservation_limit: int = RESERVATION_SLOTS
    reserve_memory_mib: int = 4096


class CenterOverview(Contract):
    server_time: datetime
    session: SessionView | None
    labs: list[CenterLab]
    lab_count: int
    truncated: bool
    help_count: int = Field(ge=0)
    capacity: CapacityView
    nodes: list[CapacityView] = Field(default_factory=list)


class CenterEvent(Contract):
    event_id: UUID
    sequence: int
    stream: Literal["domain", "teaching"]
    schema_version: int
    lab_id: UUID | None
    generation: int | None
    source: Literal["control", "score", "teaching"]
    evidence_level: Literal["system", "validated", "submitted"]
    initiator_type: Literal["user", "system"]
    occurred_at: datetime
    received_at: datetime
    receipt_basis: Literal["recorded", "transaction_start_legacy"] = "recorded"
    kind: str
    summary: str


class CenterEvents(Contract):
    events: list[CenterEvent]
    domain_cursor: int
    teaching_cursor: int
    has_more: bool


class TeacherAction(Contract):
    generation: int = Field(ge=1, le=10000, strict=True)


@router.get("/overview", response_model=CenterOverview)
def overview(user: CurrentUser) -> CenterOverview:
    require_scope(user, user.organization_id, teacher=True)
    now = datetime.now(UTC)
    definitions = {(m.id, m.version): m for m in missions()}
    with connect() as db:
        rows = db.execute(
            "SELECT l.*,u.username AS owner_name,coalesce(nullif(m.teacher_alias,''),u.username) AS owner_display_name,t.name AS team_name,(SELECT count(*) FROM lab_participants p WHERE p.lab_id=l.id) AS operator_count FROM labs l JOIN users u ON u.id=l.user_id JOIN memberships m ON m.user_id=l.user_id AND m.organization_id=l.organization_id LEFT JOIN teams t ON t.id=l.team_id WHERE l.organization_id=%s AND l.state<>'DESTROYED' ORDER BY l.updated_at DESC,l.id LIMIT 101",
            (user.organization_id,),
        ).fetchall()
        labs = []
        for row in rows[:100]:
            mission = definitions[(row["mission_id"], row["mission_version"])]
            completed = db.execute(
                "SELECT objective_id FROM lab_completions WHERE lab_id=%s AND generation=%s ORDER BY objective_id",
                (row["id"], row["generation"]),
            ).fetchall()
            labs.append(
                CenterLab(
                    node_id=row["node_id"],
                    id=row["id"],
                    owner_id=row["user_id"],
                    owner_name=row["owner_name"],
                    owner_display_name=row["owner_display_name"],
                    team_id=row["team_id"],
                    team_name=row["team_name"],
                    mission_title=mission.title,
                    mission_id=mission.id,
                    generation=row["generation"],
                    state=row["state"],
                    desired_state=row["desired_state"],
                    stale=not fresh(row),
                    error_code=row["error_code"],
                    observed_at=row["last_observed_at"],
                    updated_at=row["updated_at"],
                    operator_count=max(1, row["operator_count"]),
                    desktop_enabled=row["desktop_enabled"],
                    completed_objectives=[item["objective_id"] for item in completed],
                    objective_count=len(mission.objectives),
                    earned_points=sum(item["points"] for item in earned_rows(db, row)),
                )
            )
        count = db.execute(
            "SELECT count(*) AS n FROM labs WHERE organization_id=%s AND state<>'DESTROYED'",
            (user.organization_id,),
        ).fetchone()
        session = db.execute(
            "SELECT * FROM teaching_sessions WHERE organization_id=%s AND NOT closed",
            (user.organization_id,),
        ).fetchone()
        try:
            configured = nodes()
        except (ValueError, OSError):
            configured = {}
        observed = {row["id"]: row for row in db.execute("SELECT * FROM node_status").fetchall()}
        bound = {
            row["node_id"] for row in db.execute("SELECT DISTINCT node_id FROM labs").fetchall()
        }
        capacities = []
        for identity in sorted({1} | set(configured) | set(observed) | bound):
            node = observed.get(identity)
            stale = (
                not node
                or not node["connected"]
                or now - node["received_at"] > timedelta(seconds=20)
                or identity not in configured
            )
            capacities.append(
                CapacityView.model_validate(
                    {
                        "node_id": identity,
                        "accept_new": bool(
                            identity in configured and configured[identity].accept_new
                        ),
                        "connected": bool(node and node["connected"] and identity in configured),
                        "stale": bool(stale),
                        **{
                            key: node[key] if node else None
                            for key in (
                                "observed_at",
                                "received_at",
                                "available_memory_mib",
                                "free_disk_gib",
                                "active_labs",
                                "reservations",
                                "physical_memory_mib",
                                "running_declared_memory_mib",
                            )
                        },
                    }
                )
            )
        help_total = db.execute(
            "SELECT count(*) AS n FROM help_requests WHERE organization_id=%s AND status<>'resolved'",
            (user.organization_id,),
        ).fetchone()
        assert help_total
        return CenterOverview(
            server_time=now,
            session=session_view(session) if session else None,
            labs=labs,
            lab_count=count["n"] if count else 0,
            truncated=len(rows) > 100,
            help_count=help_total["n"],
            capacity=capacities[0],
            nodes=capacities,
        )


TEACHING_LABELS = {
    "student.alias.changed": "Elevens visningsnavn ændret",
    "student.mission.released": "Opgave frigivet til en elev",
    "session.started": "Undervisningssession startet",
    "session.changed": "Undervisningens indstillinger ændret",
    "help.requested": "Hjælp efterspurgt af deltager",
    "help.answered": "Lærer har svaret på hjælp",
    "help.resolved": "Hjælp markeret afsluttet",
    "team.created": "Hold oprettet",
    "team.roles.changed": "Holdets arbejdsroller ændret",
    "team.note.saved": "Holdnoter gemt af deltager",
    "team.finding.submitted": "Fund indleveret af deltager",
}
STATES = {
    "READY": "Miljø klar",
    "STOPPED": "Miljø stoppet",
    "DESTROYED": "Miljø fjernet",
    "ERROR": "Teknisk labfejl",
}
ACTIONS = {
    "start": "Start bestilt",
    "stop": "Stop bestilt",
    "reset": "Nulstilling bestilt",
    "destroy": "Fjernelse bestilt",
}


@router.get("/events", response_model=CenterEvents)
def events(
    user: CurrentUser,
    domain_after: int = Query(0, ge=0, le=9223372036854775806),
    teaching_after: int = Query(0, ge=0, le=9223372036854775806),
    tail: bool = False,
) -> CenterEvents:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        order = " ORDER BY sequence DESC LIMIT 100" if tail else " ORDER BY sequence LIMIT 101"
        domain = db.execute(
            "SELECT sequence,event,created_at,recorded_at FROM outbox WHERE organization_id=%s AND sequence>%s"
            + order,
            (user.organization_id, domain_after),
        ).fetchall()
        teaching = db.execute(
            "SELECT sequence,event_id,schema_version,kind,evidence_level,occurred_at,received_at FROM teaching_events WHERE organization_id=%s AND sequence>%s"
            + order,
            (user.organization_id, teaching_after),
        ).fetchall()
    if tail:
        domain.reverse()
        teaching.reverse()
    result = []
    for row in domain[:100]:
        item = Event.model_validate(row["event"])
        if item.payload.kind == "lab.lifecycle":
            summary = STATES.get(item.payload.state, "Miljøstatus ændret")
        elif item.payload.kind == "objective.completed":
            summary = "Opgavemål valideret · " + str(item.payload.points_delta) + " nye point"
        else:
            summary = ACTIONS[item.payload.action]
        result.append(
            CenterEvent(
                event_id=item.event_id,
                sequence=row["sequence"],
                stream="domain",
                schema_version=item.schema_version,
                lab_id=item.lab_id,
                generation=item.generation,
                source=item.source,
                evidence_level=item.evidence_level,
                initiator_type=item.initiator_type,
                occurred_at=item.occurred_at,
                received_at=row["recorded_at"] or row["created_at"],
                receipt_basis="recorded" if row["recorded_at"] else "transaction_start_legacy",
                kind=item.payload.kind,
                summary=summary,
            )
        )
    for row in teaching[:100]:
        result.append(
            CenterEvent(
                event_id=row["event_id"],
                sequence=row["sequence"],
                stream="teaching",
                schema_version=row["schema_version"],
                lab_id=None,
                generation=None,
                source="teaching",
                evidence_level=row["evidence_level"],
                initiator_type="user",
                occurred_at=row["occurred_at"],
                received_at=row["received_at"],
                kind=row["kind"],
                summary=TEACHING_LABELS.get(row["kind"], "Undervisningshændelse"),
            )
        )
    return CenterEvents(
        events=sorted(result, key=lambda e: (e.occurred_at, e.stream, e.sequence)),
        domain_cursor=domain[min(len(domain), 100) - 1]["sequence"] if domain else domain_after,
        teaching_cursor=teaching[min(len(teaching), 100) - 1]["sequence"]
        if teaching
        else teaching_after,
        has_more=len(domain) > 100 or len(teaching) > 100,
    )


@router.post("/labs/{lab_id}/{action}", status_code=202)
def manage(
    lab_id: UUID,
    action: Literal["start", "stop", "reset"],
    body: TeacherAction,
    request: Request,
    user: CurrentUser,
) -> dict[str, Any]:
    return lifecycle(
        lab_id, action, request, user, teacher_management=True, expected_generation=body.generation
    )
