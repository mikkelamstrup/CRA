from typing import Any

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .assignments import router as assignment_router
from .auth import CurrentUser
from .auth import router as auth_router
from .command_center import router as center_router
from .competitions import router as competition_router
from .config import settings
from .contracts import Mission, missions
from .course import router as course_router
from .db import connect, expected_version
from .labs import router as lab_router
from .projector import router as projector_router
from .replay import router as replay_router
from .scoring import router as scoring_router
from .security import configure
from .teaching import router as teaching_router
from .teams import router as team_router
from .terminal import router as terminal_router
from .traffic import router as traffic_router

app = FastAPI(title="Cyber Range Academy", version="0.1.0")
configure(app)
app.include_router(auth_router)
app.include_router(assignment_router)
app.include_router(center_router)
app.include_router(traffic_router)
app.include_router(projector_router)
app.include_router(replay_router)
app.include_router(course_router)
app.include_router(teaching_router)
app.include_router(lab_router)
app.include_router(terminal_router)
app.include_router(scoring_router)
app.include_router(team_router)
app.include_router(competition_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"service": "cyber-range-academy", "status": "ok", "profile": settings().profile}


@app.get("/api/v1/ready", response_model=None)
def ready() -> dict[str, Any] | JSONResponse:
    try:
        with connect() as db:
            row = db.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
            if not row or row["version"] != expected_version():
                return JSONResponse({"ready": False, "reason": "schema_not_ready"}, status_code=503)
    except psycopg.Error:
        return JSONResponse({"ready": False, "reason": "database_unavailable"}, status_code=503)
    return {"ready": True, "database": "connected", "schema_version": row["version"]}


@app.get("/api/v1/missions", response_model=list[Mission])
def list_missions(user: CurrentUser) -> list[Mission]:
    return missions()


if settings().static_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings().static_dir, html=True), name="web")
