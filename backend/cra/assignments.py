"""Teacher-private student aliases and additive individual mission releases."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator

from .auth import CurrentUser, require_scope
from .contracts import Contract
from .db import connect
from .mission_access import ReleaseSource, catalog, release_source
from .teaching import event

router = APIRouter(prefix="/api/v1")

INCIDENT_ID = "cybercon-incident"
INCIDENT_MESSAGE = (
    "Du er sikkerhedsanalytiker hos Cybercon. Vores store kunde Nordtele, en landsdækkende "
    "telekoncern med millioner af kunder, er under angreb. Der er tegn på, at uvedkommende "
    "henter kunderapporter fra deres serviceportal. Angrebet er stadig i gang.\n\n"
    "Du har fået ansvaret for sagen: Begræns angrebet, bevar sporene, undersøg hvad der er "
    "gået galt, og luk sikkerhedshullerne. Kontrollér, at kunderne bagefter stadig kan "
    "bruge deres egne rapporter. Aflever din hændelsesrapport til Cybercons indsatsleder.\n\n"
    "Du arbejder individuelt i dit eget kundemiljø. Åbn sagen for at læse mandatet og "
    "modtage din adgang. Nordtele og hændelsen er fiktive; dette er en CRA-øvelse."
)


class AssignmentAlert(Contract):
    mission_id: str
    mission_version: int
    released_at: datetime
    title: str
    message: str
    guidance_id: UUID | None = None


class GuidanceRelease(Contract):
    request_key: UUID
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=8000)


def guidance(db: Any, user: Any, guidance_id: UUID | None = None) -> Any:
    """Only the exact running solo lab selected at publication may receive this help."""
    return db.execute(
        "SELECT e.event_id,e.occurred_at,e.payload FROM teaching_events e "
        "JOIN labs l ON l.id::text=e.payload->>'lab_id' "
        "AND l.generation::text=e.payload->>'generation' "
        "WHERE e.organization_id=%s AND e.kind='assignment.guidance.released' "
        "AND e.payload->>'student_id'=%s AND l.organization_id=e.organization_id "
        "AND l.user_id=%s AND l.team_id IS NULL AND l.mission_id=%s "
        "AND l.mission_version=1 AND l.state='READY' AND l.desired_state='READY' "
        "AND (%s::uuid IS NULL OR e.event_id=%s) "
        "AND (%s::uuid IS NOT NULL OR NOT EXISTS (SELECT 1 FROM teaching_events a "
        "WHERE a.organization_id=e.organization_id AND a.actor_id=%s "
        "AND a.kind='assignment.guidance.acknowledged' "
        "AND a.payload->>'guidance_id'=e.event_id::text)) ORDER BY e.sequence LIMIT 1",
        (
            user.organization_id,
            str(user.user_id),
            user.user_id,
            INCIDENT_ID,
            guidance_id,
            guidance_id,
            guidance_id,
            user.user_id,
        ),
    ).fetchone()


@router.post("/command-center/assignments/cybercon-incident/guidance")
def release_guidance(body: GuidanceRelease, user: CurrentUser) -> dict[str, int]:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230030))",
            (str(user.organization_id),),
        )
        previous = db.execute(
            "SELECT payload FROM teaching_events WHERE organization_id=%s "
            "AND kind='assignment.guidance.batch' AND payload->>'request_key'=%s",
            (user.organization_id, str(body.request_key)),
        ).fetchone()
        if previous:
            if any(previous["payload"][k] != getattr(body, k) for k in ("title", "message")):
                raise HTTPException(409, "guidance_request_conflict")
            return {"released_count": previous["payload"]["released_count"]}
        recipients = db.execute(
            "SELECT l.id,l.user_id,l.generation FROM labs l "
            "JOIN memberships m ON m.user_id=l.user_id AND m.organization_id=l.organization_id "
            "JOIN users u ON u.id=l.user_id "
            "WHERE l.organization_id=%s AND l.mission_id=%s AND l.mission_version=1 "
            "AND l.state='READY' AND l.desired_state='READY' AND l.team_id IS NULL "
            "AND m.role='student' AND u.active ORDER BY l.id FOR SHARE OF l,m,u",
            (user.organization_id, INCIDENT_ID),
        ).fetchall()
        for lab in recipients:
            event(
                db,
                user,
                None,
                "assignment.guidance.released",
                {
                    "student_id": str(lab["user_id"]),
                    "lab_id": str(lab["id"]),
                    "generation": str(lab["generation"]),
                    "request_key": str(body.request_key),
                    "title": body.title,
                    "message": body.message,
                },
            )
        event(
            db,
            user,
            None,
            "assignment.guidance.batch",
            {
                "request_key": str(body.request_key),
                "title": body.title,
                "message": body.message,
                "released_count": len(recipients),
            },
        )
        return {"released_count": len(recipients)}


@router.post("/assignments/guidance/{guidance_id}/acknowledge")
def acknowledge_guidance(guidance_id: UUID, user: CurrentUser) -> dict[str, bool]:
    if user.role != "student":
        raise HTTPException(403, "role_denied")
    with connect() as db:
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230031))",
            (str(user.organization_id) + str(user.user_id),),
        )
        if not guidance(db, user, guidance_id):
            raise HTTPException(404, "assignment_guidance_not_found")
        if not db.execute(
            "SELECT 1 FROM teaching_events WHERE organization_id=%s AND actor_id=%s "
            "AND kind='assignment.guidance.acknowledged' AND payload->>'guidance_id'=%s",
            (user.organization_id, user.user_id, str(guidance_id)),
        ).fetchone():
            event(
                db,
                user,
                None,
                "assignment.guidance.acknowledged",
                {"guidance_id": str(guidance_id)},
                submitted=True,
            )
        return {"acknowledged": True}


def alert_acknowledged(db: Any, user: Any) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM teaching_events WHERE organization_id=%s AND actor_id=%s "
            "AND kind='assignment.alert.acknowledged' "
            "AND payload->>'mission_id'=%s AND payload->>'mission_version'='1' LIMIT 1",
            (user.organization_id, user.user_id, INCIDENT_ID),
        ).fetchone()
        is not None
    )


@router.get("/assignments/alert", response_model=AssignmentAlert | None)
def pending_alert(user: CurrentUser) -> AssignmentAlert | None:
    if user.role != "student":
        return None
    with connect() as db:
        help_message = guidance(db, user)
        if help_message:
            title = help_message["payload"]["title"]
            message = help_message["payload"]["message"]
            # Render the former built-in guide with its current assignment number.
            # Historical immutable events and custom teacher messages stay intact.
            if message.startswith("OPGAVE 9 · HJÆLP TRIN FOR TRIN"):
                for old, new in (
                    ("OPGAVE 9", "OPGAVE 13"),
                    ("Opgave 9", "Opgave 13"),
                    ("opgave 9", "opgave 13"),
                ):
                    title, message = title.replace(old, new), message.replace(old, new)
            return AssignmentAlert(
                mission_id=INCIDENT_ID,
                mission_version=1,
                released_at=help_message["occurred_at"],
                guidance_id=help_message["event_id"],
                title=title,
                message=message,
            )
        release = db.execute(
            "SELECT released_at FROM mission_releases WHERE organization_id=%s AND user_id=%s "
            "AND mission_id=%s AND mission_version=1",
            (user.organization_id, user.user_id, INCIDENT_ID),
        ).fetchone()
        if not release or alert_acknowledged(db, user):
            return None
        return AssignmentAlert(
            mission_id=INCIDENT_ID,
            mission_version=1,
            released_at=release["released_at"],
            title="AKUT SAG · Nordtele er under angreb",
            message=INCIDENT_MESSAGE,
        )


@router.post("/assignments/cybercon-incident/acknowledge")
def acknowledge_alert(user: CurrentUser) -> dict[str, bool]:
    if user.role != "student":
        raise HTTPException(403, "role_denied")
    with connect() as db:
        release = db.execute(
            "SELECT released_at FROM mission_releases WHERE organization_id=%s AND user_id=%s "
            "AND mission_id=%s AND mission_version=1 FOR UPDATE",
            (user.organization_id, user.user_id, INCIDENT_ID),
        ).fetchone()
        if not release:
            raise HTTPException(404, "assignment_alert_not_found")
        if not alert_acknowledged(db, user):
            event(
                db,
                user,
                None,
                "assignment.alert.acknowledged",
                {"mission_id": INCIDENT_ID, "mission_version": 1},
                submitted=True,
            )
        return {"acknowledged": True}


class AssignmentView(Contract):
    mission_id: str
    mission_version: int
    title: str
    summary: str
    estimated_minutes: int
    available: bool
    release_source: ReleaseSource
    completed: bool
    earned_points: int


class TeacherStudent(Contract):
    user_id: UUID
    username: str
    display_name: str
    teacher_alias: str
    alias_revision: int
    assignments: list[AssignmentView]


class StudentRoster(Contract):
    students: list[TeacherStudent]
    truncated: bool


class RenameStudent(Contract):
    teacher_alias: str = Field(max_length=80)
    revision: int = Field(ge=0, strict=True)

    @field_validator("teacher_alias")
    @classmethod
    def plain_name(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Control characters are not allowed")
        return value.strip()


class ReleaseMission(Contract):
    mission_id: str = Field(min_length=1, max_length=48)
    mission_version: int = Field(ge=1, strict=True)


def assignments(db: Any, organization_id: UUID, user_id: UUID, role: str) -> list[AssignmentView]:
    scores = db.execute(
        "SELECT mission_id,mission_version,objective_id,points FROM score_ledger WHERE organization_id=%s AND user_id=%s",
        (organization_id, user_id),
    ).fetchall()
    result = []
    for mission in catalog():
        source = release_source(db, organization_id, user_id, role, mission.id, mission.version)
        earned = [
            s
            for s in scores
            if s["mission_id"] == mission.id and s["mission_version"] == mission.version
        ]
        result.append(
            AssignmentView(
                mission_id=mission.id,
                mission_version=mission.version,
                title=mission.title,
                summary=mission.summary,
                estimated_minutes=mission.estimated_minutes,
                available=source != "locked",
                release_source=source,
                completed={o.id for o in mission.objectives} <= {s["objective_id"] for s in earned},
                earned_points=sum(s["points"] for s in earned),
            )
        )
    return result


def student(db: Any, organization_id: UUID, user_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    row = db.execute(
        "SELECT m.user_id,u.username,m.teacher_alias,m.alias_revision FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=%s AND m.user_id=%s AND m.role='student' AND u.active"
        + (" FOR UPDATE OF m" if lock else ""),
        (organization_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "student_not_found")
    return dict(row)


def student_view(db: Any, organization_id: UUID, row: dict[str, Any]) -> TeacherStudent:
    return TeacherStudent(
        **row,
        display_name=row["teacher_alias"] or row["username"],
        assignments=assignments(db, organization_id, row["user_id"], "student"),
    )


@router.get("/assignments", response_model=list[AssignmentView])
def my_assignments(user: CurrentUser) -> list[AssignmentView]:
    with connect() as db:
        return assignments(db, user.organization_id, user.user_id, user.role)


@router.get("/command-center/students", response_model=StudentRoster)
def students(user: CurrentUser) -> StudentRoster:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        rows = db.execute(
            "SELECT m.user_id,u.username,m.teacher_alias,m.alias_revision FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=%s AND m.role='student' AND u.active ORDER BY u.username,m.user_id LIMIT 1001",
            (user.organization_id,),
        ).fetchall()
        return StudentRoster(
            students=[student_view(db, user.organization_id, row) for row in rows[:1000]],
            truncated=len(rows) > 1000,
        )


@router.put("/command-center/students/{user_id}/name", response_model=TeacherStudent)
def rename(user_id: UUID, body: RenameStudent, user: CurrentUser) -> TeacherStudent:
    require_scope(user, user.organization_id, teacher=True)
    with connect() as db:
        row = student(db, user.organization_id, user_id, lock=True)
        if row["teacher_alias"] != body.teacher_alias:
            if row["alias_revision"] != body.revision:
                raise HTTPException(409, "student_name_conflict")
            db.execute(
                "UPDATE memberships SET teacher_alias=%s,alias_revision=alias_revision+1 WHERE organization_id=%s AND user_id=%s",
                (body.teacher_alias, user.organization_id, user_id),
            )
            event(db, user, None, "student.alias.changed", {"student_id": str(user_id)})
        elif body.revision not in (row["alias_revision"], row["alias_revision"] - 1):
            raise HTTPException(409, "student_name_conflict")
        return student_view(db, user.organization_id, student(db, user.organization_id, user_id))


@router.post("/command-center/students/{user_id}/releases", response_model=TeacherStudent)
def release(user_id: UUID, body: ReleaseMission, user: CurrentUser) -> TeacherStudent:
    require_scope(user, user.organization_id, teacher=True)
    if not any(m.id == body.mission_id and m.version == body.mission_version for m in catalog()):
        raise HTTPException(404, "mission_not_found")
    with connect() as db:
        row = student(db, user.organization_id, user_id, lock=True)
        inserted = db.execute(
            "INSERT INTO mission_releases(organization_id,user_id,mission_id,mission_version,released_by) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING user_id",
            (user.organization_id, user_id, body.mission_id, body.mission_version, user.user_id),
        ).fetchone()
        if inserted:
            event(
                db,
                user,
                None,
                "student.mission.released",
                {
                    "student_id": str(user_id),
                    "mission_id": body.mission_id,
                    "mission_version": body.mission_version,
                },
            )
        return student_view(db, user.organization_id, row)
