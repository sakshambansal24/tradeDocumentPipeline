import re
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, field_validator

from nova.agents.decision_policy import CRITICAL_FIELDS
from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.extraction import ExtractedField
from nova.schemas.pipeline import PipelineRun
from nova.schemas.shipment import CrossFieldMatch, CrossFieldStatus, Shipment
from nova.schemas.validation import (
    FieldValidation,
    FieldValidationStatus,
    ValidationOverallStatus,
)
from nova.settings import get_settings

FIELD_TOKEN_PATTERN = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
ALLOWED_REFERENCE_TOKENS = {"customer_id", "email_id"}


class ShipmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str

    @field_validator("message")
    @classmethod
    def require_non_empty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be non-empty")
        return value


class ShipmentDraftWriter(Protocol):
    def draft(
        self,
        *,
        shipment: Shipment,
        decision: DecisionType,
        per_document_issues: list["DocumentIssue"],
        cross_document_issues: list[CrossFieldMatch],
    ) -> str:
        ...


class OpenAIShipmentDraftWriter:
    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.non_vision_model
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def draft(
        self,
        *,
        shipment: Shipment,
        decision: DecisionType,
        per_document_issues: list["DocumentIssue"],
        cross_document_issues: list[CrossFieldMatch],
    ) -> str:
        response = self._client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You write concise freight operations amendment emails. "
                        "Use only the supplied validation facts and field names."
                    ),
                },
                {
                    "role": "user",
                    "content": build_draft_prompt(
                        shipment=shipment,
                        decision=decision,
                        per_document_issues=per_document_issues,
                        cross_document_issues=cross_document_issues,
                    ),
                },
            ],
            text_format=ShipmentDraft,
        )
        if response.output_parsed is None:
            raise ShipmentRouterError("Shipment draft generation returned no parsed output")
        return response.output_parsed.message


class ShipmentRouterError(Exception):
    pass


class ShipmentRouter:
    def __init__(self, *, draft_writer: ShipmentDraftWriter | None = None) -> None:
        self.draft_writer = draft_writer

    def decide(self, shipment: Shipment) -> tuple[RouterDecision, str]:
        decision = choose_shipment_decision(shipment)
        risk_flags = collect_shipment_risk_flags(shipment)
        reasoning = build_shipment_reasoning(shipment, decision)

        per_document_issues = collect_per_document_issues(shipment, decision)
        cross_document_issues = collect_cross_document_issues(shipment)
        draft = self._draft_grounded_reply(
            shipment=shipment,
            decision=decision,
            per_document_issues=per_document_issues,
            cross_document_issues=cross_document_issues,
        )
        return (
            RouterDecision(
                decision=decision,
                reasoning=reasoning,
                drafted_message=draft,
                risk_flags=risk_flags,
            ),
            draft,
        )

    def _draft_grounded_reply(
        self,
        *,
        shipment: Shipment,
        decision: DecisionType,
        per_document_issues: list["DocumentIssue"],
        cross_document_issues: list[CrossFieldMatch],
    ) -> str:
        allowed_fields = allowed_draft_fields(shipment)
        allowed_references = {shipment.customer_id, shipment.email_id}
        if shipment.triggered_by is not None:
            allowed_references.add(shipment.triggered_by)
        allowed_references.update(document_label_for_run(run) for run in shipment.document_runs)
        allowed_references.update(allowed_rule_tokens(per_document_issues))

        draft = build_deterministic_shipment_reply(
            shipment=shipment,
            decision=decision,
            per_document_issues=per_document_issues,
            cross_document_issues=cross_document_issues,
        )
        if not draft_is_grounded(
            draft,
            allowed_fields,
            allowed_reference_tokens=allowed_references,
        ):
            raise ShipmentRouterError("Generated shipment draft referenced ungrounded fields")
        return draft


class DocumentIssue(BaseModel):
    document_label: str
    field: FieldValidation


def choose_shipment_decision(shipment: Shipment) -> DecisionType:
    cross_validation = shipment.cross_validation_result
    if cross_validation is not None and not cross_validation.overall_consistent:
        return DecisionType.AMEND

    validations = [
        run.validation_result for run in shipment.document_runs if run.validation_result is not None
    ]
    if any(
        validation.overall_status == ValidationOverallStatus.FAILED
        for validation in validations
    ):
        return DecisionType.AMEND

    field_results = [field for validation in validations for field in validation.field_results]
    has_critical_mismatch = any(
        field.field_name in CRITICAL_FIELDS and field.status == FieldValidationStatus.MISMATCH
        for field in field_results
    )
    if has_critical_mismatch:
        return DecisionType.AMEND

    if any(field.status == FieldValidationStatus.UNCERTAIN for field in field_results):
        return DecisionType.HUMAN_REVIEW

    if any(
        field.status in {FieldValidationStatus.MISMATCH, FieldValidationStatus.MISSING}
        for field in field_results
    ):
        return DecisionType.HUMAN_REVIEW

    return DecisionType.AUTO_APPROVE


