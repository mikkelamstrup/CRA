"""Versioned wire contracts; reject unknown fields and unsupported architectures."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Objective(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,47}$")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    points: int = Field(ge=0, le=1000)
    evidence: Literal["validated_flag"]


class Resources(Contract):
    architecture: Literal["x86_64"]
    operator_memory_mib: Literal[1024, 2048]
    operator_profile: Literal["headless", "desktop"] = "headless"
    target_memory_mib: Literal[768, 1024]
    operator_vcpus: Literal[1]
    target_vcpus: Literal[1]
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    network: Literal["isolated-l2-pair-v1"]
    egress: Literal[False]

    @model_validator(mode="after")
    def profile_matches_memory(self) -> "Resources":
        if self.operator_memory_mib != (2048 if self.operator_profile == "desktop" else 1024):
            raise ValueError("Operator profile memory mismatch")
        return self


class Mission(Contract):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,47}$")
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    briefing: str = Field(min_length=1, max_length=10000)
    estimated_minutes: int = Field(ge=1, le=240)
    resources: Resources
    objectives: list[Objective] = Field(min_length=1, max_length=20)
    hints: list[str] = Field(max_length=10)

    @model_validator(mode="after")
    def unique_objectives(self) -> "Mission":
        if len({item.id for item in self.objectives}) != len(self.objectives):
            raise ValueError("Duplicate objective IDs")
        return self


class LabState(StrEnum):
    REQUESTED = "REQUESTED"
    QUEUED = "QUEUED"
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RESETTING = "RESETTING"
    DESTROYING = "DESTROYING"
    DESTROYED = "DESTROYED"
    ERROR = "ERROR"


class LifecyclePayload(Contract):
    kind: Literal["lab.lifecycle"]
    state: LabState


class ObjectivePayload(Contract):
    kind: Literal["objective.completed"]
    objective_id: str
    points_delta: int = Field(ge=0, le=1000)


class ActionPayload(Contract):
    kind: Literal["lab.action_requested"]
    action: Literal["start", "stop", "reset", "destroy"]
    requested_generation: int = Field(ge=1, le=10000)


class Event(Contract):
    schema_version: Literal[1]
    event_id: UUID
    organization_id: UUID
    lab_id: UUID
    generation: int = Field(ge=1)
    actor_id: UUID
    initiator_type: Literal["user", "system"] = "system"
    occurred_at: datetime
    source: Literal["control", "score"]
    evidence_level: Literal["system", "validated"]
    payload: Annotated[
        LifecyclePayload | ObjectivePayload | ActionPayload, Field(discriminator="kind")
    ]

    @model_validator(mode="after")
    def evidence_matches_source(self) -> "Event":
        if isinstance(self.payload, ObjectivePayload):
            if self.source != "score" or self.evidence_level != "validated":
                raise ValueError("Objective completion requires score validation")
        elif self.source != "control" or self.evidence_level != "system":
            raise ValueError("Lifecycle requires control evidence")
        if self.occurred_at.tzinfo is None:
            raise ValueError("Timestamp requires timezone")
        return self


CONTENT = Path(__file__).resolve().parents[2] / "content/missions"


def missions() -> list[Mission]:
    result = [
        Mission.model_validate_json(path.read_text()) for path in sorted(CONTENT.glob("*.json"))
    ]
    if not result or len({(m.id, m.version) for m in result}) != len(result):
        raise RuntimeError("Missing or duplicate mission release")
    return result
