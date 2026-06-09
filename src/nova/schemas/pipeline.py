from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nova.schemas.decision import RouterDecision
from nova.schemas.extraction import ExtractionResult
from nova.schemas.validation import ValidationResult


class PipelineRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class StageName(StrEnum):
    INGESTION = "INGESTION"
    EXTRACTION = "EXTRACTION"
    VALIDATION = "VALIDATION"
    ROUTING = "ROUTING"
    STORAGE = "STORAGE"
    QUERY = "QUERY"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class StageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: StageName
    status: StageStatus
    started_at: datetime
    completed_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    trace_id: str | None = None
    message: str | None = None
    error_message: str | None = None

    @field_validator("trace_id", "message", "error_message")
    @classmethod
    def reject_empty_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must be non-empty when provided")
        return value

    @model_validator(mode="after")
    def validate_stage_timing(self) -> "StageEvent":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be before started_at")
        return self


class PipelineRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    document_id: str
    source_filename: str | None = None
    customer_id: str
    status: PipelineRunStatus
    stages: list[StageEvent]
    started_at: datetime
    completed_at: datetime | None = None
    cost_total_usd: float = Field(ge=0.0)
    trace_id: str
    extraction_result: ExtractionResult | None = None
    validation_result: ValidationResult | None = None
    router_decision: RouterDecision | None = None

    @field_validator("document_id", "customer_id", "trace_id", "source_filename")
    @classmethod
    def require_non_empty_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_run_timing(self) -> "PipelineRun":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be before started_at")
        return self
