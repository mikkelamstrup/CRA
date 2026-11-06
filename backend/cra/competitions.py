"""Fixed classroom CTF. Clock, immutable scoring and teacher actions serialize per contest."""

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import Field, model_validator

from .auth import CurrentUser, Identity, require_scope
from .contracts import Contract
from .db import connect
from .freshness import fresh
from .jobs import DB
from .lab_access import accessible_lab
from .teams import require_team

router = APIRouter(prefix="/api/v1/competitions")
# Versioned fixed rulebook, independent of practice points. A new competition code version
# must retain this v1 mapping for existing competitions.
CHALLENGES = (
    ("first-files", "find-evidence", "Linux · Filsporet", 100),
    ("first-contact", "find-service", "Netværk · Servicen", 150),
    ("vulnerable-app", "find-authorization-flaw", "Web · Adgangsfejlen", 200),
    ("packet-hunter", "find-captured-report", "PCAP · Den historiske rapport", 250),
    ("operation-shadow", "complete-investigation", "Finale · Operation Shadow", 400),
)


class Challenge(Contract):
    mission_id: str
    mission_version: Literal[1] = 1
    objective_id: str
    title: str
    points: int


class Standing(Contract):
    rank: int
    alias: str
    points: int
    solved: int


class CompetitionTeam(Contract):
    team_id: UUID
    alias: str
    completed_challenges: list[str]
    points: int


class CompetitionView(Contract):
    id: UUID
    title: str
    status: Literal["draft", "running", "paused", "ended"]
    revision: int
    server_time: datetime
    deadline: datetime | None
    remaining_seconds: float
    frozen: bool
    challenges: list[Challenge]
    standings: list[Standing]
    own_team_ids: list[UUID]
    visible_teams: list[CompetitionTeam]
    hint_cost: Literal[0] = 0


class CompetitionCreate(Contract):
    request_key: UUID
    title: str = Field(min_length=1, max_length=80)
    duration_seconds: int = Field(ge=60, le=28800, strict=True)
    team_ids: list[UUID] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_teams(self) -> "CompetitionCreate":
        if len(set(self.team_ids)) != len(self.team_ids):
            raise ValueError("Duplicate team")
        return self


class CompetitionBoard(Contract):
    title: str
    status: str
    server_time: datetime
    remaining_seconds: float
    frozen: bool
    standings: list[Standing]


@router.get("/{competition_id}/board", response_model=CompetitionBoard)
def board(competition_id: UUID, user: CurrentUser) -> CompetitionBoard:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM competitions WHERE id=%s AND organization_id=%s",
            (competition_id, user.organization_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "competition_not_found")
        current = view(db, dict(row), user)
        return CompetitionBoard(
            title=current.title,
            status=current.status,
            server_time=current.server_time,
            remaining_seconds=current.remaining_seconds,
            frozen=current.frozen,
            standings=current.standings,
        )


class CompetitionAction(Contract):
    revision: int = Field(ge=1, strict=True)
    action: Literal["start", "pause", "resume", "freeze", "unfreeze", "end"]


class CompetitionAttempt(Contract):
    request_key: UUID
    lab_id: UUID
    generation: int = Field(ge=1, le=10000, strict=True)
    flag: str = Field(min_length=1, max_length=256)


class CompetitionResult(Contract):
    accepted: bool
    points_awarded: int
    already_completed: bool


def now(db: DB) -> datetime:
    row = db.execute("SELECT clock_timestamp() AS time").fetchone()
    assert row
    return row["time"]  # type: ignore[no-any-return]


def locked(db: DB, identity: UUID, user: Identity) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM competitions WHERE id=%s AND organization_id=%s FOR UPDATE",
        (identity, user.organization_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "competition_not_found")
    return dict(row)


