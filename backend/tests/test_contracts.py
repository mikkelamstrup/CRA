import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cra.contracts import Event, Mission, missions
from cra.generate_contracts import ROOT, generated


def test_mission_rejects_architecture_egress_unbounded_resources_and_duplicates():
    mission = next(m for m in missions() if m.id == "first-contact")
    for field, value in [
        ("architecture", "aarch64"),
        ("egress", True),
        ("operator_memory_mib", 0),
        ("image_sha256", "unlocked"),
    ]:
        data = mission.model_dump()
        data["resources"][field] = value
        with pytest.raises(ValidationError):
            Mission.model_validate(data)
    data = mission.model_dump()
    data["objectives"].append(data["objectives"][0])
    with pytest.raises(ValidationError):
        Mission.model_validate(data)
    with pytest.raises(ValidationError):
        Mission.model_validate({**mission.model_dump(), "shell": "arbitrary"})


def test_event_evidence_is_not_inferred_from_observation():
    event = {
        "schema_version": 1,
        "event_id": uuid4(),
        "organization_id": uuid4(),
        "lab_id": uuid4(),
        "generation": 1,
        "actor_id": uuid4(),
        "occurred_at": datetime.now(UTC),
        "source": "score",
        "evidence_level": "validated",
        "payload": {
            "kind": "objective.completed",
            "objective_id": "find-service",
            "points_delta": 100,
        },
    }
    assert Event.model_validate(event).payload.kind == "objective.completed"
    for delta in [
        {"source": "sensor"},
        {"evidence_level": "observed"},
        {"generation": 0},
        {"schema_version": 2},
        {"source": "control"},
        {"occurred_at": "2026-01-01T00:00:00"},
    ]:
        with pytest.raises(ValidationError):
            Event.model_validate({**event, **delta})


def test_generated_contracts_are_current():
    types, schemas = generated()
    assert (ROOT / "frontend/src/contracts.ts").read_text() == types
    assert json.loads((ROOT / "content/contracts.v1.json").read_text()) == json.loads(schemas)


def test_mission_route_requires_identity():
    from fastapi.testclient import TestClient

    from cra.main import app

    assert (
        TestClient(app, base_url="http://127.0.0.1:28000").get("/api/v1/missions").status_code
        == 401
    )
