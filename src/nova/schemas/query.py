from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nova.schemas.decision import DecisionType
from nova.schemas.pipeline import PipelineRunStatus, StageEvent
from nova.schemas.validation import FieldValidationStatus


class RunQueryFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str | None = None
    decision: DecisionType | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class FieldValidationQueryFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str | None = None
    field_name: str | None = None
    status: FieldValidationStatus | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class CountRunsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: RunQueryFilters


class ListRunsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: RunQueryFilters
    limit: int = Field(default=10, ge=1, le=100)


class GetRunDetailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str

    @field_validator("run_id")
    @classmethod
    def require_non_empty_run_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("run_id must be non-empty")
        return value


class CountFieldValidationsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: FieldValidationQueryFilters


class TopFailingFieldsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: datetime
    date_to: datetime
    limit: int = Field(default=5, ge=1, le=50)
    customer_id: str | None = None


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    document_id: str
    customer_id: str
    status: PipelineRunStatus
    decision: DecisionType | None
    completed_at: datetime | None
    cost_total_usd: float


class RunDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: RunSummary
    stage_history: list[StageEvent]


class TopFailingField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    mismatch_count: int = Field(ge=0)


class QueryToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "count_runs",
        "list_runs",
        "get_run_detail",
        "count_field_validations",
        "top_failing_fields",
    ]
    args: (
        CountRunsArgs
        | ListRunsArgs
        | GetRunDetailArgs
        | CountFieldValidationsArgs
        | TopFailingFieldsArgs
    )


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: list[QueryToolCall]


class QueryEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    args: dict[str, Any]
    result: Any


class QueryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: list[QueryEvidenceItem]


class QueryAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    evidence: QueryEvidence
