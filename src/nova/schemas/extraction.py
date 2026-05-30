from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DocumentType(StrEnum):
    BOL = "BOL"
    INVOICE = "INVOICE"
    PACKING_LIST = "PACKING_LIST"
    COO = "COO"
    UNKNOWN = "UNKNOWN"


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    source_page: int = Field(ge=1)
    source_snippet: str
    reasoning: str
    is_present: bool

    @field_validator("name", "reasoning")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @model_validator(mode="after")
    def require_evidence_for_present_fields(self) -> "ExtractedField":
        if self.is_present and not self.source_snippet.strip():
            raise ValueError("source_snippet is required when a field is present")
        if not self.is_present and self.value is not None:
            raise ValueError("value must be null when is_present is false")
        return self


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    document_type: DocumentType
    fields: dict[str, ExtractedField]
    model_used: str
    latency_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    raw_response_id: str

    @field_validator("document_id", "model_used", "raw_response_id")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value