def view(db: DB, row: dict[str, Any], user: Identity) -> CompetitionView:
    time = now(db)
    remaining = (
        max(0.0, (row["deadline"] - time).total_seconds())
        if row["status"] == "running"
        else row["remaining_seconds"]
    )
    # No cache or mutable score totals. Ties share rank; aliases are generated, never names.
    rows = db.execute(
        "SELECT t.ordinal,coalesce(sum(s.points),0)::integer AS points,count(s.id)::integer AS solved FROM competition_teams t LEFT JOIN competition_scores s ON s.competition_id=t.competition_id AND s.team_id=t.team_id AND (%s::timestamptz IS NULL OR s.created_at<=%s) WHERE t.competition_id=%s GROUP BY t.ordinal ORDER BY points DESC,t.ordinal",
        (row["frozen_at"], row["frozen_at"], row["id"]),
    ).fetchall()
    standings = []
    rank, previous = 0, None
    for index, item in enumerate(rows, 1):
        if item["points"] != previous:
            rank, previous = index, item["points"]
        standings.append(
            Standing(
                rank=rank,
                alias=f"Hold {item['ordinal']:02d}",
                points=item["points"],
                solved=item["solved"],
            )
        )
    own = db.execute(
        "SELECT t.team_id FROM competition_teams t WHERE t.competition_id=%s AND EXISTS(SELECT 1 FROM team_members m WHERE m.team_id=t.team_id AND m.user_id=%s) ORDER BY t.ordinal",
        (row["id"], user.user_id),
    ).fetchall()
    visible = []
    for team in db.execute(
        "SELECT t.team_id,t.ordinal FROM competition_teams t WHERE t.competition_id=%s AND (%s OR EXISTS(SELECT 1 FROM team_members m WHERE m.team_id=t.team_id AND m.user_id=%s)) ORDER BY t.ordinal",
        (row["id"], user.role == "teacher", user.user_id),
    ).fetchall():
        scores = db.execute(
            "SELECT challenge_id,points FROM competition_scores WHERE competition_id=%s AND team_id=%s ORDER BY challenge_id",
            (row["id"], team["team_id"]),
        ).fetchall()
        visible.append(
            CompetitionTeam(
                team_id=team["team_id"],
                alias=f"Hold {team['ordinal']:02d}",
                completed_challenges=[s["challenge_id"] for s in scores],
                points=sum(s["points"] for s in scores),
            )
        )
    return CompetitionView(
        id=row["id"],
        title=row["title"],
        status="ended" if row["status"] == "running" and remaining == 0 else row["status"],
        revision=row["revision"],
        server_time=time,
        deadline=row["deadline"],
        remaining_seconds=remaining,
        frozen=row["frozen_at"] is not None,
        challenges=[
            Challenge(mission_id=m, objective_id=o, title=t, points=p) for m, o, t, p in CHALLENGES
        ],
        standings=standings,
        own_team_ids=[r["team_id"] for r in own],
        visible_teams=visible,
    )


@router.get("", response_model=list[CompetitionView])
def list_competitions(user: CurrentUser) -> list[CompetitionView]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM competitions WHERE organization_id=%s ORDER BY created_at DESC LIMIT 20",
            (user.organization_id,),
        ).fetchall()
        return [view(db, dict(row), user) for row in rows]


@router.post("", response_model=CompetitionView, status_code=201)
def create(body: CompetitionCreate, user: CurrentUser) -> CompetitionView:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,703))", (str(user.organization_id),)
        )
        old = db.execute(
            "SELECT * FROM competitions WHERE organization_id=%s AND request_key=%s",
            (user.organization_id, body.request_key),
        ).fetchone()
        if old:
            assigned = db.execute(
                "SELECT team_id FROM competition_teams WHERE competition_id=%s ORDER BY ordinal",
                (old["id"],),
            ).fetchall()
            if (
                old["title"] != body.title
                or old["duration_seconds"] != body.duration_seconds
                or [r["team_id"] for r in assigned] != body.team_ids
            ):
                raise HTTPException(409, "idempotency_conflict")
            return view(db, dict(old), user)
        # Expiration is resolved under the same row locks as submissions/actions.
        open_rows = db.execute(
            "SELECT * FROM competitions WHERE organization_id=%s AND status<>'ended' FOR UPDATE",
            (user.organization_id,),
        ).fetchall()
        for opened in open_rows:
            if opened["status"] == "running" and opened["deadline"] <= now(db):
                db.execute(
                    "UPDATE competitions SET status='ended',remaining_seconds=0,revision=revision+1 WHERE id=%s",
                    (opened["id"],),
                )
            else:
                raise HTTPException(409, "competition_already_open")
        members: set[UUID] = set()
        for tid in body.team_ids:
            require_team(db, tid, user, lock=True)
            rows = db.execute(
                "SELECT m.user_id,u.active FROM team_members m JOIN users u ON u.id=m.user_id WHERE m.team_id=%s FOR SHARE OF u",
                (tid,),
            ).fetchall()
            if not rows or any(not r["active"] for r in rows):
                raise HTTPException(409, "competition_inactive_member")
            ids = {r["user_id"] for r in rows}
            if members & ids:
                raise HTTPException(409, "competition_duplicate_member")
            members |= ids
        row = db.execute(
            "INSERT INTO competitions(id,organization_id,created_by,request_key,title,duration_seconds,remaining_seconds) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                uuid4(),
                user.organization_id,
                user.user_id,
                body.request_key,
                body.title,
                body.duration_seconds,
                body.duration_seconds,
            ),
        ).fetchone()
        assert row
        for ordinal, tid in enumerate(body.team_ids, 1):
            db.execute(
                "INSERT INTO competition_teams VALUES (%s,%s,%s,%s)",
                (row["id"], user.organization_id, tid, ordinal),
            )
        return view(db, dict(row), user)


