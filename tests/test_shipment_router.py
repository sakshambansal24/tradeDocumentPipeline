from datetime import UTC, datetime
from uuid import uuid4

from nova.agents.shipment_router import FIELD_TOKEN_PATTERN, ShipmentRouter, ShipmentRouterError
from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.pipeline import PipelineRun, PipelineRunStatus
from nova.schemas.shipment import (
    CrossFieldMatch,
    CrossFieldStatus,
    CrossValidationResult,
    Shipment,
    ShipmentStatus,
)
from nova.schemas.validation import (
    FieldValidation,
    FieldValidationStatus,
    ValidationOverallStatus,
    ValidationResult,
)


def test_all_docs_pass_and_cross_validation_passes_auto_approve_with_draft() -> None:
    shipment = _shipment(
        [
            _run(
                DocumentType.BOL,
                validation_fields=[
                    _validation_field("consignee_name", FieldValidationStatus.MATCH),
                    _validation_field("hs_code", FieldValidationStatus.MATCH),
                ],
            )
        ],
        cross_consistent=True,
    )

    decision, draft = ShipmentRouter(draft_writer=FailingDraftWriter()).decide(shipment)

    assert decision.decision == DecisionType.AUTO_APPROVE
    assert decision.drafted_message is not None
    assert "No document amendments are required" in draft
    assert "Subject: Re: Shipment docs - ACME Corp" in draft


def test_cross_validation_failure_amends_with_cross_doc_section() -> None:
    shipment = _shipment(
        [
            _run(
                DocumentType.BOL,
                validation_fields=[
                    _validation_field("consignee_name", FieldValidationStatus.MATCH)
                ],
            ),
            _run(
                DocumentType.INVOICE,
                validation_fields=[
                    _validation_field("consignee_name", FieldValidationStatus.MATCH)
                ],
            ),
        ],
        cross_consistent=False,
    )

    decision, draft = ShipmentRouter(draft_writer=FailingDraftWriter()).decide(shipment)

    assert decision.decision == DecisionType.AMEND
    assert "CROSS-DOCUMENT INCONSISTENCIES:" in draft
    assert "consignee_name" in draft
    assert "BOL shows ACME Corporation Pvt Ltd" in draft
    assert "INVOICE shows ACME Corp Ltd." in draft


def test_individual_doc_critical_mismatch_amends_with_specific_field() -> None:
    shipment = _shipment(
        [
            _run(
                DocumentType.INVOICE,
                validation_fields=[
                    _validation_field(
                        "hs_code",
                        FieldValidationStatus.MISMATCH,
                        found_value="123456",
                        expected_value="12345678",
                    )
                ],
            )
        ],
        cross_consistent=True,
    )

    decision, draft = ShipmentRouter(draft_writer=FailingDraftWriter()).decide(shipment)

    assert decision.decision == DecisionType.AMEND
    assert "PER-DOCUMENT ISSUES:" in draft
    assert "INVOICE: hs_code" in draft
    assert "Found: 123456" in draft


def test_draft_field_names_are_grounded_in_actual_field_results() -> None:
    shipment = _shipment(
        [
            _run(
                DocumentType.INVOICE,
                validation_fields=[
                    _validation_field(
                        "hs_code",
                        FieldValidationStatus.MISMATCH,
                        found_value="123456",
                        expected_value="12345678",
                    ),
                    _validation_field("invoice_number", FieldValidationStatus.MATCH),
                ],
            )
        ],
        cross_consistent=True,
    )

    _, draft = ShipmentRouter(draft_writer=FailingDraftWriter()).decide(shipment)

    actual_fields = {
        field.field_name
        for run in shipment.document_runs
        for field in run.validation_result.field_results
    }
    referenced_fields = {
        token
        for token in FIELD_TOKEN_PATTERN.findall(draft)
        if token not in {shipment.customer_id, shipment.email_id}
    }
    assert referenced_fields <= actual_fields


def test_draft_allows_source_filename_document_labels() -> None:
    shipment = _shipment(
        [
            _run(
                DocumentType.INVOICE,
                source_filename="bill_of_lading_messy.pdf",
                validation_fields=[
                    _validation_field(
                        "gross_weight",
                        FieldValidationStatus.MISSING,
                        found_value=None,
                        expected_value="numeric_range:min_kg=100.0,max_kg=25000.0",
                        expected_rule="numeric_range:min_kg=100.0,max_kg=25000.0",
                    )
                ],
            )
        ],
        cross_consistent=True,
    )

    decision, draft = ShipmentRouter(draft_writer=FailingDraftWriter()).decide(shipment)

    assert decision.decision == DecisionType.AMEND
    assert "bill_of_lading_messy: gross_weight" in draft


