from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from nova.schemas import (
    DecisionType,
    DocumentType,
    ExtractedField,
    ExtractionResult,
    PipelineRun,
    PipelineRunStatus,
    RouterDecision,
    StageEvent,
    StageName,
    StageStatus,
)


def test_extracted_field_forbids_hallucinated_absent_value() -> None:
    with pytest.raises(ValidationError):
        ExtractedField(
            name="HS code",
            value="1234",
            confidence=0.8,
            source_page=1,
            source_snippet="",
            reasoning="Looks like an HS code.",
            is_present=False,
        )


def test_extraction_result_forbids_unexpected_fields() -> None:
    field = ExtractedField(
        name="invoice_number",
        value="INV-001",
        confidence=0.95,
        source_page=1,
        source_snippet="Invoice No: INV-001",
        reasoning="The label directly precedes the value.",
        is_present=True,
    )

    with pytest.raises(ValidationError):
        ExtractionResult(
            document_id="doc-1",
            document_type=DocumentType.INVOICE,
            fields={"invoice_number": field},
            model_used="gpt-4o",
            latency_ms=1200,
            cost_usd=0.02,
            raw_response_id="resp-1",
            unexpected="fail loud",
        )


def test_router_requires_drafted_message_for_amendment() -> None:
    with pytest.raises(ValidationError):
        RouterDecision(
            decision=DecisionType.AMEND,
            reasoning="HS code does not match the customer rule.",
            risk_flags=["hs_code_mismatch"],
        )


def test_pipeline_run_rejects_impossible_timing() -> None:
    started_at = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    completed_at = datetime(2026, 5, 30, 11, 59, tzinfo=UTC)

    stage = StageEvent(
        stage=StageName.EXTRACTION,
        status=StageStatus.COMPLETED,
        started_at=started_at,
        completed_at=started_at,
        latency_ms=100,
        cost_usd=0.01,
        trace_id="trace-1",
    )

    with pytest.raises(ValidationError):
        PipelineRun(
            run_id=uuid4(),
            document_id="doc-1",
            customer_id="customer-1",
            status=PipelineRunStatus.COMPLETED,
            stages=[stage],
            started_at=started_at,
            completed_at=completed_at,
            cost_total_usd=0.01,
            trace_id="trace-1",
        )
