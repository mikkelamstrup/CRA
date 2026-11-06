"""Versioned teaching content and private, optimistic-locking learner notebooks."""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import Field

from .auth import CurrentUser, require_scope
from .contracts import Contract, missions
from .db import connect

router = APIRouter(prefix="/api/v1/course")
CONTENT = Path(__file__).resolve().parents[2] / "content/course"


class Lesson(Contract):
    title: str
    text: str
    exercise: str


class CourseDay(Contract):
    day: int = Field(ge=1, le=8)
    version: int = Field(ge=1)
    title: str
    summary: str
    mission_id: str
    mission_version: int
    goals: list[str]
    schedule: list[str]
    lessons: list[Lesson]
    commands: list[str]
    reflection: list[str]
    bonus: list[str]


def days() -> list[CourseDay]:
    result = [
        CourseDay.model_validate_json(p.read_text()) for p in sorted(CONTENT.glob("day-*.json"))
    ]
    if not result or len({d.day for d in result}) != len(result):
        raise RuntimeError("Missing or duplicate course day")
    catalog = {(m.id, m.version) for m in missions()}
    for day in result:
        if (day.mission_id, day.mission_version) not in catalog:
            raise RuntimeError("Course references an unknown mission release")
        if not (CONTENT / "teacher" / f"day-{day.day:02}.md").is_file():
            raise RuntimeError("Course teacher notes are missing")
    return result


def day_by_id(day: int) -> CourseDay:
    for item in days():
        if item.day == day:
            return item
    raise HTTPException(404, "day_not_found")


class NotebookWrite(Contract):
    revision: int = Field(ge=0, strict=True)
    body: str = Field(max_length=16000)
    reflection: str = Field(max_length=4000)
    theory_read: bool


class Notebook(NotebookWrite):
    day: int
    updated_at: datetime | None
    evidence_level: Literal["submitted"] = "submitted"


@router.get("/days", response_model=list[CourseDay])
def course_days(user: CurrentUser) -> list[CourseDay]:
    result = days()
    if user.role == "student":
        with connect() as db:
            session = db.execute(
                "SELECT day FROM teaching_sessions WHERE organization_id=%s AND NOT closed",
                (user.organization_id,),
            ).fetchone()
            if session:
                grants = {
                    (r["mission_id"], r["mission_version"])
                    for r in db.execute(
                        "SELECT mission_id,mission_version FROM mission_releases "
                        "WHERE organization_id=%s AND user_id=%s",
                        (user.organization_id, user.user_id),
                    )
                }
                result = [
                    day
                    for day in result
                    if day.day <= session["day"] or (day.mission_id, day.mission_version) in grants
                ]
    return result


@router.get("/days/{day}/notebook", response_model=Notebook)
def notebook(day: int, user: CurrentUser) -> Notebook:
    day_by_id(day)
    with connect() as db:
        row = db.execute(
            "SELECT day,body,reflection,theory_read,revision,updated_at FROM notebooks "
            "WHERE organization_id=%s AND user_id=%s AND day=%s",
            (user.organization_id, user.user_id, day),
        ).fetchone()
        return Notebook.model_validate(
            row
            or {
                "day": day,
                "body": "",
                "reflection": "",
                "theory_read": False,
                "revision": 0,
                "updated_at": None,
            }
        )


@router.put("/days/{day}/notebook", response_model=Notebook)
def save_notebook(day: int, body: NotebookWrite, user: CurrentUser) -> Notebook:
    day_by_id(day)
    with connect() as db:
        # One identity/day lock also serializes first insert and uncertain retry.
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1415230011))",
            (f"{user.organization_id}:{user.user_id}:{day}",),
        )
        old = db.execute(
            "SELECT * FROM notebooks WHERE organization_id=%s AND user_id=%s AND day=%s FOR UPDATE",
            (user.organization_id, user.user_id, day),
        ).fetchone()
        row: dict[str, Any] | None
        same = old and all(
            old[key] == getattr(body, key) for key in ("body", "reflection", "theory_read")
        )
        if old and same and body.revision in (old["revision"], old["revision"] - 1):
            row = old
        else:
            if (old["revision"] if old else 0) != body.revision:
                raise HTTPException(409, "notebook_conflict")
            row = db.execute(
                "INSERT INTO notebooks(organization_id,user_id,day,body,reflection,theory_read) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(organization_id,user_id,day) DO UPDATE SET body=excluded.body,reflection=excluded.reflection,theory_read=excluded.theory_read,revision=notebooks.revision+1,updated_at=now() RETURNING *",
                (
                    user.organization_id,
                    user.user_id,
                    day,
                    body.body,
                    body.reflection,
                    body.theory_read,
                ),
            ).fetchone()
        assert row
        return Notebook.model_validate(
            {
                key: row[key]
                for key in ("day", "body", "reflection", "theory_read", "revision", "updated_at")
            }
        )


@router.get("/days/{day}/export", response_class=PlainTextResponse)
def export_day(day: int, user: CurrentUser) -> str:
    if not any(item.day == day for item in course_days(user)):
        raise HTTPException(403, "course_day_locked")
    lesson = day_by_id(day)
    note = notebook(day, user)
    lines = [f"Dag {day}: {lesson.title}", lesson.summary, "Læringsmål", *lesson.goals]
    for part in lesson.lessons:
        lines.extend([part.title, part.text, "Prøv selv: " + part.exercise])
    lines.extend(
        [
            "Dagens rytme",
            *lesson.schedule,
            "Kommandooversigt",
            *lesson.commands,
            "Ekstraopgaver",
            *lesson.bonus,
            "Refleksionsspørgsmål",
            *lesson.reflection,
        ]
    )
    lines.extend(
        [
            "Mine noter (indleveret, ikke automatisk valideret)",
            note.body,
            "Min refleksion",
            note.reflection,
        ]
    )
    return "\n\n".join(lines)


@router.get("/days/{day}/teacher-notes", response_class=PlainTextResponse)
def teacher_notes(day: int, user: CurrentUser) -> str:
    require_scope(user, user.organization_id, teacher=True)
    day_by_id(day)
    return (CONTENT / "teacher" / f"day-{day:02}.md").read_text()
