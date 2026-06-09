from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nova.schemas.decision import RouterDecision
from nova.schemas.pipeline import PipelineRun


class CrossFieldStatus(StrEnum):
    CONSISTENT = "CONSISTENT"
    INCONSISTENT = "INCONSISTENT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class CrossFieldMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    values_by_doc: dict[str, str | None]
    status: CrossFieldStatus
    reason: str

    @field_validator("field_name", "reason")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @field_validator("values_by_doc")
    @classmethod
    def require_document_values(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        for doc_type, extracted_value in value.items():
            if not doc_type.strip():
                raise ValueError("document type keys must be non-empty")
            if extracted_value is not None and not extracted_value.strip():
                raise ValueError("document values cannot be empty strings")
        return value


class CrossValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: UUID
    checked_fields: list[CrossFieldMatch]
    overall_consistent: bool
    checked_at: datetime

    @model_validator(mode="after")
    def validate_overall_consistency(self) -> "CrossValidationResult":
        has_inconsistent_field = any(
            field.status == CrossFieldStatus.INCONSISTENT for field in self.checked_fields
        )
        if self.overall_consistent and has_inconsistent_field:
            raise ValueError("overall_consistent cannot be true when a field is inconsistent")
        return self


class ShipmentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    APPROVED = "APPROVED"
    AMENDED = "AMENDED"


class Shipment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: UUID
    email_id: str
    customer_id: str
    triggered_by: str | None = None
    recipient: str | None = None
    subject: str | None = None
    original_message_id: str | None = None
    references: list[str] = Field(default_factory=list)
    reply_message_id: str | None = None
    reply_mail_path: str | None = None
    triggered_at: datetime
    status: ShipmentStatus
    document_runs: list[PipelineRun]
    cross_validation_result: CrossValidationResult | None = None
    overall_decision: RouterDecision | None = None
    draft_reply: str | None = None
    completed_at: datetime | None = None

    @field_validator("email_id", "customer_id")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @field_validator("triggered_by")
    @classmethod
    def reject_empty_triggered_by(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("triggered_by cannot be empty")
        return value

    @field_validator(
        "recipient",
        "subject",
        "original_message_id",
        "reply_message_id",
        "reply_mail_path",
    )
    @classmethod
    def reject_empty_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must be non-empty when provided")
        return value

    @field_validator("references")
    @classmethod
    def reject_empty_references(cls, value: list[str]) -> list[str]:
        for reference in value:
            if not reference.strip():
                raise ValueError("references cannot contain empty values")
        return value

    @field_validator("draft_reply")
    @classmethod
    def reject_empty_draft_reply(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("draft_reply cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_shipment_timing(self) -> "Shipment":
        if self.completed_at is not None and as_utc(self.completed_at) < as_utc(self.triggered_at):
            raise ValueError("completed_at cannot be before triggered_at")
        if (
            self.cross_validation_result is not None
            and self.cross_validation_result.shipment_id != self.shipment_id
        ):
            raise ValueError("cross_validation_result shipment_id must match shipment_id")
        return self


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
