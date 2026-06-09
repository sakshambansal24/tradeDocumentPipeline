from datetime import UTC, datetime
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from nova.schemas.extraction import DocumentType


class EmailAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    path: Path
    detected_doc_type: DocumentType | None = None

    @field_validator("filename")
    @classmethod
    def require_non_empty_filename(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("filename must be non-empty")
        return value


class IncomingEmail(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    email_id: str
    sender: str = Field(validation_alias=AliasChoices("sender", "from"))
    recipient: str | None = None
    subject: str
    customer_id: str
    attachments: list[EmailAttachment]
    message_id: str | None = None
    references: list[str] = Field(default_factory=list)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("email_id", "sender", "subject", "customer_id")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @field_validator("recipient", "message_id")
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

    @field_validator("attachments")
    @classmethod
    def require_attachments(cls, value: list[EmailAttachment]) -> list[EmailAttachment]:
        if not value:
            raise ValueError("attachments must include at least one document")
        return value
