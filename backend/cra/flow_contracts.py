"""Versioned, bounded observations. IP headers never establish operator identity."""

from ipaddress import ip_address
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .contracts import Contract

Counter = Annotated[int, Field(ge=0, le=9007199254740991, strict=True)]
Sequence = Annotated[int, Field(ge=1, le=9007199254740991, strict=True)]
Moment = Annotated[float, Field(ge=0, le=4102444800, allow_inf_nan=False)]


class HeaderFlow(Contract):
    direction: Literal["operator_to_target", "target_to_operator"]
    src: str = Field(max_length=39)
    dst: str = Field(max_length=39)
    protocol: int = Field(ge=0, le=255, strict=True)
    source_port: int | None = Field(ge=0, le=65535, strict=True)
    destination_port: int | None = Field(ge=0, le=65535, strict=True)
    packets: int = Field(ge=1, le=9007199254740991, strict=True)
    bytes: int = Field(ge=1, le=9007199254740991, strict=True)

    @field_validator("src", "dst")
    @classmethod
    def numeric_address(cls, value: str) -> str:
        if "%" in value:
            raise ValueError("Zone identifiers are not packet headers")
        return str(ip_address(value))

    @model_validator(mode="after")
    def ports_match(self) -> "HeaderFlow":
        if (self.source_port is None) != (self.destination_port is None):
            raise ValueError("Incomplete port observation")
        if self.protocol not in (6, 17) and self.source_port is not None:
            raise ValueError("Unexpected transport ports")
        return self


class RawBase(Contract):
    sequence: Sequence
    window_sequence: Sequence
    observed_at: Moment

    @model_validator(mode="after")
    def ordered_record(self) -> "RawBase":
        if self.sequence < self.window_sequence:
            raise ValueError("Window ahead of record sequence")
        return self


class RawFlow(RawBase, HeaderFlow):
    kind: Literal["flow"]


class RawStatus(RawBase):
    kind: Literal["status"]
    state: Literal["waiting", "connected", "disconnected"]
    metadata_dropped: Counter
    unknown_packets: Counter
    flow_overflow: Counter
    packets: Counter
    bytes: Counter
    max_queue: int = Field(ge=0, le=262144, strict=True)

    @model_validator(mode="after")
    def counters_match(self) -> "RawStatus":
        if (
            self.unknown_packets + self.flow_overflow > self.packets
            or self.bytes < self.packets
            or self.bytes > self.packets * 69632
        ):
            raise ValueError("Impossible sensor counters")
        if self.metadata_dropped >= self.sequence:
            raise ValueError("Impossible metadata drop count")
        return self


class FlowWindow(Contract):
    schema_version: Literal[1] = 1
    sequence: Sequence
    source_sequence: Sequence
    observed_at: Moment
    flows: list[HeaderFlow] = Field(max_length=16)
    partial: bool = Field(strict=True)
    omitted_packets: Counter

    @model_validator(mode="after")
    def ordered_seal(self) -> "FlowWindow":
        if self.source_sequence < self.sequence:
            raise ValueError("Window ahead of seal")
        return self


class CaptureStatus(Contract):
    operator_user_id: UUID
    epoch: UUID
    connected: bool = Field(strict=True)
    observed_at: Moment
    last_window: Counter
    metadata_dropped: Counter
    unknown_packets: Counter
    flow_overflow: Counter
    sequence_gaps: Counter
    partial_windows: Counter
    ring_overwrites: Counter
    packets: Counter
    bytes: Counter
    poll_missed_windows: Counter = 0


class NodeCapture(CaptureStatus):
    windows: list[FlowWindow] = Field(max_length=8)

    @model_validator(mode="after")
    def ordered_windows(self) -> "NodeCapture":
        sequences = [w.sequence for w in self.windows]
        if sequences != sorted(set(sequences)) or any(n > self.last_window for n in sequences):
            raise ValueError("Invalid capture window order")
        return self
