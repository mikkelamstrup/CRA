"""Teacher-controlled phases and explicit help; no inferred learner assessment."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import Field

from .auth import CurrentUser, Identity, require_scope
from .contracts import Contract
from .course import day_by_id
from .db import connect
from .jobs import DB
from .mission_access import release_source

router = APIRouter(prefix="/api/v1/teaching")


class SessionChange(Contract):
    revision: int = Field(ge=1, strict=True)
    day: int = Field(ge=1, le=8, strict=True)
    phase: Literal["theory", "solo", "team", "debrief"]
    terminal_enabled: bool
    announcement: str = Field(max_length=2000)
    closed: bool


class SessionView(SessionChange):
    id: UUID
    title: str
    updated_at: datetime


class SessionCreate(Contract):
    request_key: UUID
    title: str = Field(min_length=1, max_length=100)
    day: int = Field(ge=1, le=8, strict=True)


class HelpCreate(Contract):
    request_key: UUID
    message: str = Field(min_length=1, max_length=2000)


class HelpChange(Contract):
    revision: int = Field(ge=1, strict=True)
    reply: str = Field(max_length=2000)
    status: Literal["answered", "resolved"]


class HelpView(Contract):
    id: UUID
    username: str
    user_id: UUID
    message: str
    reply: str
    status: Literal["open", "answered", "resolved"]
    revision: int
    created_at: datetime
    updated_at: datetime


def event(
    db: DB,
    user: Identity,
    session_id: UUID | None,
    kind: str,
    payload: dict[str, Any],
    submitted: bool = False,
) -> None:
    # Same publication lock/order as the outbox: no cursor can skip an uncommitted ID.
    db.execute("SELECT pg_advisory_xact_lock(1415230002)")
    db.execute(
        "INSERT INTO teaching_events(event_id,organization_id,session_id,actor_id,kind,evidence_level,payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid4(),
            user.organization_id,
            session_id,
            user.user_id,
            kind,
            "submitted" if submitted else "system",
            Jsonb(payload),
        ),
    )


def session_view(row: dict[str, Any]) -> SessionView:
    return SessionView.model_validate({key: row[key] for key in SessionView.model_fields})


def require_terminal_enabled(db: DB, user: Identity) -> None:
    if (
        user.role == "student"
        and db.execute(
            "SELECT id FROM teaching_sessions WHERE organization_id=%s AND NOT closed AND NOT terminal_enabled",
            (user.organization_id,),
        ).fetchone()
    ):
        raise HTTPException(403, "terminal_paused_by_teacher")


def require_mission_released(
    db: DB, user: Identity, mission_id: str, mission_version: int = 1
) -> None:
    if (
        release_source(
            db, user.organization_id, user.user_id, user.role, mission_id, mission_version
        )
        == "locked"
    ):
        raise HTTPException(403, "mission_not_released")


@router.get("/session", response_model=SessionView | None)
def current(user: CurrentUser) -> SessionView | None:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM teaching_sessions WHERE organization_id=%s AND NOT closed",
            (user.organization_id,),
        ).fetchone()
        return session_view(row) if row else None


@router.post("/sessions", response_model=SessionView, status_code=201)
def create(body: SessionCreate, user: CurrentUser) -> SessionView:
    require_scope(user, user.organization_id, teacher=True)
    day_by_id(body.day)
    with connect() as db:
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230012))",
            (str(user.organization_id),),
        )
        old = db.execute(
            "SELECT * FROM teaching_sessions WHERE organization_id=%s AND created_by=%s AND request_key=%s",
            (user.organization_id, user.user_id, body.request_key),
        ).fetchone()
        if old:
            if old["title"] != body.title or old["initial_day"] != body.day:
                raise HTTPException(409, "idempotency_conflict")
            return session_view(old)
        if db.execute(
            "SELECT id FROM teaching_sessions WHERE organization_id=%s AND NOT closed",
            (user.organization_id,),
        ).fetchone():
            raise HTTPException(409, "session_already_open")
        row = db.execute(
            "INSERT INTO teaching_sessions(id,organization_id,created_by,request_key,title,day,initial_day) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                uuid4(),
                user.organization_id,
                user.user_id,
                body.request_key,
                body.title,
                body.day,
                body.day,
            ),
        ).fetchone()
        assert row
        event(db, user, row["id"], "session.started", {"day": body.day})
        return session_view(row)


@router.put("/sessions/{session_id}", response_model=SessionView)
def change(session_id: UUID, body: SessionChange, user: CurrentUser) -> SessionView:
    require_scope(user, user.organization_id, teacher=True)
    day_by_id(body.day)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM teaching_sessions WHERE id=%s AND organization_id=%s FOR UPDATE",
            (session_id, user.organization_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "not_found")
        if row["closed"]:
            raise HTTPException(409, "session_closed")
        if row["revision"] != body.revision:
            raise HTTPException(409, "session_conflict")
        row = db.execute(
            "UPDATE teaching_sessions SET day=%s,phase=%s,terminal_enabled=%s,announcement=%s,closed=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *",
            (
                body.day,
                body.phase,
                body.terminal_enabled,
                body.announcement,
                body.closed,
                session_id,
            ),
        ).fetchone()
        assert row
        event(
            db,
            user,
            session_id,
            "session.changed",
            {
                "day": body.day,
                "phase": body.phase,
                "terminal_enabled": body.terminal_enabled,
                "closed": body.closed,
            },
        )
        return session_view(row)


@router.get("/help", response_model=list[HelpView])
def help_list(user: CurrentUser) -> list[dict[str, Any]]:
    with connect() as db:
        base = "SELECT h.id,h.user_id,u.username,h.message,h.reply,h.status,h.revision,h.created_at,h.updated_at FROM help_requests h JOIN users u ON u.id=h.user_id WHERE h.organization_id=%s AND (%s OR h.user_id=%s)"
        scope = (user.organization_id, user.role == "teacher", user.user_id)
        active = db.execute(
            base + " AND h.status<>'resolved' ORDER BY h.created_at", scope
        ).fetchall()
        history = db.execute(
            base + " AND h.status='resolved' ORDER BY h.created_at DESC LIMIT 20", scope
        ).fetchall()
        return list(active) + list(history)


@router.post("/help", status_code=201)
def ask(body: HelpCreate, user: CurrentUser) -> dict[str, UUID]:
    if not body.message.strip():
        raise HTTPException(422, "message_required")
    with connect() as db:
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230013))", (str(user.user_id),)
        )
        old = db.execute(
            "SELECT id,message FROM help_requests WHERE organization_id=%s AND user_id=%s AND request_key=%s",
            (user.organization_id, user.user_id, body.request_key),
        ).fetchone()
        if old:
            if old["message"] != body.message:
                raise HTTPException(409, "idempotency_conflict")
            return {"id": old["id"]}
        if db.execute(
            "SELECT id FROM help_requests WHERE organization_id=%s AND user_id=%s AND status IN ('open','answered')",
            (user.organization_id, user.user_id),
        ).fetchone():
            raise HTTPException(409, "help_already_open")
        session = db.execute(
            "SELECT id FROM teaching_sessions WHERE organization_id=%s AND NOT closed",
            (user.organization_id,),
        ).fetchone()
        identity = uuid4()
        db.execute(
            "INSERT INTO help_requests(id,organization_id,user_id,session_id,request_key,message) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                identity,
                user.organization_id,
                user.user_id,
                session["id"] if session else None,
                body.request_key,
                body.message,
            ),
        )
        event(
            db,
            user,
            session["id"] if session else None,
            "help.requested",
            {"help_id": str(identity)},
            submitted=True,
        )
        return {"id": identity}


@router.put("/help/{help_id}")
def answer(help_id: UUID, body: HelpChange, user: CurrentUser) -> dict[str, bool]:
    require_scope(user, user.organization_id, teacher=True)
    if body.status == "answered" and not body.reply.strip():
        raise HTTPException(422, "reply_required")
    with connect() as db:
        row = db.execute(
            "SELECT * FROM help_requests WHERE id=%s AND organization_id=%s FOR UPDATE",
            (help_id, user.organization_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "not_found")
        if row["revision"] != body.revision:
            raise HTTPException(409, "help_conflict")
        if row["status"] == "resolved":
            raise HTTPException(409, "help_resolved")
        db.execute(
            "UPDATE help_requests SET reply=%s,status=%s,responder_id=%s,revision=revision+1,updated_at=now() WHERE id=%s",
            (body.reply, body.status, user.user_id, help_id),
        )
        event(db, user, row["session_id"], "help." + body.status, {"help_id": str(help_id)})
        return {"saved": True}
