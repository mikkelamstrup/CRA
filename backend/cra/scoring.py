"""Authoritative flag validation; append-only earned points survive practice resets."""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import Field

from .auth import CurrentUser
from .contracts import Contract, Event, ObjectivePayload
from .db import connect
from .freshness import fresh
from .jobs import DB, publish
from .lab_access import accessible_lab
from .labs import mission_by_id, request_key

router = APIRouter(prefix="/api/v1/labs")


class Attempt(Contract):
    generation: int = Field(ge=1, le=10000, strict=True)
    flag: str = Field(min_length=1, max_length=256)


class AttemptView(Contract):
    accepted: bool
    already_completed: bool
    points_awarded: int
    total_points: int
    evidence_level: Literal["validated", "none"]


class ProgressView(Contract):
    total_points: int
    completed_objectives: list[str]
    earned_objectives: list[str]


def earned_rows(db: DB, lab: dict[str, Any]) -> list[dict[str, Any]]:
    if lab["team_id"] is not None:
        return db.execute(
            "SELECT objective_id,points FROM team_score_ledger WHERE team_id=%s AND mission_id=%s AND mission_version=%s",
            (lab["team_id"], lab["mission_id"], lab["mission_version"]),
        ).fetchall()
    return db.execute(
        "SELECT objective_id,points FROM score_ledger WHERE organization_id=%s AND user_id=%s AND mission_id=%s AND mission_version=%s",
        (lab["organization_id"], lab["user_id"], lab["mission_id"], lab["mission_version"]),
    ).fetchall()


@router.get("/{lab_id}/progress", response_model=ProgressView)
def progress(lab_id: UUID, user: CurrentUser) -> ProgressView:
    with connect() as db:
        lab = accessible_lab(db, lab_id, user)
        rows = earned_rows(db, lab)
        completed = db.execute(
            "SELECT objective_id FROM lab_completions WHERE lab_id=%s AND generation=%s",
            (lab_id, lab["generation"]),
        ).fetchall()
        return ProgressView(
            total_points=sum(row["points"] for row in rows),
            completed_objectives=[row["objective_id"] for row in completed],
            earned_objectives=[row["objective_id"] for row in rows],
        )


@router.post("/{lab_id}/objectives/{objective_id}/attempts", response_model=AttemptView)
def attempt(
    lab_id: UUID, objective_id: str, body: Attempt, request: Request, user: CurrentUser
) -> AttemptView:
    key = request_key(request)
    fingerprint = hashlib.sha256(
        (str(body.generation) + "\0" + objective_id + "\0" + body.flag).encode()
    ).hexdigest()
    with connect() as db:
        lab = accessible_lab(db, lab_id, user, lock=True, operator=True)
        if lab["generation"] != body.generation:
            raise HTTPException(409, "generation_changed")
        old = db.execute(
            "SELECT fingerprint,result FROM objective_attempts WHERE lab_id=%s AND user_id=%s AND request_key=%s",
            (lab_id, user.user_id, key),
        ).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise HTTPException(409, "idempotency_conflict")
            return AttemptView.model_validate(old["result"])
        if lab["state"] != "READY" or not fresh(lab):
            raise HTTPException(409, "lab_not_ready")
        mission = mission_by_id(lab["mission_id"], lab["mission_version"])
        objective = next((item for item in mission.objectives if item.id == objective_id), None)
        if not objective:
            raise HTTPException(404, "objective_not_found")
        recent = db.execute(
            "SELECT count(*) AS n FROM objective_attempts WHERE user_id=%s AND created_at>now()-interval '1 minute'",
            (user.user_id,),
        ).fetchone()
        if recent and recent["n"] >= 20:
            raise HTTPException(429, "attempt_limit")
        verifier = db.execute(
            "SELECT verifier FROM lab_flags WHERE lab_id=%s AND generation=%s AND objective_id=%s",
            (lab_id, body.generation, objective_id),
        ).fetchone()
        if not verifier:
            raise HTTPException(503, "validator_unavailable")
        accepted = hmac.compare_digest(
            hashlib.sha256(body.flag.encode()).hexdigest(), verifier["verifier"]
        )
        points = 0
        already = False
        if accepted:
            first = db.execute(
                "INSERT INTO lab_completions(lab_id,generation,objective_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING lab_id",
                (lab_id, body.generation, objective_id),
            ).fetchone()
            already = first is None
            if lab["team_id"] is not None:
                earned = db.execute(
                    "INSERT INTO team_score_ledger(id,team_id,actor_id,mission_id,mission_version,objective_id,lab_id,generation,points) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING points",
                    (
                        uuid4(),
                        lab["team_id"],
                        user.user_id,
                        mission.id,
                        mission.version,
                        objective_id,
                        lab_id,
                        body.generation,
                        objective.points,
                    ),
                ).fetchone()
            else:
                earned = db.execute(
                    "INSERT INTO score_ledger(id,organization_id,user_id,mission_id,mission_version,objective_id,lab_id,generation,points) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING points",
                    (
                        uuid4(),
                        user.organization_id,
                        user.user_id,
                        mission.id,
                        mission.version,
                        objective_id,
                        lab_id,
                        body.generation,
                        objective.points,
                    ),
                ).fetchone()
            points = earned["points"] if earned else 0
            if first:
                publish(
                    db,
                    Event(
                        schema_version=1,
                        event_id=uuid4(),
                        organization_id=user.organization_id,
                        lab_id=lab_id,
                        generation=body.generation,
                        actor_id=user.user_id,
                        initiator_type="user",
                        occurred_at=datetime.now(UTC),
                        source="score",
                        evidence_level="validated",
                        payload=ObjectivePayload(
                            kind="objective.completed",
                            objective_id=objective_id,
                            points_delta=points,
                        ),
                    ),
                )
        total_points = sum(row["points"] for row in earned_rows(db, lab))
        result = AttemptView(
            accepted=accepted,
            already_completed=already,
            points_awarded=points,
            total_points=total_points,
            evidence_level="validated" if accepted else "none",
        )
        db.execute(
            "INSERT INTO objective_attempts(id,lab_id,generation,objective_id,user_id,request_key,fingerprint,result) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                lab_id,
                body.generation,
                objective_id,
                user.user_id,
                key,
                fingerprint,
                Jsonb(result.model_dump()),
            ),
        )
        return result
