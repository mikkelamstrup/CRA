"""Teacher-assigned teams. Membership is immutable while a lab retains resources."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import Field, model_validator

from .auth import CurrentUser, Identity, require_scope
from .contracts import Contract
from .db import connect
from .jobs import DB
from .teaching import event

router = APIRouter(prefix="/api/v1/teams")


class TeamMember(Contract):
    user_id: UUID
    role: Literal["coordinator", "analyst", "operator", "reporter"]


class TeamCreate(Contract):
    request_key: UUID
    name: str = Field(min_length=1, max_length=80)
    members: list[TeamMember] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_members(self) -> "TeamCreate":
        if len({m.user_id for m in self.members}) != len(self.members):
            raise ValueError("Duplicate member")
        return self


class TeamMemberView(TeamMember):
    username: str
    active: bool


class TeamView(Contract):
    id: UUID
    name: str
    revision: int
    created_by: UUID
    members: list[TeamMemberView]


def team_view(db: DB, row: dict[str, Any]) -> TeamView:
    members = db.execute(
        "SELECT m.user_id,m.role,u.username,u.active FROM team_members m JOIN users u ON u.id=m.user_id WHERE team_id=%s ORDER BY ordinal",
        (row["id"],),
    ).fetchall()
    return TeamView(
        id=row["id"],
        name=row["name"],
        revision=row["revision"],
        created_by=row["created_by"],
        members=[TeamMemberView.model_validate(m) for m in members],
    )


@router.get("", response_model=list[TeamView])
def list_teams(user: CurrentUser) -> list[TeamView]:
    with connect() as db:
        rows = db.execute(
            "SELECT t.* FROM teams t WHERE t.organization_id=%s AND (%s OR EXISTS(SELECT 1 FROM team_members m WHERE m.team_id=t.id AND m.user_id=%s)) ORDER BY t.created_at",
            (user.organization_id, user.role == "teacher", user.user_id),
        ).fetchall()
        return [team_view(db, row) for row in rows]


@router.get("/roster")
def roster(user: CurrentUser) -> list[dict[str, Any]]:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        return db.execute(
            "SELECT u.id AS user_id,u.username,m.role FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.organization_id=%s AND u.active ORDER BY u.username",
            (user.organization_id,),
        ).fetchall()


@router.post("", response_model=TeamView, status_code=201)
def create_team(body: TeamCreate, user: CurrentUser) -> TeamView:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230013))",
            (str(user.organization_id),),
        )
        old = db.execute(
            "SELECT * FROM teams WHERE organization_id=%s AND created_by=%s AND request_key=%s",
            (user.organization_id, user.user_id, body.request_key),
        ).fetchone()
        if old:
            current = team_view(db, old)
            if current.name != body.name or old["creation_members"] != [
                m.model_dump(mode="json") for m in body.members
            ]:
                raise HTTPException(409, "idempotency_conflict")
            return current
        for member in body.members:
            if not db.execute(
                "SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.id=%s AND m.organization_id=%s AND u.active",
                (member.user_id, user.organization_id),
            ).fetchone():
                raise HTTPException(404, "member_not_found")
        identity = uuid4()
        row = db.execute(
            "INSERT INTO teams(id,organization_id,created_by,name,request_key,creation_members) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                identity,
                user.organization_id,
                user.user_id,
                body.name,
                body.request_key,
                Jsonb([m.model_dump(mode="json") for m in body.members]),
            ),
        ).fetchone()
        assert row
        for index, member in enumerate(body.members):
            db.execute(
                "INSERT INTO team_members(team_id,organization_id,user_id,ordinal,role) VALUES (%s,%s,%s,%s,%s)",
                (identity, user.organization_id, member.user_id, index, member.role),
            )
        event(
            db,
            user,
            None,
            "team.created",
            {"team_id": str(identity), "member_count": len(body.members)},
        )
        return team_view(db, row)


class RolesChange(Contract):
    revision: int = Field(ge=1, strict=True)
    members: list[TeamMember] = Field(min_length=1, max_length=4)


class TeamNotebookWrite(Contract):
    revision: int = Field(ge=0, strict=True)
    body: str = Field(max_length=16000)


class TeamNotebook(TeamNotebookWrite):
    updated_by: str | None
    updated_at: datetime | None
    evidence_level: Literal["submitted"] = "submitted"


class FindingCreate(Contract):
    request_key: UUID
    body: str = Field(min_length=1, max_length=2000)
    source: str = Field(min_length=1, max_length=300)


class FindingView(Contract):
    id: UUID
    actor_id: UUID
    username: str
    body: str
    source: str
    created_at: datetime
    evidence_level: Literal["submitted"] = "submitted"


class FindingsPage(Contract):
    items: list[FindingView]
    next_cursor: UUID | None


def require_team(db: DB, team_id: UUID, user: Identity, *, lock: bool = False) -> dict[str, Any]:
    row = db.execute(
        "SELECT t.* FROM teams t WHERE t.id=%s AND t.organization_id=%s AND (%s OR EXISTS(SELECT 1 FROM team_members m WHERE m.team_id=t.id AND m.user_id=%s))"
        + (" FOR UPDATE OF t" if lock else ""),
        (team_id, user.organization_id, user.role == "teacher", user.user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "team_not_found")
    return row


@router.put("/{team_id}/roles", response_model=TeamView)
def change_roles(team_id: UUID, body: RolesChange, user: CurrentUser) -> TeamView:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        row = require_team(db, team_id, user, lock=True)
        if row["created_by"] != user.user_id:
            raise HTTPException(403, "team_owner_required")
        current = team_view(db, row)
        incoming = {member.user_id: member.role for member in body.members}
        previous = {member.user_id: member.role for member in current.members}
        if len(incoming) != len(body.members) or set(incoming) != set(previous):
            raise HTTPException(409, "team_members_immutable")
        if body.revision != row["revision"]:
            if body.revision + 1 == row["revision"] and incoming == previous:
                return current
            raise HTTPException(409, "team_revision_conflict")
        for member in body.members:
            db.execute(
                "UPDATE team_members SET role=%s WHERE team_id=%s AND user_id=%s",
                (member.role, team_id, member.user_id),
            )
        updated = db.execute(
            "UPDATE teams SET revision=revision+1 WHERE id=%s RETURNING *", (team_id,)
        ).fetchone()
        assert updated
        event(
            db,
            user,
            None,
            "team.roles.changed",
            {"team_id": str(team_id), "revision": updated["revision"]},
        )
        return team_view(db, updated)


def notebook_view(db: DB, team_id: UUID) -> TeamNotebook:
    row = db.execute(
        "SELECT n.body,n.revision,n.updated_at,u.username AS updated_by FROM team_notebooks n JOIN users u ON u.id=n.updated_by WHERE n.team_id=%s",
        (team_id,),
    ).fetchone()
    return TeamNotebook.model_validate(
        row or {"body": "", "revision": 0, "updated_by": None, "updated_at": None}
    )


@router.get("/{team_id}/notebook", response_model=TeamNotebook)
def team_notebook(team_id: UUID, user: CurrentUser) -> TeamNotebook:
    with connect() as db:
        require_team(db, team_id, user)
        return notebook_view(db, team_id)


@router.put("/{team_id}/notebook", response_model=TeamNotebook)
def save_team_notebook(team_id: UUID, body: TeamNotebookWrite, user: CurrentUser) -> TeamNotebook:
    with connect() as db:
        require_team(db, team_id, user, lock=True)
        current = db.execute("SELECT * FROM team_notebooks WHERE team_id=%s", (team_id,)).fetchone()
        revision = current["revision"] if current else 0
        if revision != body.revision:
            if (
                current
                and revision == body.revision + 1
                and current["body"] == body.body
                and current["updated_by"] == user.user_id
            ):
                return notebook_view(db, team_id)
            raise HTTPException(409, "team_note_conflict")
        db.execute(
            "INSERT INTO team_notebooks(team_id,body,revision,updated_by) VALUES (%s,%s,%s,%s) ON CONFLICT(team_id) DO UPDATE SET body=excluded.body,revision=excluded.revision,updated_by=excluded.updated_by,updated_at=now()",
            (team_id, body.body, revision + 1, user.user_id),
        )
        event(
            db,
            user,
            None,
            "team.note.saved",
            {"team_id": str(team_id), "revision": revision + 1},
            submitted=True,
        )
        return notebook_view(db, team_id)


def finding_view(db: DB, row: dict[str, Any]) -> FindingView:
    username = db.execute("SELECT username FROM users WHERE id=%s", (row["actor_id"],)).fetchone()
    assert username
    return FindingView(
        id=row["id"],
        actor_id=row["actor_id"],
        username=username["username"],
        body=row["body"],
        source=row["source"],
        created_at=row["created_at"],
    )


@router.get("/{team_id}/findings", response_model=FindingsPage)
def findings(team_id: UUID, user: CurrentUser, before: UUID | None = None) -> FindingsPage:
    with connect() as db:
        require_team(db, team_id, user)
        cursor = None
        if before is not None:
            cursor = db.execute(
                "SELECT created_at,id FROM team_findings WHERE id=%s AND team_id=%s",
                (before, team_id),
            ).fetchone()
            if not cursor:
                raise HTTPException(404, "finding_not_found")
        rows = db.execute(
            "SELECT * FROM team_findings WHERE team_id=%s"
            + (" AND (created_at,id)<(%s,%s)" if cursor else "")
            + " ORDER BY created_at DESC,id DESC LIMIT 51",
            (team_id, cursor["created_at"], cursor["id"]) if cursor else (team_id,),
        ).fetchall()
        return FindingsPage(
            items=[finding_view(db, row) for row in rows[:50]],
            next_cursor=rows[49]["id"] if len(rows) > 50 else None,
        )


@router.post("/{team_id}/findings", response_model=FindingView, status_code=201)
def add_finding(team_id: UUID, body: FindingCreate, user: CurrentUser) -> FindingView:
    with connect() as db:
        require_team(db, team_id, user, lock=True)
        old = db.execute(
            "SELECT * FROM team_findings WHERE team_id=%s AND actor_id=%s AND request_key=%s",
            (team_id, user.user_id, body.request_key),
        ).fetchone()
        if old:
            if old["body"] != body.body or old["source"] != body.source:
                raise HTTPException(409, "idempotency_conflict")
            return finding_view(db, old)
        count = db.execute(
            "SELECT count(*) AS n FROM team_findings WHERE actor_id=%s AND created_at>now()-interval '1 minute'",
            (user.user_id,),
        ).fetchone()
        if count and count["n"] >= 20:
            raise HTTPException(429, "finding_limit")
        row = db.execute(
            "INSERT INTO team_findings(id,team_id,actor_id,request_key,body,source) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (uuid4(), team_id, user.user_id, body.request_key, body.body, body.source),
        ).fetchone()
        assert row
        event(
            db,
            user,
            None,
            "team.finding.submitted",
            {"team_id": str(team_id), "finding_id": str(row["id"])},
            submitted=True,
        )
        return finding_view(db, row)


@router.get("/{team_id}", response_model=TeamView)
def get_team(team_id: UUID, user: CurrentUser) -> TeamView:
    with connect() as db:
        return team_view(db, require_team(db, team_id, user))
