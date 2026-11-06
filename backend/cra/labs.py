"""Authorised lab lifecycle: accept intent, reserve capacity, enqueue durable work."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, ValidationError

from .auth import CurrentUser, require_scope
from .capacity import MAX_ACTIVE_LABS, RESERVATION_SLOTS, target_ip
from .compute_nodes import nodes
from .contracts import ActionPayload, Contract, Event, LabState, Mission, missions
from .db import connect
from .freshness import fresh
from .jobs import enqueue, publish
from .lab_access import ACCESS, accessible_lab
from .node_client import NodeRejected, NodeUnavailable
from .node_client import request as node_request
from .node_contracts import NodeInventory
from .teaching import require_mission_released

router = APIRouter(prefix="/api/v1")


class CreateLab(Contract):
    mission_id: str = Field(min_length=1, max_length=48)
    mission_version: int = Field(ge=1, strict=True)
    team_id: UUID | None = None
    desktop_enabled: bool = Field(default=False, strict=True)


class LabView(Contract):
    id: UUID
    mission_id: str
    mission_version: int
    generation: int
    state: LabState
    desired_state: str
    target_ip: str | None
    last_observed_at: datetime | None
    error_code: str | None
    team_id: UUID | None
    desktop_enabled: bool
    can_manage: bool
    can_operate: bool


def view(row: dict[str, Any], user: CurrentUser, db: Any) -> LabView:
    row = {
        **row,
        "can_manage": row["user_id"] == user.user_id
        and (row["team_id"] is None or user.role == "teacher"),
        "can_operate": row["team_id"] is None,
    }
    if row["team_id"] is not None:
        participant = db.execute(
            "SELECT p.ordinal FROM lab_participants p JOIN team_members m ON m.user_id=p.user_id AND m.team_id=%s WHERE p.lab_id=%s AND p.user_id=%s",
            (row["team_id"], row["id"], user.user_id),
        ).fetchone()
        row["can_operate"] = participant is not None
        if participant and row["slot"] is not None:
            row["target_ip"] = target_ip(row["slot"], participant["ordinal"])
    if row["state"] in ("READY", "STOPPED") and not fresh(row):
        row = {**row, "state": "ERROR", "error_code": "observation_stale"}
    return LabView.model_validate(
        {
            key: str(row[key]) if key == "target_ip" and row[key] else row[key]
            for key in LabView.model_fields
        }
    )


def request_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key", "")
    try:
        return str(UUID(key))
    except ValueError:
        raise HTTPException(400, "idempotency_key_required") from None


def mission_by_id(identity: str, version: int) -> Mission:
    for mission in missions():
        if mission.id == identity and mission.version == version:
            return mission
    raise HTTPException(404, "mission_not_found")


def with_desktop(mission: Mission, enabled: bool) -> Mission:
    if not enabled:
        return mission
    resource = mission.resources.model_copy(
        update={"operator_profile": "desktop", "operator_memory_mib": 2048}
    )
    return mission.model_copy(update={"resources": resource})


def require_transition(
    db: Any,
    user: CurrentUser,
    participants: list[UUID],
    team_id: UUID | None,
    *,
    owner_id: UUID | None = None,
) -> None:
    if team_id is not None:
        if db.execute(
            "SELECT id FROM labs WHERE organization_id=%s AND team_id IS NULL AND user_id=ANY(%s) AND state<>'DESTROYED' AND (state<>'STOPPED' OR last_observed_at IS NULL OR last_observed_at<now()-interval '20 seconds')",
            (user.organization_id, participants),
        ).fetchone():
            raise HTTPException(409, "stop_solo_first")
    elif db.execute(
        "SELECT l.id FROM labs l JOIN lab_participants p ON p.lab_id=l.id JOIN team_members m ON m.team_id=l.team_id AND m.user_id=p.user_id WHERE l.organization_id=%s AND p.user_id=%s AND l.state<>'DESTROYED' AND (l.state<>'STOPPED' OR l.last_observed_at IS NULL OR l.last_observed_at<now()-interval '20 seconds')",
        (user.organization_id, owner_id or user.user_id),
    ).fetchone():
        raise HTTPException(409, "stop_team_first")


def admit(
    db: Any,
    mission: Mission,
    operator_count: int,
    existing: dict[str, Any] | None = None,
    *,
    node_id: int = 1,
) -> int:
    if existing is not None:
        node_id = existing["node_id"]
    reservations = db.execute(
        "SELECT l.*,(SELECT count(*) FROM lab_participants p WHERE p.lab_id=l.id) AS operator_count FROM labs l WHERE l.node_id=%s AND l.slot IS NOT NULL",
        (node_id,),
    ).fetchall()
    try:
        inventory = (
            node_request("/v1/inventory")
            if node_id == 1
            else node_request("/v1/inventory", node_id=node_id)
        )
    except (NodeUnavailable, NodeRejected):
        if existing is not None:
            # Existing reservation: accept bounded durable intent during an outage.
            # The node repeats RAM/disk/active admission before any VM mutation.
            active = {str(item["id"]) for item in reservations if item["state"] != "STOPPED"}
            active.discard(str(existing["id"]))
            if len(active) >= MAX_ACTIVE_LABS:
                raise HTTPException(409, "capacity_unavailable") from None
            return int(existing["slot"])
        raise HTTPException(503, "node_unavailable") from None
    if inventory["architecture"] != mission.resources.architecture:
        raise HTTPException(409, "architecture_mismatch")
    try:
        inventory = NodeInventory.model_validate(inventory).model_dump(mode="json")
    except ValidationError:
        raise HTTPException(503, "node_unavailable") from None
    if (
        inventory["node_id"] != node_id
        or inventory["physical_memory_mib"] is None
        or inventory["running_declared_memory_mib"] is None
    ):
        raise HTTPException(503, "node_unavailable")
    if abs(datetime.now(UTC).timestamp() - inventory["observed_at"]) > 20:
        raise HTTPException(503, "node_unavailable")
    # Reserve RAM for every accepted active intent, including those not yet on the node.
    # Running declared RAM already includes control and orphan VMs; credit is exact-owner only.
    pending_memory = 0
    for reserved in reservations:
        if reserved["state"] in ("STOPPED", "DESTROYED") or (
            existing and reserved["id"] == existing["id"]
        ):
            continue
        profile = with_desktop(
            mission_by_id(reserved["mission_id"], reserved["mission_version"]),
            reserved["desktop_enabled"],
        )
        planned = (
            profile.resources.operator_memory_mib * max(1, reserved["operator_count"])
            + profile.resources.target_memory_mib
        )
        observed = next(
            (item for item in inventory["labs"] if item["id"] == str(reserved["id"])), None
        )
        running = 0
        if (
            observed
            and observed["ownership_valid"]
            and all(
                str(observed[k]) == str(reserved[k])
                for k in ("organization_id", "user_id", "generation")
            )
        ):
            running = min(planned, observed["running_memory_mib"])
        pending_memory += planned - running
    required = (
        mission.resources.operator_memory_mib * max(1, operator_count)
        + mission.resources.target_memory_mib
    )
    credit = 0
    if existing is not None:
        own = next((item for item in inventory["labs"] if item["id"] == str(existing["id"])), None)
        if (
            own
            and own["ownership_valid"]
            and own["organization_id"] == str(existing["organization_id"])
            and own["user_id"] == str(existing["user_id"])
            and own["generation"] == existing["generation"]
        ):
            credit = min(required, own["running_memory_mib"])
    if (
        inventory["available_memory_mib"] < required - credit + pending_memory + 4096
        or inventory["physical_memory_mib"]
        < inventory["running_declared_memory_mib"] + required - credit + pending_memory + 4096
    ):
        raise HTTPException(409, "capacity_unavailable")
    reserved_disks = {
        str(item["id"]): 12 * (1 + max(1, item["operator_count"])) for item in reservations
    }
    for observed in inventory["labs"]:
        reserved_disks[str(observed["id"])] = max(
            reserved_disks.get(str(observed["id"]), 0),
            12 * (1 + max(1, len(observed["operators"]))),
        )
    if existing is not None:
        reserved_disks.pop(str(existing["id"]), None)
    active = {str(item["id"]) for item in reservations if item["state"] != "STOPPED"}
    active.update(
        str(item["id"])
        for item in inventory["labs"]
        if item["state"] == "PROVISIONING"
        or item["target_running"]
        or item["operator_running"]
        or any(op["running"] for op in item["operators"])
    )
    if existing is not None:
        active.discard(str(existing["id"]))
    if len(active) >= inventory["max_labs"]:
        raise HTTPException(409, "capacity_unavailable")
    if inventory["free_disk_gib"] < sum(reserved_disks.values()) + 12 * (
        1 + max(1, operator_count)
    ):
        raise HTTPException(409, "capacity_unavailable")
    used = {row["slot"] for row in reservations}
    # Include node reservations to avoid reusing an orphaned slot after control failure.
    used.update(lab["slot"] for lab in inventory["labs"] if lab["slot"] is not None)
    slots = [slot for slot in range(RESERVATION_SLOTS) if slot not in used]
    if existing is not None:
        return int(existing["slot"])
    if not slots:
        raise HTTPException(409, "capacity_unavailable")
    return slots[0]


def place(db: Any, mission: Mission, operator_count: int) -> tuple[int, int]:
    try:
        configured = nodes()
    except (ValueError, OSError):
        raise HTTPException(503, "node_unavailable") from None
    counts = {
        row["node_id"]: row["n"]
        for row in db.execute(
            "SELECT node_id,count(*) AS n FROM labs WHERE slot IS NOT NULL AND state<>'STOPPED' GROUP BY node_id"
        ).fetchall()
    }
    errors = []
    for target in sorted(configured.values(), key=lambda item: (counts.get(item.id, 0), item.id)):
        if not target.accept_new:
            continue
        try:
            return target.id, admit(db, mission, operator_count, node_id=target.id)
        except HTTPException as error:
            errors.append(error)
    # Preserve useful single-node diagnostics; never route an existing lab elsewhere.
    if errors:
        raise errors[0]
    raise HTTPException(503, "node_unavailable")


@router.get("/labs", response_model=list[LabView])
def list_labs(user: CurrentUser) -> list[LabView]:
    with connect() as db:
        return [
            view(row, user, db)
            for row in db.execute(
                "SELECT l.* FROM labs l WHERE "
                + ACCESS
                + " ORDER BY (l.state='DESTROYED'),l.created_at DESC LIMIT 20",
                (user.organization_id, user.user_id, user.user_id),
            ).fetchall()
        ]


@router.get("/labs/{lab_id}", response_model=LabView)
def get_lab(lab_id: UUID, user: CurrentUser) -> LabView:
    with connect() as db:
        row = accessible_lab(db, lab_id, user)
        return view(row, user, db)


@router.post("/labs", status_code=202)
def create_lab(body: CreateLab, request: Request, user: CurrentUser) -> dict[str, Any]:
    key = request_key(request)
    mission = with_desktop(
        mission_by_id(body.mission_id, body.mission_version), body.desktop_enabled
    )
    with connect() as db:
        require_mission_released(db, user, body.mission_id, body.mission_version)
        participants = []
        if (
            body.mission_id
            in (
                "cybercon-incident",
                "request-forge",
                "incident-http",
                "incident-evidence",
                "incident-containment",
                "incident-ownership",
            )
            and body.team_id is not None
        ):
            raise HTTPException(422, "individual_mission_required")
        if body.team_id is not None:
            require_scope(user, user.organization_id, teacher=True)
            team = db.execute(
                "SELECT * FROM teams WHERE id=%s AND organization_id=%s AND created_by=%s FOR UPDATE",
                (body.team_id, user.organization_id, user.user_id),
            ).fetchone()
            if not team:
                raise HTTPException(404, "team_not_found")
            participants = db.execute(
                "SELECT m.user_id,m.ordinal,u.active FROM team_members m JOIN users u ON u.id=m.user_id WHERE m.team_id=%s ORDER BY ordinal",
                (body.team_id,),
            ).fetchall()
            if not participants or any(not p["active"] for p in participants):
                raise HTTPException(409, "team_members_unavailable")
        db.execute("SELECT pg_advisory_xact_lock(1415230003)")
        old = db.execute(
            "SELECT * FROM jobs WHERE organization_id=%s AND actor_id=%s AND request_key=%s",
            (user.organization_id, user.user_id, key),
        ).fetchone()
        if old:
            if (
                old["kind"] != "create"
                or old["payload"]["mission_id"] != body.mission_id
                or old["payload"]["mission_version"] != body.mission_version
                or old["payload"].get("desktop_enabled", False) != body.desktop_enabled
                or old["payload"].get("team_id") != (str(body.team_id) if body.team_id else None)
            ):
                raise HTTPException(409, "idempotency_conflict")
            return {"operation_id": old["id"], "lab_id": old["payload"]["lab_id"]}
        if db.execute(
            "SELECT id FROM labs WHERE organization_id=%s AND state<>'DESTROYED' AND ((%s::uuid IS NULL AND team_id IS NULL AND user_id=%s) OR team_id=%s)",
            (user.organization_id, body.team_id, user.user_id, body.team_id),
        ).fetchone():
            raise HTTPException(409, "active_lab_exists")
        require_transition(db, user, [p["user_id"] for p in participants], body.team_id)
        node_id, slot = place(db, mission, max(1, len(participants)))
        lab_id = uuid4()
        db.execute(
            "INSERT INTO labs(id,organization_id,user_id,mission_id,mission_version,state,slot,team_id,desktop_enabled,node_id) VALUES (%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s)",
            (
                lab_id,
                user.organization_id,
                user.user_id,
                body.mission_id,
                body.mission_version,
                slot,
                body.team_id,
                body.desktop_enabled,
                node_id,
            ),
        )
        for participant in participants:
            db.execute(
                "INSERT INTO lab_participants(lab_id,user_id,ordinal) VALUES (%s,%s,%s)",
                (lab_id, participant["user_id"], participant["ordinal"]),
            )
        job = enqueue(
            db,
            user.organization_id,
            user.user_id,
            key,
            "create",
            {
                "lab_id": str(lab_id),
                "node_id": node_id,
                "generation": 1,
                "mission_id": body.mission_id,
                "mission_version": body.mission_version,
                "slot": slot,
                "team_id": str(body.team_id) if body.team_id else None,
                "desktop_enabled": body.desktop_enabled,
            },
        )
        db.execute("UPDATE labs SET current_job_id=%s WHERE id=%s", (job["id"], lab_id))
        return {"operation_id": job["id"], "lab_id": lab_id}


def lifecycle(
    lab_id: UUID,
    action: Literal["start", "stop", "reset", "destroy"],
    request: Request,
    user: CurrentUser,
    *,
    teacher_management: bool = False,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    if teacher_management:
        require_scope(user, user.organization_id, teacher=True)
        if expected_generation is None:
            raise HTTPException(422, "generation_required")
    key = request_key(request)
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(1415230003)")
        row = db.execute(
            "SELECT * FROM labs WHERE id=%s AND organization_id=%s AND (%s OR user_id=%s) FOR UPDATE",
            (lab_id, user.organization_id, teacher_management, user.user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "not_found")
        if row["team_id"] is not None:
            require_scope(user, user.organization_id, teacher=True)
        old = db.execute(
            "SELECT * FROM jobs WHERE organization_id=%s AND actor_id=%s AND request_key=%s",
            (user.organization_id, user.user_id, key),
        ).fetchone()
        if old:
            if (
                old["kind"] != action
                or old["payload"]["lab_id"] != str(lab_id)
                or old["resource_owner_id"] != row["user_id"]
                or old["payload"].get("expected_generation") != expected_generation
                or old["payload"].get("teacher_management", False) != teacher_management
            ):
                raise HTTPException(409, "idempotency_conflict")
            return {"operation_id": old["id"], "lab_id": lab_id}
        if expected_generation is not None and row["generation"] != expected_generation:
            raise HTTPException(409, "generation_changed")
        allowed = {
            "start": {"STOPPED"},
            "stop": {"READY"},
            "reset": {"READY", "STOPPED", "ERROR"},
            "destroy": {"READY", "STOPPED", "ERROR"},
        }
        recovery_start = False
        if action == "start" and row["state"] == "ERROR" and row["error_code"] == "runtime_drift":
            previous = db.execute(
                "SELECT status FROM jobs WHERE id=%s", (row["current_job_id"],)
            ).fetchone()
            recovery_start = bool(previous and previous["status"] == "SUCCEEDED")
            if not recovery_start:
                raise HTTPException(409, "lifecycle_incomplete")
        if row["state"] not in allowed[action] and not recovery_start:
            raise HTTPException(409, "invalid_transition")
        if action in ("start", "reset"):
            participants = db.execute(
                "SELECT user_id FROM lab_participants WHERE lab_id=%s ORDER BY ordinal", (lab_id,)
            ).fetchall()
            require_transition(
                db,
                user,
                [p["user_id"] for p in participants],
                row["team_id"],
                owner_id=row["user_id"],
            )
            admit(
                db,
                with_desktop(
                    mission_by_id(row["mission_id"], row["mission_version"]), row["desktop_enabled"]
                ),
                max(1, len(participants)),
                row,
            )
        desired = {"start": "READY", "stop": "STOPPED", "reset": "READY", "destroy": "DESTROYED"}[
            action
        ]
        state = {
            "start": "STARTING",
            "stop": "STOPPING",
            "reset": "RESETTING",
            "destroy": "DESTROYING",
        }[action]
        generation = row["generation"] + (1 if action == "reset" else 0)
        if generation > 10000:
            raise HTTPException(409, "generation_limit")
        db.execute(
            "UPDATE labs SET state=%s,desired_state=%s,generation=%s,error_code=NULL,updated_at=now() WHERE id=%s",
            (state, desired, generation, lab_id),
        )
        job = enqueue(
            db,
            user.organization_id,
            user.user_id,
            key,
            action,
            {
                "lab_id": str(lab_id),
                "node_id": row["node_id"],
                "generation": generation,
                "mission_id": row["mission_id"],
                "mission_version": row["mission_version"],
                "slot": row["slot"],
                "expected_generation": expected_generation,
                "teacher_management": teacher_management,
            },
            resource_owner_id=row["user_id"],
        )
        db.execute("UPDATE labs SET current_job_id=%s WHERE id=%s", (job["id"], lab_id))
        publish(
            db,
            Event(
                schema_version=1,
                event_id=uuid4(),
                organization_id=user.organization_id,
                lab_id=lab_id,
                generation=generation,
                actor_id=user.user_id,
                initiator_type="user",
                occurred_at=datetime.now(UTC),
                source="control",
                evidence_level="system",
                payload=ActionPayload(
                    kind="lab.action_requested",
                    action=action,
                    requested_generation=row["generation"],
                ),
            ),
        )
        return {"operation_id": job["id"], "lab_id": lab_id}


@router.get("/operations/{operation_id}")
def operation(operation_id: UUID, user: CurrentUser) -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            "SELECT id,status,attempts,error_code FROM jobs WHERE id=%s AND organization_id=%s AND actor_id=%s",
            (operation_id, user.organization_id, user.user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "not_found")
        return row


@router.post("/labs/{lab_id}/start", status_code=202)
def start_lab(lab_id: UUID, request: Request, user: CurrentUser) -> dict[str, Any]:
    return lifecycle(lab_id, "start", request, user)


@router.post("/labs/{lab_id}/stop", status_code=202)
def stop_lab(lab_id: UUID, request: Request, user: CurrentUser) -> dict[str, Any]:
    return lifecycle(lab_id, "stop", request, user)


@router.post("/labs/{lab_id}/destroy", status_code=202)
def destroy_lab(lab_id: UUID, request: Request, user: CurrentUser) -> dict[str, Any]:
    return lifecycle(lab_id, "destroy", request, user)


@router.post("/labs/{lab_id}/reset", status_code=202)
def reset_lab(lab_id: UUID, request: Request, user: CurrentUser) -> dict[str, Any]:
    return lifecycle(lab_id, "reset", request, user)
