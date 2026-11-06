"""Narrow control-to-node commands, independent of hypervisor implementation."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .capacity import MAX_ACTIVE_LABS, RESERVATION_SLOTS, target_ip
from .contracts import Contract, Resources
from .flow_contracts import NodeCapture


class NodeCommand(Contract):
    node_id: int = Field(default=1, ge=1, le=2, strict=True)
    schema_version: Literal[1]
    job_id: UUID
    lab_id: UUID
    organization_id: UUID
    user_id: UUID
    operation: Literal["create", "start", "stop", "reset", "destroy"]
    generation: int = Field(ge=1, le=10000, strict=True)
    slot: int = Field(ge=0, le=RESERVATION_SLOTS - 1, strict=True)
    mission_id: Literal[
        "first-contact",
        "first-files",
        "web-trail",
        "web-trail-dns",
        "access-granted",
        "packet-hunter",
        "vulnerable-app",
        "operation-shadow",
        "cybercon-incident",
        "request-forge",
        "incident-http",
        "incident-evidence",
        "incident-containment",
        "incident-ownership",
    ]
    mission_version: Literal[1]
    resources: Resources
    operator_user_ids: list[UUID] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def unique_operators(self) -> "NodeCommand":
        if self.mission_id in (
            "cybercon-incident",
            "request-forge",
            "incident-http",
            "incident-evidence",
            "incident-containment",
            "incident-ownership",
        ) and self.operator_user_ids not in (
            [],
            [self.user_id],
        ):
            raise ValueError("Mission requires an individual operator")
        if len(set(self.operator_user_ids)) != len(self.operator_user_ids):
            raise ValueError("Duplicate operator identity")
        return self


class TerminalAllocation(Contract):
    payload: str = Field(max_length=2048)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class TerminalClaims(Contract):
    node_id: int = Field(default=1, ge=1, le=2, strict=True)
    schema_version: Literal[1]
    lab_id: UUID
    organization_id: UUID
    user_id: UUID
    generation: int = Field(ge=1, le=10000, strict=True)
    grant_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: int = Field(strict=True)
    operator_user_id: UUID | None = None
    purpose: Literal["terminal", "desktop"] = "terminal"


class NodeOperator(Contract):
    user_id: UUID
    name: str = Field(pattern=r"^cra-[a-z0-9-]{1,60}[opqr]$")
    present: bool = Field(strict=True)
    running: bool = Field(strict=True)


class NodeLab(Contract):
    id: UUID
    organization_id: UUID
    user_id: UUID
    generation: int = Field(ge=1, le=10000, strict=True)
    slot: int | None = Field(ge=0, le=RESERVATION_SLOTS - 1)
    state: Literal["PROVISIONING", "READY", "STOPPED", "DESTROYED"]
    operator: str = Field(pattern=r"^cra-[a-z0-9-]{1,60}o$")
    target: str = Field(pattern=r"^cra-[a-z0-9-]{1,60}t$")
    target_ip: str = Field(pattern=r"^192\.168\.[0-9]{2,3}\.10$")
    operator_running: bool = Field(strict=True)
    target_running: bool = Field(strict=True)
    operator_present: bool = Field(strict=True)
    target_present: bool = Field(strict=True)
    ownership_valid: bool = Field(strict=True)
    running_memory_mib: int = Field(default=0, ge=0, le=20480, strict=True)
    capture_ready: bool = Field(default=False, strict=True)
    captures: list[NodeCapture] = Field(default_factory=list, max_length=4)
    operators: list[NodeOperator] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def consistent(self) -> "NodeLab":
        if self.state != "DESTROYED":
            prefix = "cra-" + self.id.hex[:10] + "-" + format(self.generation, "x")
            if (
                self.slot is None
                or self.operator != prefix + "o"
                or self.target != prefix + "t"
                or self.target_ip != target_ip(self.slot)
            ):
                raise ValueError("Node resource identity mismatch")
        if self.operators:
            prefix = "cra-" + self.id.hex[:10] + "-" + format(self.generation, "x")
            if len({item.user_id for item in self.operators}) != len(self.operators):
                raise ValueError("Duplicate operator identity")
            for index, item in enumerate(self.operators):
                if item.name != prefix + "opqr"[index] or (item.running and not item.present):
                    raise ValueError("Operator resource mismatch")
            first = self.operators[0]
            if (
                first.name != self.operator
                or first.present != self.operator_present
                or first.running != self.operator_running
            ):
                raise ValueError("Primary operator mismatch")
        expected = [op.user_id for op in self.operators] or [self.user_id]
        if self.captures and [item.operator_user_id for item in self.captures] != expected:
            raise ValueError("Capture assignment mismatch")
        if self.capture_ready != (bool(self.captures) and all(c.connected for c in self.captures)):
            raise ValueError("Capture readiness mismatch")
        if (self.operator_running and not self.operator_present) or (
            self.target_running and not self.target_present
        ):
            raise ValueError("Node resource state mismatch")
        return self


class NodeInventory(Contract):
    node_id: int = Field(default=1, ge=1, le=2, strict=True)
    schema_version: Literal[1]
    architecture: Literal["x86_64"]
    connected: Literal[True]
    labs: list[NodeLab] = Field(max_length=10000)
    available_memory_mib: int = Field(ge=0, strict=True)
    physical_memory_mib: int | None = Field(default=None, ge=0, strict=True)
    running_declared_memory_mib: int | None = Field(default=None, ge=0, strict=True)
    free_disk_gib: int = Field(ge=0, strict=True)
    max_labs: int = Field(ge=1, le=MAX_ACTIVE_LABS, strict=True)
    observed_at: float = Field(ge=0, le=4102444800, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique_labs(self) -> "NodeInventory":
        if len({lab.id for lab in self.labs}) != len(self.labs):
            raise ValueError("Duplicate node lab")
        slots = [lab.slot for lab in self.labs if lab.slot is not None]
        if len(set(slots)) != len(slots):
            raise ValueError("Duplicate node reservation")
        return self
