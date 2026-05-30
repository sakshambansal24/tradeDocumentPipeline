"""Validator Agent.

Hybrid strategy: deterministic rules handle exact, allowed-value, regex, range, and presence
checks cheaply and repeatably. Fuzzy string matches produce UNCERTAIN only in the narrow band
where deterministic logic is likely too brittle. The LLM is called only for those uncertain
fields, never for the whole extraction, which keeps cost low, preserves auditability, and makes
reviewer-facing failures inspectable. Final confidence reflects how much LLM adjudication was
needed.
"""

from typing import Protocol

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, field_validator

from nova.rules.engine import apply_rules
from nova.rules.loader import load_rules
from nova.schemas.extraction import ExtractionResult
from nova.schemas.rules import CustomerRuleSet
from nova.schemas.validation import (
    FieldValidation,
    FieldValidationStatus,
    ValidationOverallStatus,
    ValidationResult,
)
from nova.settings import get_settings


class ValidatorError(Exception):
    pass


class LLMValidationVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: FieldValidationStatus
    reason: str

    @field_validator("status")
    @classmethod
    def restrict_status(cls, value: FieldValidationStatus) -> FieldValidationStatus:
        if value not in {
            FieldValidationStatus.MATCH,
            FieldValidationStatus.MISMATCH,
            FieldValidationStatus.UNCERTAIN,
        }:
            raise ValueError("LLM adjudication must be MATCH, MISMATCH, or UNCERTAIN")
        return value

    @field_validator("reason")
    @classmethod
    def require_non_empty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must be non-empty")
        return value


class ValidationAdjudicator(Protocol):
    def adjudicate(self, validation: FieldValidation) -> LLMValidationVerdict:
        ...


class OpenAIValidationAdjudicator:
    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.non_vision_model
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def adjudicate(self, validation: FieldValidation) -> LLMValidationVerdict:
        prompt = (
            "You adjudicate one ambiguous trade-document validation result.\n"
            "Return MATCH only if found and expected are the same business value.\n"
            "Return MISMATCH if they are materially different.\n"
            "Return UNCERTAIN if a human should review.\n\n"
            f"Field: {validation.field_name}\n"
            f"Found: {validation.found_value}\n"
            f"Expected: {validation.expected_value}\n"
            f"Rule: {validation.expected_rule}\n"
            "Output one status plus a one-sentence reason."
        )
        try:
            response = self._client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You are a strict trade-document validation adjudicator. "
                            "Do not invent missing information."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                text_format=LLMValidationVerdict,
            )
        except OpenAIError as exc:
            raise ValidatorError(f"LLM validation adjudication failed: {exc}") from exc

        if response.output_parsed is None:
            raise ValidatorError("LLM validation adjudication returned no parsed output")
        return response.output_parsed


class ValidatorAgent:
    def __init__(
        self,
        *,
        adjudicator: ValidationAdjudicator | None = None,
    ) -> None:
        self.adjudicator = adjudicator or OpenAIValidationAdjudicator()

    def validate(
        self,
        extraction: ExtractionResult,
        *,
        customer_id: str,
        rules: CustomerRuleSet | None = None,
    ) -> ValidationResult:
        rule_set = rules or load_rules(customer_id)
        field_results = apply_rules(extraction, rule_set)
        adjudicated_count = 0

        merged_results: list[FieldValidation] = []
        for result in field_results:
            if result.status != FieldValidationStatus.UNCERTAIN:
                merged_results.append(result)
                continue

            verdict = self.adjudicator.adjudicate(result)
            adjudicated_count += 1
            merged_results.append(
                result.model_copy(
                    update={
                        "status": verdict.status,
                        "reason": f"{result.reason} LLM adjudication: {verdict.reason}",
                    }
                )
            )

        return ValidationResult(
            extraction_id=extraction.document_id,
            customer_id=customer_id,
            rule_set_version=rule_set.version,
            field_results=merged_results,
            overall_status=calculate_overall_status(merged_results),
            validator_confidence=calculate_validator_confidence(
                adjudicated_count=adjudicated_count,
                total_count=len(merged_results),
            ),
        )


def calculate_overall_status(results: list[FieldValidation]) -> ValidationOverallStatus:
    statuses = {result.status for result in results}
    if statuses & {FieldValidationStatus.MISMATCH, FieldValidationStatus.MISSING}:
        return ValidationOverallStatus.FAILED
    if FieldValidationStatus.UNCERTAIN in statuses:
        return ValidationOverallStatus.NEEDS_REVIEW
    return ValidationOverallStatus.PASSED


def calculate_validator_confidence(*, adjudicated_count: int, total_count: int) -> float:
    if total_count == 0 or adjudicated_count == 0:
        return 0.95
    if adjudicated_count <= 2:
        return 0.75
    if adjudicated_count > total_count / 2:
        return 0.4
    return 0.6