class FailingDraftWriter:
    def draft(self, **kwargs) -> str:
        raise ShipmentRouterError("force deterministic fallback")


def _shipment(
    runs: list[PipelineRun],
    *,
    cross_consistent: bool,
) -> Shipment:
    shipment_id = uuid4()
    checked_fields = [
        CrossFieldMatch(
            field_name="consignee_name",
            values_by_doc={
                "BOL": "ACME Corporation Pvt Ltd",
                "INVOICE": "ACME Corp Ltd.",
            },
            status=(
                CrossFieldStatus.CONSISTENT
                if cross_consistent
                else CrossFieldStatus.INCONSISTENT
            ),
            reason="INVOICE value differs from BOL",
        )
    ]
    return Shipment(
        shipment_id=shipment_id,
        email_id="email-shipment-router-001",
        customer_id="acme_corp",
        triggered_by="supplier@example.com",
        subject="Shipment docs - ACME Corp",
        triggered_at=datetime.now(UTC),
        status=ShipmentStatus.PROCESSING,
        document_runs=runs,
        cross_validation_result=CrossValidationResult(
            shipment_id=shipment_id,
            checked_fields=checked_fields,
            overall_consistent=cross_consistent,
            checked_at=datetime.now(UTC),
        ),
    )


def _run(
    document_type: DocumentType,
    *,
    validation_fields: list[FieldValidation],
    source_filename: str | None = None,
) -> PipelineRun:
    now = datetime.now(UTC)
    run_id = uuid4()
    document_id = f"doc-{document_type.value}-{run_id}"
    return PipelineRun(
        run_id=run_id,
        document_id=document_id,
        source_filename=source_filename,
        customer_id="acme_corp",
        status=PipelineRunStatus.COMPLETED,
        stages=[],
        started_at=now,
        completed_at=now,
        cost_total_usd=0.0,
        trace_id=str(run_id),
        extraction_result=ExtractionResult(
            document_id=document_id,
            document_type=document_type,
            fields={
                "invoice_number": _extracted_field("invoice_number", "INV-2024-00123"),
                "consignee_name": _extracted_field(
                    "consignee_name",
                    "ACME Corporation Pvt Ltd",
                ),
                "hs_code": _extracted_field("hs_code", "123456"),
            },
            model_used="test-model",
            latency_ms=1,
            cost_usd=0.0,
            raw_response_id=f"response-{run_id}",
        ),
        validation_result=ValidationResult(
            extraction_id=f"response-{run_id}",
            customer_id="acme_corp",
            rule_set_version="test",
            field_results=validation_fields,
            overall_status=(
                ValidationOverallStatus.FAILED
                if any(
                    field.status
                    in {FieldValidationStatus.MISMATCH, FieldValidationStatus.MISSING}
                    for field in validation_fields
                )
                else ValidationOverallStatus.PASSED
            ),
            validator_confidence=0.99,
        ),
        router_decision=RouterDecision(
            decision=DecisionType.AUTO_APPROVE,
            reasoning="Document passed validation.",
            risk_flags=[],
        ),
    )


def _validation_field(
    field_name: str,
    status: FieldValidationStatus,
    *,
    found_value: str | None = "ACME Corporation Pvt Ltd",
    expected_value: str = "ACME Corporation Pvt Ltd",
    expected_rule: str | None = None,
) -> FieldValidation:
    return FieldValidation(
        field_name=field_name,
        status=status,
        found_value=found_value,
        expected_value=expected_value if status == FieldValidationStatus.MISMATCH else None,
        expected_rule=expected_rule or f"{field_name} must match customer rule",
        reason="Test validation result.",
        extraction_confidence=0.99,
    )


def _extracted_field(field_name: str, value: str) -> ExtractedField:
    return ExtractedField(
        name=field_name,
        value=value,
        confidence=0.99,
        source_page=1,
        source_snippet=value,
        reasoning="Found in test fixture.",
        is_present=True,
    )
