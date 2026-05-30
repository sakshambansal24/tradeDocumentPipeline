from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RuleType(StrEnum):
    EXACT = "exact"
    ALLOWED_VALUES = "allowed_values"
    REGEX = "regex"
    NUMERIC_RANGE = "numeric_range"
    PRESENCE = "presence"


class FieldRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RuleType
    value: str | None = None
    values: list[str] | None = None
    pattern: str | None = None
    min_kg: float | None = None
    max_kg: float | None = None
    required: bool | None = None
    case_insensitive: bool = True
    trim: bool = True

    @field_validator("value", "pattern")
    @classmethod
    def reject_empty_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must be non-empty when provided")
        return value

    @field_validator("values")
    @classmethod
    def reject_empty_allowed_values(cls, values: list[str] | None) -> list[str] | None:
        if values is not None:
            if not values:
                raise ValueError("values must not be empty")
            for value in values:
                if not value.strip():
                    raise ValueError("values cannot contain empty strings")
        return values

    @model_validator(mode="after")
    def validate_rule_shape(self) -> "FieldRule":
        match self.type:
            case RuleType.EXACT:
                if self.value is None:
                    raise ValueError("exact rule requires value")
            case RuleType.ALLOWED_VALUES:
                if self.values is None:
                    raise ValueError("allowed_values rule requires values")
            case RuleType.REGEX:
                if self.pattern is None:
                    raise ValueError("regex rule requires pattern")
            case RuleType.NUMERIC_RANGE:
                if self.min_kg is None and self.max_kg is None:
                    raise ValueError("numeric_range rule requires min_kg or max_kg")
                if (
                    self.min_kg is not None
                    and self.max_kg is not None
                    and self.min_kg > self.max_kg
                ):
                    raise ValueError("min_kg cannot exceed max_kg")
            case RuleType.PRESENCE:
                if self.required is None:
                    raise ValueError("presence rule requires required")
        return self


class CrossFieldCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    operator: str
    values: list[str]

    @field_validator("field", "operator")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value


class CrossFieldExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    pattern: str | None = None
    length: int | None = Field(default=None, gt=0)
    reason: str

    @field_validator("field", "reason")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value


class CrossFieldRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    if_: CrossFieldCondition = Field(alias="if")
    then: CrossFieldExpectation

    @field_validator("name")
    @classmethod
    def require_non_empty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value


class CustomerRuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    version: str
    fields: dict[str, FieldRule]
    cross_field_rules: list[CrossFieldRule] = Field(default_factory=list)

    @field_validator("customer_id", "version")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value
