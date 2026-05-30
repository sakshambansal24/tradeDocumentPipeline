"""Router / Decision Agent.

The decision path is deterministic and policy-driven: the LLM never chooses whether to
approve, review, or amend. That keeps routing consistent, auditable, and cheap. The LLM is
used only as a writer for AMEND drafts, where human-facing tone and clarity matter. Drafts are
grounding-checked against `ValidationResult.field_results`; if a generated draft mentions a
field outside the validation set, it gets one stricter retry before deterministic fallback.
"""

import re
from typing import Protocol

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, field_validator

from nova.agents.decision_policy import CRITICAL_FIELDS
from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.validation import FieldValidation, FieldValidationStatus, ValidationResult
from nova.settings import get_settings

VALIDATOR_LOW_CONFIDENCE_THRESHOLD = 0.5
LOW_EXTRACTION_CONFIDENCE_THRESHOLD = 0.7
FIELD_TOKEN_PATTERN = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
ALLOWED_REFERENCE_TOKENS = {"customer_id", "document_id"}


class AmendmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str

    @field_validator("message")
    @classmethod
    def require_non_empty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be non-empty")
        return value


class AmendmentDrafter(Protocol):
    def draft(self, *, validation: ValidationResult, discrepancies: list[FieldValidation]) -> str:
        ...


class OpenAIAmendmentDrafter:
    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.non_vision_model
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def draft(self, *, validation: ValidationResult, discrepancies: list[FieldValidation]) -> str:
        prompt = build_draft_prompt(validation=validation, discrepancies=discrepancies)
        try:
            response = self._client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You write concise, professional amendment requests for suppliers. "
                            "Use only the validation fields provided."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                text_format=AmendmentDraft,
            )
        except OpenAIError as exc:
            raise RouterError(f"Amendment draft generation failed: {exc}") from exc

        if response.output_parsed is None:
            raise RouterError("Amendment draft generation returned no parsed output")
        return response.output_parsed.message


class RouterError(Exception):
    pass


class RouterAgent:
    def __init__(self, *, drafter: AmendmentDrafter | None = None) -> None:
        self.drafter = drafter

    def decide(self, validation: ValidationResult) -> RouterDecision:
        decision = choose_decision(validation)
        risk_flags = collect_risk_flags(validation)
        reasoning = build_reasoning(validation, decision)

        if decision != DecisionType.AMEND:
            return RouterDecision(
                decision=decision,
                reasoning=reasoning,
                drafted_message=None,
                risk_flags=risk_flags,
            )

        discrepancies = amendment_discrepancies(validation)
        drafted_message = self._draft_grounded_amendment(validation, discrepancies)
        return RouterDecision(
            decision=decision,
            reasoning=reasoning,
            drafted_message=drafted_message,
            risk_flags=risk_flags,
        )

    def _draft_grounded_amendment(
        self,
        validation: ValidationResult,
        discrepancies: list[FieldValidation],
    ) -> str:
        allowed_fields = {field.field_name for field in validation.field_results}
        drafter = self.drafter or OpenAIAmendmentDrafter()
        allowed_reference_tokens = {validation.customer_id, validation.extraction_id}
        for _ in range(2):
            message = drafter.draft(validation=validation, discrepancies=discrepancies)
            if draft_is_grounded(
                message,
                allowed_fields,
                allowed_reference_tokens=allowed_reference_tokens,
            ):
                return message
        return build_deterministic_amendment_message(validation, discrepancies)


def choose_decision(validation: ValidationResult) -> DecisionType:
    statuses = [field.status for field in validation.field_results]
    critical_failures = [
        field
        for field in validation.field_results
        if field.field_name in CRITICAL_FIELDS
        and field.status in {FieldValidationStatus.MISMATCH, FieldValidationStatus.MISSING}
    ]
    any_failures = any(
        status in {FieldValidationStatus.MISMATCH, FieldValidationStatus.MISSING}
        for status in statuses
    )

    if statuses and all(status == FieldValidationStatus.MATCH for status in statuses):
        return DecisionType.AUTO_APPROVE
    if critical_failures:
        return DecisionType.AMEND
    if FieldValidationStatus.UNCERTAIN in statuses and not any_failures:
        return DecisionType.HUMAN_REVIEW
    return DecisionType.HUMAN_REVIEW