def collect_shipment_risk_flags(shipment: Shipment) -> list[str]:
    flags: list[str] = []
    if (
        shipment.cross_validation_result is not None
        and not shipment.cross_validation_result.overall_consistent
    ):
        flags.append("cross_document_inconsistency")
    if any(
        run.validation_result is not None
        and run.validation_result.overall_status == ValidationOverallStatus.FAILED
        for run in shipment.document_runs
    ):
        flags.append("document_validation_failed")
    if any(
        issue.field.status == FieldValidationStatus.UNCERTAIN
        for issue in collect_per_document_issues(shipment, DecisionType.HUMAN_REVIEW)
    ):
        flags.append("uncertain_fields_present")
    return flags


def build_shipment_reasoning(shipment: Shipment, decision: DecisionType) -> str:
    match decision:
        case DecisionType.AMEND:
            if (
                shipment.cross_validation_result is not None
                and not shipment.cross_validation_result.overall_consistent
            ):
                return "Cross-document inconsistencies require supplier amendment."
            return "Shipment contains document-level validation failures requiring amendment."
        case DecisionType.HUMAN_REVIEW:
            return "Shipment contains uncertain or non-critical validation issues for CG review."
        case DecisionType.AUTO_APPROVE:
            return "All documents passed validation and cross-document checks."


def collect_per_document_issues(
    shipment: Shipment,
    decision: DecisionType,
) -> list[DocumentIssue]:
    issue_statuses = {
        FieldValidationStatus.MISMATCH,
        FieldValidationStatus.MISSING,
    }
    if decision == DecisionType.HUMAN_REVIEW:
        issue_statuses.add(FieldValidationStatus.UNCERTAIN)

    issues: list[DocumentIssue] = []
    for run in shipment.document_runs:
        if run.validation_result is None:
            continue
        document_label = document_label_for_run(run)
        for field in run.validation_result.field_results:
            if field.status in issue_statuses:
                issues.append(DocumentIssue(document_label=document_label, field=field))
    return issues


def collect_cross_document_issues(shipment: Shipment) -> list[CrossFieldMatch]:
    if shipment.cross_validation_result is None:
        return []
    return [
        field
        for field in shipment.cross_validation_result.checked_fields
        if field.status == CrossFieldStatus.INCONSISTENT
    ]


def build_draft_prompt(
    *,
    shipment: Shipment,
    decision: DecisionType,
    per_document_issues: list[DocumentIssue],
    cross_document_issues: list[CrossFieldMatch],
) -> str:
    return f"""Draft a freight amendment/review email using only these facts.

Decision: {decision.value}
Customer: {shipment.customer_id}
Supplier greeting: {supplier_name_for(shipment)}
Invoice number: {invoice_number_for(shipment) or "not available"}

Required structure:
Subject: {reply_subject_for(shipment)}
Dear [supplier],
Thank you for submitting the shipment documents for {shipment.customer_id}.
If decision is AUTO_APPROVE, say the document set is approved.
If decision is AMEND or HUMAN_REVIEW, list the issues below.
PER-DOCUMENT ISSUES:
{format_per_document_facts(per_document_issues)}
CROSS-DOCUMENT INCONSISTENCIES:
{format_cross_document_facts(cross_document_issues)}
Please correct and resubmit all affected documents.
Regards,
CG Team

Only mention field names present in the facts above."""


