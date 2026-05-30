from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class DecisionType(StrEnum):
    AUTO_APPROVE = "AUTO_APPROVE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    AMEND = "AMEND"


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionType
    reasoning: str
    drafted_message: str | None = None
    risk_flags: list[str]

    @field_validator("reasoning")
    @classmethod
    def require_non_empty_reasoning(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reasoning must be non-empty")
        return value

    @field_validator("risk_flags")
    @classmethod
    def require_non_empty_risk_flags(cls, value: list[str]) -> list[str]:
        for flag in value:
            if not flag.strip():
                raise ValueError("risk_flags cannot contain empty values")
        return value

    @model_validator(mode="after")
    def require_draft_for_amendment(self) -> "RouterDecision":
        if self.decision == DecisionType.AMEND and not self.drafted_message:
            raise ValueError("drafted_message is required when decision is AMEND")
        if self.drafted_message is not None and not self.drafted_message.strip():
            raise ValueError("drafted_message cannot be empty")
        return self
