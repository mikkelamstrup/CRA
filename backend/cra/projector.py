"""Read-only, explicitly selected projection with independent revocable access."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, model_validator

from .auth import CurrentUser, check_origin, digest, origins, require_scope
from .contracts import Contract, LabState, missions
from .db import connect
from .flow_contracts import CaptureStatus
from .freshness import fresh
from .jobs import DB
from .scoring import earned_rows

router = APIRouter(prefix="/api/v1")
COOKIE = "cra_projector"
COOKIE_PATH = "/api/v1/projector"


class ProjectorCreate(Contract):
    lab_ids: list[UUID] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_labs(self) -> "ProjectorCreate":
        if len(set(self.lab_ids)) != len(self.lab_ids):
            raise ValueError("Duplicate selected labs")
        return self


class ProjectorGrant(Contract):
    id: UUID
    created_at: datetime
    expires_at: datetime
    exchanged: bool
    revoked: bool
    lab_count: int


class ProjectorLaunch(Contract):
    grant: ProjectorGrant
    token: str


class ProjectorExchange(Contract):
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


class ProjectedLab(Contract):
    ordinal: int
    alias: str
    state: LabState
    stale: bool
    generation: int
    completed: int
    objectives: int
    points: int
    activity: Literal["observed", "quiet", "unconfirmed"]
    observation_incomplete: bool


class ProjectorFeed(Contract):
    schema_version: Literal[1] = 1
    server_time: datetime
    expires_at: datetime
    day: int | None
    phase: Literal["theory", "solo", "team", "debrief"] | None
    labs: list[ProjectedLab]


def grant_view(row: dict[str, Any]) -> ProjectorGrant:
    return ProjectorGrant(
        id=row["id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        exchanged=row["exchanged_at"] is not None,
        revoked=row["revoked_at"] is not None,
        lab_count=row["lab_count"],
    )


@router.get("/command-center/projectors", response_model=list[ProjectorGrant])
def grants(user: CurrentUser) -> list[ProjectorGrant]:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        rows = db.execute(
            "SELECT g.*,(SELECT count(*) FROM projector_labs p WHERE p.grant_id=g.id) AS lab_count FROM projector_grants g WHERE organization_id=%s AND expires_at>clock_timestamp() ORDER BY (revoked_at IS NULL) DESC,created_at DESC LIMIT 25",
            (user.organization_id,),
        ).fetchall()
        return [grant_view(row) for row in rows]


@router.post("/command-center/projectors", response_model=ProjectorLaunch, status_code=201)
def create(body: ProjectorCreate, user: CurrentUser) -> ProjectorLaunch:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,603))", (str(user.organization_id),)
        )
        db.execute(
            "DELETE FROM projector_grants WHERE expires_at<clock_timestamp()-interval '1 day'"
        )
        count = db.execute(
            "SELECT count(*) AS n FROM projector_grants WHERE organization_id=%s AND expires_at>clock_timestamp() AND revoked_at IS NULL",
            (user.organization_id,),
        ).fetchone()
        if count and count["n"] >= 5:
            raise HTTPException(409, "projector_limit")
        rows = db.execute(
            "SELECT id FROM labs WHERE id=ANY(%s) AND organization_id=%s AND state<>'DESTROYED' FOR SHARE",
            (body.lab_ids, user.organization_id),
        ).fetchall()
        if {row["id"] for row in rows} != set(body.lab_ids):
            raise HTTPException(404, "not_found")
        token = secrets.token_urlsafe(32)
        row = db.execute(
            "INSERT INTO projector_grants(id,organization_id,issuer_id,exchange_hash,exchange_until,expires_at) VALUES (%s,%s,%s,%s,clock_timestamp()+interval '5 minutes',clock_timestamp()+interval '8 hours') RETURNING *",
            (uuid4(), user.organization_id, user.user_id, digest(token)),
        ).fetchone()
        assert row
        for ordinal, lab in enumerate(body.lab_ids, 1):
            db.execute("INSERT INTO projector_labs VALUES (%s,%s,%s)", (row["id"], ordinal, lab))
        return ProjectorLaunch(
            grant=grant_view({**row, "lab_count": len(body.lab_ids)}), token=token
        )


@router.post("/command-center/projectors/{grant_id}/revoke")
def revoke(grant_id: UUID, user: CurrentUser) -> dict[str, bool]:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        row = db.execute(
            "UPDATE projector_grants SET revoked_at=coalesce(revoked_at,clock_timestamp()) WHERE id=%s AND organization_id=%s RETURNING id",
            (grant_id, user.organization_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "not_found")
    return {"revoked": True}


def authorized(db: DB, grant_id: UUID, token: str, *, exchange: bool) -> dict[str, Any]:
    if len(token) != 43:
        raise HTTPException(401, "projector_access_required")
    # Lock serializes exchange and revocation. Recheck wall time after waiting.
    column = "exchange_hash" if exchange else "session_hash"
    row = db.execute(
        "SELECT * FROM projector_grants WHERE " + column + "=%s AND id=%s FOR UPDATE",
        (digest(token), grant_id),
    ).fetchone()
    now = db.execute("SELECT clock_timestamp() AS now").fetchone()
    assert now
    if (
        not row
        or row["revoked_at"]
        or row["expires_at"] <= now["now"]
        or (exchange and (row["exchanged_at"] or row["exchange_until"] <= now["now"]))
    ):
        raise HTTPException(401, "projector_access_required")
    issuer = db.execute(
        "SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.id=%s AND u.active AND m.organization_id=%s AND m.role='teacher'",
        (row["issuer_id"], row["organization_id"]),
    ).fetchone()
    if not issuer:
        raise HTTPException(401, "projector_access_required")
    return row


@router.post("/projector/{grant_id}/exchange")
def exchange(
    grant_id: UUID, body: ProjectorExchange, request: Request, response: Response
) -> dict[str, bool]:
    check_origin(request)
    with connect() as db:
        row = authorized(db, grant_id, body.token, exchange=True)
        token = secrets.token_urlsafe(32)
        db.execute(
            "UPDATE projector_grants SET session_hash=%s,exchanged_at=clock_timestamp() WHERE id=%s",
            (digest(token), row["id"]),
        )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        max_age=28800,
        secure=all(o.startswith("https://") for o in origins()),
        path=COOKIE_PATH + "/" + str(grant_id),
    )
    return {"connected": True}


@router.get("/projector/{grant_id}/feed", response_model=ProjectorFeed)
def feed(grant_id: UUID, request: Request) -> ProjectorFeed:
    definitions = {(m.id, m.version): m for m in missions()}
    with connect() as db:
        grant = authorized(db, grant_id, request.cookies.get(COOKIE, ""), exchange=False)
        now = datetime.now(UTC)
        session = db.execute(
            "SELECT day,phase FROM teaching_sessions WHERE organization_id=%s AND NOT closed",
            (grant["organization_id"],),
        ).fetchone()
        labs = db.execute(
            "SELECT l.*,p.ordinal FROM projector_labs p JOIN labs l ON l.id=p.lab_id WHERE p.grant_id=%s AND l.organization_id=%s AND l.state<>'DESTROYED' ORDER BY p.ordinal",
            (grant["id"], grant["organization_id"]),
        ).fetchall()
        result = []
        for lab in labs:
            completed = db.execute(
                "SELECT count(*) AS n FROM lab_completions WHERE lab_id=%s AND generation=%s",
                (lab["id"], lab["generation"]),
            ).fetchone()
            snapshot = db.execute(
                "SELECT * FROM lab_capture_snapshots WHERE lab_id=%s AND generation=%s",
                (lab["id"], lab["generation"]),
            ).fetchone()
            captures = (
                [CaptureStatus.model_validate(item) for item in snapshot["captures"]]
                if snapshot
                else []
            )
            confirmed = bool(
                fresh(lab)
                and lab["state"] == "READY"
                and snapshot
                and now - snapshot["received_at"] <= timedelta(seconds=20)
                and captures
                and all(c.connected for c in captures)
            )
            # Only existence of recent numeric observations is exposed; no raw
            # headers, operator IDs, ports or hidden topology enter the DTO.
            recent = (
                db.execute(
                    "SELECT 1 FROM flow_history WHERE lab_id=%s AND generation=%s AND epoch=ANY(%s) AND observed_at>%s AND jsonb_array_length(payload->'flows')>0 LIMIT 1",
                    (
                        lab["id"],
                        lab["generation"],
                        [c.epoch for c in captures],
                        now - timedelta(seconds=10),
                    ),
                ).fetchone()
                if confirmed
                else None
            )
            result.append(
                ProjectedLab(
                    ordinal=lab["ordinal"],
                    alias=f"Hold {lab['ordinal']:02}",
                    state=lab["state"],
                    stale=not fresh(lab),
                    generation=lab["generation"],
                    completed=completed["n"] if completed else 0,
                    objectives=len(
                        definitions[(lab["mission_id"], lab["mission_version"])].objectives
                    ),
                    points=sum(r["points"] for r in earned_rows(db, lab)),
                    activity="observed" if recent else "quiet" if confirmed else "unconfirmed",
                    observation_incomplete=any(
                        c.metadata_dropped or c.partial_windows or c.poll_missed_windows
                        for c in captures
                    ),
                )
            )
        return ProjectorFeed(
            server_time=now,
            expires_at=grant["expires_at"],
            day=session["day"] if session else None,
            phase=session["phase"] if session else None,
            labs=result,
        )
