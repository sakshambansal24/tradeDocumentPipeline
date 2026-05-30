from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FieldValidationStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNCERTAIN = "UNCERTAIN"
    MISSING = "MISSING"


class ValidationOverallStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class FieldValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    status: FieldValidationStatus
    found_value: str | None
    expected_value: str | None
    expected_rule: str
    reason: str
    extraction_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("field_name", "expected_rule", "reason")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @model_validator(mode="after")
    def require_expected_value_for_mismatch(self) -> "FieldValidation":
        if self.status == FieldValidationStatus.MISMATCH and self.expected_value is None:
            raise ValueError("expected_value is required for mismatches")
        return self


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_id: str
    customer_id: str
    rule_set_version: str
    field_results: list[FieldValidation]
    overall_status: ValidationOverallStatus
    validator_confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("extraction_id", "customer_id", "rule_set_version")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value