def build_deterministic_shipment_reply(
    *,
    shipment: Shipment,
    decision: DecisionType,
    per_document_issues: list[DocumentIssue],
    cross_document_issues: list[CrossFieldMatch],
) -> str:
    lines = [
        f"Subject: {reply_subject_for(shipment)}",
        "",
        f"Dear {supplier_name_for(shipment)},",
        "",
    ]

    if decision == DecisionType.AUTO_APPROVE:
        lines.extend(
            [
                (
                    f"Thank you for submitting the shipment documents for {shipment.customer_id}. "
                    "Upon review, the document set is aligned with the customer requirements "
                    "and is ready for CG approval."
                ),
                "",
                "No document amendments are required at this stage.",
            ]
        )
    else:
        lines.extend(
            [
                (
                    f"Thank you for submitting the shipment documents for {shipment.customer_id}. "
                    "Upon review, we have identified the following issues that require correction "
                    "before we can proceed:"
                ),
                "",
                "PER-DOCUMENT ISSUES:",
            ]
        )

    if per_document_issues:
        for issue in per_document_issues:
            lines.append(
                f"{issue.document_label}: {issue.field.field_name} — "
                f"Found: {issue.field.found_value}. "
                f"Expected: {issue.field.expected_rule}."
            )
    else:
        if decision != DecisionType.AUTO_APPROVE:
            lines.append("None.")

    if cross_document_issues:
        lines.extend(["", "CROSS-DOCUMENT INCONSISTENCIES:"])
        for issue in cross_document_issues:
            values = ", ".join(
                f"{doc_label} shows {value}" for doc_label, value in issue.values_by_doc.items()
            )
            lines.append(
                f"{issue.field_name} — {values}. Reason: {issue.reason}. "
                "Please align across all documents."
            )

    if decision == DecisionType.AUTO_APPROVE:
        lines.extend(["", "Regards,", "CG Team"])
    else:
        lines.extend(
            ["", "Please correct and resubmit all affected documents.", "", "Regards,", "CG Team"]
        )
    return "\n".join(lines)


def format_per_document_facts(issues: list[DocumentIssue]) -> str:
    if not issues:
        return "None."
    return "\n".join(
        (
            f"- {issue.document_label}: {issue.field.field_name}; "
            f"found={issue.field.found_value!r}; expected_rule={issue.field.expected_rule!r}"
        )
        for issue in issues
    )


def format_cross_document_facts(issues: list[CrossFieldMatch]) -> str:
    if not issues:
        return "None."
    lines: list[str] = []
    for issue in issues:
        values = ", ".join(
            f"{doc_label}={value!r}" for doc_label, value in issue.values_by_doc.items()
        )
        lines.append(f"- {issue.field_name}: {values}")
    return "\n".join(lines)


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


def allowed_draft_fields(shipment: Shipment) -> set[str]:
    fields = {
        field.field_name
        for run in shipment.document_runs
        if run.validation_result is not None
        for field in run.validation_result.field_results
    }
    if shipment.cross_validation_result is not None:
        fields.update(field.field_name for field in shipment.cross_validation_result.checked_fields)
    return fields


def allowed_rule_tokens(issues: list[DocumentIssue]) -> set[str]:
    tokens: set[str] = set()
    for issue in issues:
        tokens.update(FIELD_TOKEN_PATTERN.findall(issue.field.expected_rule))
        if issue.field.expected_value:
            tokens.update(FIELD_TOKEN_PATTERN.findall(issue.field.expected_value))
        if issue.field.found_value:
            tokens.update(FIELD_TOKEN_PATTERN.findall(issue.field.found_value))
    return tokens


def invoice_number_for(shipment: Shipment) -> str | None:
    for run in shipment.document_runs:
        field = extracted_field_for(run, "invoice_number")
        if field is not None and field.value:
            return field.value
    return None


def supplier_name_for(shipment: Shipment) -> str:
    if shipment.triggered_by is None:
        return "Supplier"
    local_part = shipment.triggered_by.split("@", maxsplit=1)[0]
    cleaned = " ".join(part for part in re.split(r"[._+-]+", local_part) if part)
    return cleaned.title() if cleaned else "Supplier"


def reply_subject_for(shipment: Shipment) -> str:
    if shipment.subject:
        subject = shipment.subject.strip()
        return subject if subject.lower().startswith("re:") else f"Re: {subject}"

    invoice_number = invoice_number_for(shipment)
    subject_suffix = f" — {invoice_number}" if invoice_number else ""
    return f"Re: Shipment documents — {shipment.customer_id}{subject_suffix}"


def document_label_for_run(run: PipelineRun) -> str:
    if run.source_filename:
        return Path(run.source_filename).stem
    if run.extraction_result is not None:
        return run.extraction_result.document_type.value
    return run.document_id


def extracted_field_for(run: PipelineRun, field_name: str) -> ExtractedField | None:
    if run.extraction_result is None:
        return None
    field = run.extraction_result.fields.get(field_name)
    if field is None or not field.is_present:
        return None
    return field
