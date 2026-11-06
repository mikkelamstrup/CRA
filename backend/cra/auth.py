"""Local identity. No browser-provided organization or role is trusted."""

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .db import connect

router = APIRouter(prefix="/api/v1")
PASSWORDS = PasswordHasher()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))
COOKIE = "cra_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf(token: str) -> str:
    return digest("cra-csrf-v1:" + token)


def origins() -> set[str]:
    return set(
        os.environ.get("CRA_ORIGINS", "http://127.0.0.1:28000,http://localhost:28000").split(",")
    )


def check_origin(request: Request) -> None:
    if request.headers.get("origin") not in origins():
        raise HTTPException(403, "origin_denied")


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    organization_id: UUID
    username: str
    role: str
    session_hash: str


def identity(request: Request) -> Identity:
    token = request.cookies.get(COOKIE, "")
    if not 32 <= len(token) <= 128:
        raise HTTPException(401, "login_required")
    with connect() as db:
        row = db.execute(
            "SELECT u.id, u.username, s.organization_id, m.role FROM auth_sessions s "
            "JOIN users u ON u.id=s.user_id JOIN memberships m ON m.user_id=u.id "
            "AND m.organization_id=s.organization_id WHERE s.token_hash=%s "
            "AND s.revoked_at IS NULL AND s.expires_at>now() AND u.active",
            (digest(token),),
        ).fetchone()
    if not row:
        raise HTTPException(401, "login_required")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
        if not hmac.compare_digest(request.headers.get("x-cra-csrf", ""), csrf(token)):
            raise HTTPException(403, "csrf_denied")
    return Identity(row["id"], row["organization_id"], row["username"], row["role"], digest(token))


CurrentUser = Annotated[Identity, Depends(identity)]


def require_scope(user: Identity, organization_id: UUID, teacher: bool = False) -> None:
    if user.organization_id != organization_id:
        raise HTTPException(404, "not_found")
    if teacher and user.role != "teacher":
        raise HTTPException(403, "role_denied")


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    password: str = Field(min_length=1, max_length=256)


class UserView(BaseModel):
    user_id: UUID
    organization_id: UUID
    username: str
    role: Literal["student", "teacher"]
    csrf_token: str


def throttle(request: Request, username: str) -> None:
    # Atomic counters are durable across API restarts and concurrent requests.
    # Commit counters even when a later password check fails.
    # The trusted edge deliberately does not forward browser-supplied client IPs.
    # A classroom shares one proxy bucket; retain the tighter per-account limit.
    keys = [
        ("account:" + username, 8),
        ("client:" + (request.client.host if request.client else "unknown"), 60),
    ]
    exceeded = False
    with connect() as db:
        db.execute("DELETE FROM login_limits WHERE window_start < now()-interval '2 minutes'")
        for key, limit in sorted(keys):
            row = db.execute(
                "INSERT INTO login_limits VALUES (%s, now(), 1) ON CONFLICT(key) DO UPDATE SET "
                "attempts=CASE WHEN login_limits.window_start < now()-interval '1 minute' "
                "THEN 1 ELSE login_limits.attempts+1 END, "
                "window_start=CASE WHEN login_limits.window_start < now()-interval '1 minute' "
                "THEN now() ELSE login_limits.window_start END RETURNING attempts",
                (digest(key),),
            ).fetchone()
            exceeded = exceeded or bool(row and row["attempts"] > limit)
    if exceeded:
        raise HTTPException(429, "try_later", headers={"Retry-After": "60"})


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response) -> dict[str, bool]:
    check_origin(request)
    throttle(request, body.username)
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE username=%s", (body.username,)).fetchone()
        try:
            PASSWORDS.verify(row["password_hash"] if row else DUMMY_HASH, body.password)
        except VerificationError:
            raise HTTPException(401, "invalid_credentials") from None
        if not row or not row["active"]:
            raise HTTPException(401, "invalid_credentials")
        membership = db.execute(
            "SELECT organization_id FROM memberships WHERE user_id=%s ORDER BY organization_id LIMIT 1",
            (row["id"],),
        ).fetchone()
        if not membership:
            raise HTTPException(401, "invalid_credentials")
        if PASSWORDS.check_needs_rehash(row["password_hash"]):
            db.execute(
                "UPDATE users SET password_hash=%s WHERE id=%s",
                (PASSWORDS.hash(body.password), row["id"]),
            )
        old = request.cookies.get(COOKIE)
        if old:
            db.execute(
                "UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=%s", (digest(old),)
            )
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM auth_sessions WHERE expires_at < now()-interval '1 day'")
        db.execute(
            "INSERT INTO auth_sessions(token_hash,user_id,organization_id,expires_at) "
            "VALUES (%s,%s,%s,now()+interval '8 hours')",
            (digest(token), row["id"], membership["organization_id"]),
        )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        max_age=28800,
        secure=all(x.startswith("https://") for x in origins()),
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True}


@router.get("/auth/me", response_model=UserView)
def me(request: Request, response: Response, user: CurrentUser) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return {
        "user_id": user.user_id,
        "organization_id": user.organization_id,
        "username": user.username,
        "role": user.role,
        "csrf_token": csrf(request.cookies[COOKIE]),
    }


@router.post("/auth/logout")
def logout(response: Response, user: CurrentUser) -> dict[str, bool]:
    with connect() as db:
        db.execute(
            "UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=%s", (user.session_hash,)
        )
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False}


@router.get("/organizations/{organization_id}/members")
def members(organization_id: UUID, user: CurrentUser) -> list[dict[str, object]]:
    require_scope(user, organization_id, teacher=True)
    with connect() as db:
        return list(
            db.execute(
                "SELECT u.id, u.username, m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=%s ORDER BY u.username",
                (user.organization_id,),
            ).fetchall()
        )
