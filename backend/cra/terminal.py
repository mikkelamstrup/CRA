"""Short-lived grants. Internal redemption remains session- and CSRF-authenticated."""

import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field

from .auth import CurrentUser, digest
from .compute_nodes import node
from .contracts import Contract
from .db import connect
from .freshness import fresh
from .lab_access import accessible_lab
from .teaching import require_terminal_enabled

router = APIRouter()


class Redeem(Contract):
    grant: str = Field(min_length=40, max_length=128)
    lab_id: UUID
    purpose: Literal["terminal", "desktop"] = "terminal"


def signing_key(node_id: int = 1) -> bytes:
    return node(node_id).signing_key().read_bytes()


@router.post("/api/v1/labs/{lab_id}/terminal-grants", status_code=201)
def grant(lab_id: UUID, user: CurrentUser) -> dict[str, str | int]:
    return issue_grant(lab_id, user, "terminal")


@router.post("/api/v1/labs/{lab_id}/desktop-grants", status_code=201)
def desktop_grant(lab_id: UUID, user: CurrentUser) -> dict[str, str | int]:
    return issue_grant(lab_id, user, "desktop")


def issue_grant(
    lab_id: UUID, user: CurrentUser, purpose: Literal["terminal", "desktop"]
) -> dict[str, str | int]:
    with connect() as db:
        require_terminal_enabled(db, user)
        lab = accessible_lab(db, lab_id, user, lock=True, operator=True)
        if purpose == "desktop" and not lab["desktop_enabled"]:
            raise HTTPException(409, "desktop_unavailable")
        if lab["state"] != "READY" or not fresh(lab):
            raise HTTPException(409, "lab_not_ready")
        db.execute("DELETE FROM terminal_grants WHERE expires_at<now()-interval '1 day'")
        db.execute(
            "UPDATE terminal_grants SET closed_at=now() WHERE lab_id=%s AND user_id=%s AND purpose=%s AND closed_at IS NULL",
            (lab_id, user.user_id, purpose),
        )
        token = secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO terminal_grants(token_hash,session_hash,lab_id,organization_id,user_id,generation,purpose,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,now()+interval '30 seconds')",
            (
                digest(token),
                user.session_hash,
                lab_id,
                user.organization_id,
                user.user_id,
                lab["generation"],
                purpose,
            ),
        )
    return {"grant": token, "expires_in": 30}


def authorized_grant(body: Redeem, user: CurrentUser, *, consume: bool) -> dict[str, Any]:
    with connect() as db:
        require_terminal_enabled(db, user)
        row = db.execute(
            "SELECT g.*,l.node_id,l.state,l.desktop_enabled,l.last_observed_at,l.generation AS current_generation,l.user_id AS owner_id FROM terminal_grants g "
            "JOIN labs l ON l.id=g.lab_id WHERE g.token_hash=%s AND g.lab_id=%s "
            "AND g.session_hash=%s AND g.organization_id=%s AND g.user_id=%s "
            "AND l.organization_id=g.organization_id AND (l.team_id IS NULL AND l.user_id=g.user_id OR EXISTS(SELECT 1 FROM lab_participants p JOIN team_members m ON m.user_id=p.user_id AND m.team_id=l.team_id WHERE p.lab_id=l.id AND p.user_id=g.user_id AND m.organization_id=l.organization_id)) FOR UPDATE OF g",
            (
                digest(body.grant),
                body.lab_id,
                user.session_hash,
                user.organization_id,
                user.user_id,
            ),
        ).fetchone()
        if (
            not row
            or row["purpose"] != body.purpose
            or (body.purpose == "desktop" and not row["desktop_enabled"])
            or row["closed_at"] is not None
            or row["state"] != "READY"
            or not fresh(row)
            or row["generation"] != row["current_generation"]
        ):
            raise HTTPException(403, "terminal_denied")
        now = db.execute("SELECT clock_timestamp() AS current").fetchone()
        assert now
        if consume:
            if row["consumed_at"] is not None or row["expires_at"] <= now["current"]:
                raise HTTPException(403, "terminal_denied")
            db.execute(
                "UPDATE terminal_grants SET consumed_at=clock_timestamp() WHERE token_hash=%s",
                (row["token_hash"],),
            )
        elif row["consumed_at"] is None:
            raise HTTPException(403, "terminal_denied")
        return row


@router.post("/internal/terminal/consume")
def consume(body: Redeem, user: CurrentUser) -> dict[str, Any]:
    row = authorized_grant(body, user, consume=True)
    claims = {
        "schema_version": 1,
        "node_id": row["node_id"],
        "purpose": row["purpose"],
        "lab_id": str(row["lab_id"]),
        "organization_id": str(row["organization_id"]),
        "user_id": str(row["owner_id"]),
        "operator_user_id": str(row["user_id"]),
        "generation": row["generation"],
        "grant_hash": row["token_hash"],
        "expires_at": int(time.time()) + 15,
    }
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":"))
    return {
        "payload": payload,
        "signature": hmac.new(
            signing_key(row["node_id"]), payload.encode(), hashlib.sha256
        ).hexdigest(),
    }


@router.post("/internal/terminal/check")
def check(body: Redeem, user: CurrentUser) -> dict[str, bool]:
    authorized_grant(body, user, consume=False)
    return {"authorized": True}