@router.post("/{competition_id}/actions", response_model=CompetitionView)
def action(competition_id: UUID, body: CompetitionAction, user: CurrentUser) -> CompetitionView:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        row = locked(db, competition_id, user)
        time = now(db)
        if body.revision != row["revision"]:
            raise HTTPException(409, "competition_changed")
        remaining = (
            max(0.0, (row["deadline"] - time).total_seconds())
            if row["status"] == "running"
            else row["remaining_seconds"]
        )
        state = row["status"]
        if state == "running" and remaining == 0:
            state = "ended"
        if body.action == "start" and state == "draft":
            row.update(
                status="running", started_at=time, deadline=time + timedelta(seconds=remaining)
            )
        elif body.action == "pause" and state == "running":
            row.update(status="paused", remaining_seconds=remaining, deadline=None)
        elif body.action == "resume" and state == "paused":
            row.update(status="running", deadline=time + timedelta(seconds=remaining))
        elif body.action == "end" and state in ("draft", "running", "paused", "ended"):
            row.update(status="ended", remaining_seconds=0, deadline=None)
        elif body.action in ("freeze", "unfreeze") and state in ("running", "paused", "ended"):
            row["frozen_at"] = (row["frozen_at"] or time) if body.action == "freeze" else None
        else:
            raise HTTPException(409, "competition_action_invalid")
        updated = db.execute(
            "UPDATE competitions SET status=%s,started_at=%s,deadline=%s,remaining_seconds=%s,frozen_at=%s,revision=revision+1 WHERE id=%s RETURNING *",
            (
                row["status"],
                row["started_at"],
                row["deadline"],
                row["remaining_seconds"],
                row["frozen_at"],
                competition_id,
            ),
        ).fetchone()
        assert updated
        return view(db, dict(updated), user)


@router.post("/{competition_id}/attempts", response_model=CompetitionResult)
def attempt(competition_id: UUID, body: CompetitionAttempt, user: CurrentUser) -> CompetitionResult:
    fingerprint = hashlib.sha256(
        (str(body.lab_id) + "\0" + str(body.generation) + "\0" + body.flag).encode()
    ).hexdigest()
    with connect() as db:
        row = locked(db, competition_id, user)
        # Lock lab before wall-clock check: time spent waiting cannot extend the deadline.
        lab = accessible_lab(db, body.lab_id, user, lock=True, operator=True)
        if lab["generation"] != body.generation:
            raise HTTPException(409, "generation_changed")
        if not db.execute(
            "SELECT 1 FROM competition_teams t JOIN team_members m ON m.team_id=t.team_id WHERE t.competition_id=%s AND t.team_id=%s AND m.user_id=%s",
            (competition_id, lab["team_id"], user.user_id),
        ).fetchone():
            raise HTTPException(404, "competition_team_required")
        old = db.execute(
            "SELECT fingerprint,result FROM competition_attempts WHERE competition_id=%s AND actor_id=%s AND request_key=%s",
            (competition_id, user.user_id, body.request_key),
        ).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise HTTPException(409, "idempotency_conflict")
            return CompetitionResult.model_validate(old["result"])
        time = now(db)
        if row["status"] != "running" or row["deadline"] <= time:
            raise HTTPException(409, "competition_not_running")
        if lab["created_at"] < row["started_at"]:
            raise HTTPException(409, "competition_fresh_lab_required")
        if lab["state"] != "READY" or not fresh(lab):
            raise HTTPException(409, "lab_not_ready")
        challenge = next(
            (c for c in CHALLENGES if c[0] == lab["mission_id"] and lab["mission_version"] == 1),
            None,
        )
        if not challenge:
            raise HTTPException(409, "competition_challenge_required")
        recent = db.execute(
            "SELECT count(*) AS n FROM competition_attempts WHERE actor_id=%s AND created_at>clock_timestamp()-interval '1 minute'",
            (user.user_id,),
        ).fetchone()
        if recent and recent["n"] >= 20:
            raise HTTPException(429, "attempt_limit")
        verifier = db.execute(
            "SELECT verifier FROM lab_flags WHERE lab_id=%s AND generation=%s AND objective_id=%s",
            (body.lab_id, body.generation, challenge[1]),
        ).fetchone()
        if not verifier:
            raise HTTPException(503, "validator_unavailable")
        accepted = hmac.compare_digest(
            hashlib.sha256(body.flag.encode()).hexdigest(), verifier["verifier"]
        )
        earned = None
        if accepted:
            earned = db.execute(
                "INSERT INTO competition_scores(id,competition_id,team_id,challenge_id,lab_id,generation,actor_id,points) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING points",
                (
                    uuid4(),
                    competition_id,
                    lab["team_id"],
                    challenge[0],
                    body.lab_id,
                    body.generation,
                    user.user_id,
                    challenge[3],
                ),
            ).fetchone()
        result = CompetitionResult(
            accepted=accepted,
            points_awarded=earned["points"] if earned else 0,
            already_completed=accepted and earned is None,
        )
        db.execute(
            "INSERT INTO competition_attempts(id,competition_id,actor_id,request_key,fingerprint,result) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                competition_id,
                user.user_id,
                body.request_key,
                fingerprint,
                Jsonb(result.model_dump()),
            ),
        )
        return result
