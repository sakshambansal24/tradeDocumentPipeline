from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from nova.agents.cross_validator import CROSS_VALIDATED_FIELDS, CrossValidatorAgent
from nova.agents.shipment_router import ShipmentRouter, ShipmentRouterError
from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.pipeline import PipelineRun, PipelineRunStatus
from nova.schemas.shipment import CrossFieldStatus
from nova.storage import init_db
from nova.trigger import IncomingEmail, ShipmentPipeline


def test_consistent_shipment_marks_overall_consistent() -> None:
    result = CrossValidatorAgent().validate(
        [
            _run(DocumentType.BOL, consignee_name="ACME Corp", hs_code="1234"),
            _run(DocumentType.INVOICE, consignee_name=" acme corp ", hs_code="1234"),
        ]
    )

    assert result.overall_consistent is True
    fields_by_name = {field.field_name: field for field in result.checked_fields}
    assert fields_by_name["consignee_name"].status == CrossFieldStatus.CONSISTENT
    assert fields_by_name["hs_code"].status == CrossFieldStatus.CONSISTENT


def test_inconsistent_shipment_populates_values_by_doc() -> None:
    result = CrossValidatorAgent().validate(
        [
            _run(DocumentType.BOL, consignee_name="ACME Corporation Pvt Ltd"),
            _run(DocumentType.INVOICE, consignee_name="ACME Corp Ltd."),
        ]
    )

    assert result.overall_consistent is False
    consignee = next(
        field for field in result.checked_fields if field.field_name == "consignee_name"
    )
    assert consignee.status == CrossFieldStatus.INCONSISTENT
    assert consignee.values_by_doc == {
        "BOL": "ACME Corporation Pvt Ltd",
        "INVOICE": "ACME Corp Ltd.",
    }
    assert "INVOICE" in consignee.reason


def test_single_doc_is_insufficient_data_not_failure() -> None:
    result = CrossValidatorAgent().validate(
        [
            _run(
                DocumentType.BOL,
                consignee_name="ACME Corp",
                hs_code="1234",
                gross_weight="1000 KG",
                incoterms="FOB",
            )
        ]
    )

    assert result.overall_consistent is True
    assert {field.field_name for field in result.checked_fields} == set(CROSS_VALIDATED_FIELDS)
    assert all(
        field.status == CrossFieldStatus.INSUFFICIENT_DATA for field in result.checked_fields
    )


def test_cross_validation_failure_overrides_doc_approval_to_amend(tmp_path) -> None:
    session_factory = init_db(f"sqlite:///{tmp_path / 'cross-validator.db'}")
    pipeline = ShipmentPipeline(
        runner=SequentialRunner(
            [
                _run(DocumentType.BOL, consignee_name="ACME Corporation Pvt Ltd"),
                _run(DocumentType.INVOICE, consignee_name="ACME Corp Ltd."),
            ]
        ),
        storage_session_factory=session_factory,
        document_loader=NoopDocumentLoader(),
        shipment_router=ShipmentRouter(draft_writer=FailingDraftWriter()),
    )

    shipment = pipeline.process(
        IncomingEmail(
            email_id="email-cross-001",
            sender="supplier@example.com",
            subject="Shipment docs - ACME Corp",
            customer_id="acme_corp",
            attachments=[
                {"filename": "bol.pdf", "path": str(tmp_path / "bol.pdf")},
                {"filename": "invoice.pdf", "path": str(tmp_path / "invoice.pdf")},
            ],
        )
    )

    assert shipment.overall_decision is not None
    assert shipment.overall_decision.decision == DecisionType.AMEND
    assert shipment.draft_reply is not None
    assert "CROSS-DOCUMENT INCONSISTENCIES:" in shipment.draft_reply
    assert "BOL shows ACME Corporation Pvt Ltd" in shipment.draft_reply
    assert "INVOICE shows ACME Corp Ltd." in shipment.draft_reply


class SequentialRunner:
    def __init__(self, runs: list[PipelineRun]) -> None:
        self.runs = runs
        self.index = 0

    def run(self, loaded_document, customer_id: str) -> PipelineRun:
        run = self.runs[self.index]
        self.index += 1
        return run


class NoopDocumentLoader:
    def load(self, source: Path, *, source_filename: str | None = None):
        return SimpleNamespace(
            doc_id=source_filename or source.name,
            source_filename=source_filename,
        )


class FailingDraftWriter:
    def draft(self, **kwargs) -> str:
        raise ShipmentRouterError("force deterministic fallback")


def _run(document_type: DocumentType, **field_values: str) -> PipelineRun:
    now = datetime.now(UTC)
    document_id = f"doc-{document_type.value}-{uuid4()}"
    run_id = uuid4()
    return PipelineRun(
        run_id=run_id,
        document_id=document_id,
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
                name: _field(name, value)
                for name, value in field_values.items()
            },
            model_used="test-model",
            latency_ms=1,
            cost_usd=0.0,
            raw_response_id=f"response-{run_id}",
        ),
        router_decision=RouterDecision(
            decision=DecisionType.AUTO_APPROVE,
            reasoning="Document passed validation.",
            risk_flags=[],
        ),
    )


def _field(name: str, value: str) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        confidence=0.99,
        source_page=1,
        source_snippet=value,
        reasoning="Found in test fixture.",
        is_present=True,
    )