def build_reasoning(validation: ValidationResult, decision: DecisionType) -> str:
    field_count = len(validation.field_results)
    confidence_label = confidence_label_for(validation.validator_confidence)
    critical_failures = [
        field.field_name
        for field in validation.field_results
        if field.field_name in CRITICAL_FIELDS
        and field.status in {FieldValidationStatus.MISMATCH, FieldValidationStatus.MISSING}
    ]
    uncertain_fields = [
        field.field_name
        for field in validation.field_results
        if field.status == FieldValidationStatus.UNCERTAIN
    ]
    non_critical_failures = [
        field.field_name
        for field in validation.field_results
        if field.field_name not in CRITICAL_FIELDS
        and field.status in {FieldValidationStatus.MISMATCH, FieldValidationStatus.MISSING}
    ]

    match decision:
        case DecisionType.AUTO_APPROVE:
            return (
                f"All {field_count} required fields matched customer rules. "
                f"Validator confidence: {confidence_label}."
            )
        case DecisionType.AMEND:
            names = ", ".join(critical_failures)
            return (
                f"Critical mismatches detected on {len(critical_failures)} fields ({names}). "
                "See drafted amendment for details."
            )
        case DecisionType.HUMAN_REVIEW:
            if uncertain_fields and not non_critical_failures:
                names = ", ".join(uncertain_fields)
                return (
                    f"{len(uncertain_fields)} fields flagged as UNCERTAIN ({names}). "
                    "Manual review recommended."
                )
            names = ", ".join(non_critical_failures or uncertain_fields)
            return (
                f"Non-critical validation issues require review ({names}). "
                "Manual review recommended before supplier amendment."
            )


def collect_risk_flags(validation: ValidationResult) -> list[str]:
    flags: list[str] = []
    if validation.validator_confidence < VALIDATOR_LOW_CONFIDENCE_THRESHOLD:
        flags.append("validator_confidence_low")

    for field in validation.field_results:
        if (
            field.status == FieldValidationStatus.MATCH
            and field.extraction_confidence is not None
            and field.extraction_confidence < LOW_EXTRACTION_CONFIDENCE_THRESHOLD
        ):
            flags.append(f"low_extraction_confidence_on_{field.field_name}")

    if any(field.status == FieldValidationStatus.UNCERTAIN for field in validation.field_results):
        flags.append("uncertain_fields_present")

    return flags


def amendment_discrepancies(validation: ValidationResult) -> list[FieldValidation]:
    return [
        field
        for field in validation.field_results
        if field.status in {FieldValidationStatus.MISMATCH, FieldValidationStatus.MISSING}
    ]


def build_draft_prompt(
    *,
    validation: ValidationResult,
    discrepancies: list[FieldValidation],
) -> str:
    invoice_number = next(
        (
            field.found_value
            for field in validation.field_results
            if field.field_name == "invoice_number" and field.found_value
        ),
        "not available",
    )
    discrepancy_lines = "\n".join(
        (
            f"- {field.field_name}: found={field.found_value!r}, "
            f"expected={field.expected_value!r}, rule={field.expected_rule!r}"
        )
        for field in discrepancies
    )
    allowed_fields = ", ".join(field.field_name for field in validation.field_results)
    return f"""Draft a supplier amendment request.

Customer ID: {validation.customer_id}
Document ID: {validation.extraction_id}
Invoice number: {invoice_number}

Discrepancies:
{discrepancy_lines}

Requirements:
- Polite, professional tone.
- Include every discrepancy as "Found X, expected Y" with the rule reference.
- Ask: "Please correct and resubmit with the above changes."
- You may reference ONLY these validation field names: {allowed_fields}.
"""


def draft_is_grounded(
    message: str,
    allowed_fields: set[str],
    *,
    allowed_reference_tokens: set[str] | None = None,
) -> bool:
    allowed_references = ALLOWED_REFERENCE_TOKENS | (allowed_reference_tokens or set())
    referenced_tokens = {
        token
        for token in FIELD_TOKEN_PATTERN.findall(message)
        if token not in allowed_references
    }
    return referenced_tokens <= allowed_fields


def build_deterministic_amendment_message(
    validation: ValidationResult,
    discrepancies: list[FieldValidation],
) -> str:
    invoice_number = next(
        (
            field.found_value
            for field in validation.field_results
            if field.field_name == "invoice_number" and field.found_value
        ),
        "not available",
    )
    lines = [
        "Dear Supplier,",
        "",
        "During document validation, we found discrepancies that require amendment.",
        f"Customer ID: {validation.customer_id}",
        f"Document ID: {validation.extraction_id}",
        f"Invoice number: {invoice_number}",
        "",
        "Please review the following:",
    ]
    for field in discrepancies:
        lines.append(
            f"- {field.field_name}: Found {field.found_value!r}, expected "
            f"{field.expected_value!r}. Rule reference: {field.expected_rule}."
        )
    lines.extend(["", "Please correct and resubmit with the above changes."])
    return "\n".join(lines)


def confidence_label_for(value: float) -> str:
    if value >= 0.85:
        return "high"
    if value >= 0.6:
        return "moderate"
    return "low"
