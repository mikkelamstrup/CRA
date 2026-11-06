"""Class release policy supplemented by exact-version, individual teacher grants."""

from typing import Any, Literal
from uuid import UUID

from .contracts import Mission, missions
from .course import days

ReleaseSource = Literal["class", "individual", "teacher", "locked"]


def mission_day(mission_id: str) -> int:
    return next(
        (d.day for d in days() if d.mission_id == mission_id),
        {
            "first-contact": 2,
            "web-trail": 3,
            "incident-http": 9,
            "incident-evidence": 10,
            "incident-containment": 11,
            "incident-ownership": 12,
            "cybercon-incident": 13,
            "request-forge": 14,
        }.get(mission_id, 15),
    )


def catalog() -> list[Mission]:
    return sorted(missions(), key=lambda m: (mission_day(m.id), m.id, m.version))


def release_source(
    db: Any, organization_id: UUID, user_id: UUID, role: str, mission_id: str, version: int
) -> ReleaseSource:
    if role == "teacher":
        return "teacher"
    # Advanced individual missions always require deliberate teacher release.
    if mission_id in (
        "cybercon-incident",
        "request-forge",
        "incident-http",
        "incident-evidence",
        "incident-containment",
        "incident-ownership",
    ):
        grant = db.execute(
            "SELECT 1 FROM mission_releases WHERE organization_id=%s AND user_id=%s AND mission_id=%s AND mission_version=%s",
            (organization_id, user_id, mission_id, version),
        ).fetchone()
        return "individual" if grant else "locked"
    session = db.execute(
        "SELECT day FROM teaching_sessions WHERE organization_id=%s AND NOT closed",
        (organization_id,),
    ).fetchone()
    if session is None or mission_day(mission_id) <= session["day"]:
        return "class"
    grant = db.execute(
        "SELECT 1 FROM mission_releases WHERE organization_id=%s AND user_id=%s AND mission_id=%s AND mission_version=%s",
        (organization_id, user_id, mission_id, version),
    ).fetchone()
    return "individual" if grant else "locked"
