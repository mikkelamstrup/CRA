"""An allocated operator plus current team membership is required for learner access."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException

from .auth import Identity
from .jobs import DB

# Caller supplies aliases and parameter values; no browser input is SQL.
ACCESS = """l.organization_id=%s AND (l.user_id=%s OR EXISTS (
    SELECT 1 FROM lab_participants p JOIN team_members m ON m.user_id=p.user_id AND m.team_id=l.team_id
    WHERE p.lab_id=l.id AND p.user_id=%s AND m.organization_id=l.organization_id))"""


def accessible_lab(
    db: DB, lab_id: UUID, user: Identity, *, lock: bool = False, operator: bool = False
) -> dict[str, Any]:
    row = db.execute(
        "SELECT l.* FROM labs l WHERE l.id=%s AND " + ACCESS + (" FOR UPDATE OF l" if lock else ""),
        (lab_id, user.organization_id, user.user_id, user.user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "not_found")
    if (
        operator
        and row["team_id"] is not None
        and not db.execute(
            "SELECT p.user_id FROM lab_participants p JOIN team_members m ON m.user_id=p.user_id AND m.team_id=%s "
            "WHERE p.lab_id=%s AND p.user_id=%s AND m.organization_id=%s",
            (row["team_id"], lab_id, user.user_id, user.organization_id),
        ).fetchone()
    ):
        raise HTTPException(403, "operator_not_assigned")
    return row
