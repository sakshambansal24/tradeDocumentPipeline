from io import BytesIO

from langgraph.checkpoint.memory import MemorySaver
from PIL import Image, ImageDraw

from nova.agents.router import RouterAgent
from nova.agents.validator import ValidatorAgent
from nova.ingestion import DocumentLoader
from nova.orchestration import PipelineGraphDependencies, PipelineRunner
from nova.prompts.extractor import REQUIRED_FIELDS
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.pipeline import PipelineRunStatus, StageName, StageStatus
from nova.schemas.validation import (
    FieldValidation,
    FieldValidationStatus,
    ValidationOverallStatus,
    ValidationResult,
)


class FakeTracer:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def emit(self, stage: str, event_type: str, payload: dict) -> None:
        self.events.append((stage, event_type))


class CountingExtractor:
    def __init__(self, extraction: ExtractionResult) -> None:
        self.extraction = extraction
        self.calls = 0

    def extract(self, document) -> ExtractionResult:
        self.calls += 1
        return self.extraction


class FailingValidator:
    def validate(self, extraction: ExtractionResult, *, customer_id: str) -> ValidationResult:
        raise RuntimeError("simulated validate crash")


class WorkingValidator:
    def validate(self, extraction: ExtractionResult, *, customer_id: str) -> ValidationResult:
        fields = [
            FieldValidation(
                field_name=field_name,
                status=FieldValidationStatus.MATCH,
                found_value=field.value,
                expected_value=field.value,
                expected_rule="test_rule",
                reason="Recovered validation matched.",
                extraction_confidence=field.confidence,
            )
            for field_name, field in extraction.fields.items()
        ]
        return ValidationResult(
            extraction_id=extraction.document_id,
            customer_id=customer_id,
            rule_set_version="test",
            field_results=fields,
            overall_status=ValidationOverallStatus.PASSED,
            validator_confidence=0.95,
        )


def test_pipeline_happy_path_runs_loaded_document_through_graph() -> None:
    loaded_document = _loaded_document_from_image()
    tracer = FakeTracer()
    extraction = _acme_extraction(loaded_document.doc_id)
    dependencies = PipelineGraphDependencies(
        extractor=CountingExtractor(extraction),
        validator=ValidatorAgent(),
        router=RouterAgent(),
        tracer=tracer,
        persist_state=lambda state: None,
    )

    run = PipelineRunner(dependencies=dependencies, tracer=tracer).run(
        loaded_document,
        customer_id="acme_corp",
    )

    assert run.status == PipelineRunStatus.COMPLETED
    assert run.document_id == loaded_document.doc_id
    assert run.cost_total_usd == extraction.cost_usd
    assert [stage.stage for stage in run.stages] == [
        StageName.INGESTION,
        StageName.EXTRACTION,
        StageName.VALIDATION,
        StageName.ROUTING,
        StageName.STORAGE,
    ]
    assert all(stage.status == StageStatus.COMPLETED for stage in run.stages)
    assert ("EXTRACTION", "completed") in tracer.events


def test_pipeline_resumes_after_validate_crash_without_rerunning_extract() -> None:
    loaded_document = _loaded_document_from_image()
    tracer = FakeTracer()
    checkpointer = MemorySaver()
    extraction = _acme_extraction(loaded_document.doc_id)
    extractor = CountingExtractor(extraction)
    failing_runner = PipelineRunner(
        dependencies=PipelineGraphDependencies(
            extractor=extractor,
            validator=FailingValidator(),
            router=RouterAgent(),
            tracer=tracer,
            persist_state=lambda state: None,
        ),
        checkpointer=checkpointer,
        tracer=tracer,
    )

    failed_run = failing_runner.run(loaded_document, customer_id="acme_corp")

    assert failed_run.status == PipelineRunStatus.FAILED
    assert extractor.calls == 1
    assert failed_run.stages[-1].stage == StageName.VALIDATION
    assert failed_run.stages[-1].status == StageStatus.FAILED

    recovered_runner = PipelineRunner(
        dependencies=PipelineGraphDependencies(
            extractor=extractor,
            validator=WorkingValidator(),
            router=RouterAgent(),
            tracer=tracer,
            persist_state=lambda state: None,
        ),
        checkpointer=checkpointer,
        tracer=tracer,
    )

    recovered_run = recovered_runner.resume(failed_run.trace_id)

    assert recovered_run.status == PipelineRunStatus.COMPLETED
    assert extractor.calls == 1
    assert recovered_run.stages[-1].stage == StageName.STORAGE


def _loaded_document_from_image():
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 100), "Commercial Invoice", fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return DocumentLoader().load(
        buffer.getvalue(),
        content_type="image/png",
        source_filename="doc.png",
    )


def _acme_extraction(document_id: str) -> ExtractionResult:
    values = {
        "consignee_name": "ACME Corporation Pvt Ltd",
        "hs_code": "090121",
        "port_of_loading": "Shanghai",
        "port_of_discharge": "Rotterdam",
        "incoterms": "CIF",
        "description_of_goods": "Roasted coffee beans",
        "gross_weight": "1200 KG",
        "invoice_number": "INV-001",
    }
    assert set(values) == set(REQUIRED_FIELDS)
    return ExtractionResult(
        document_id=document_id,
        document_type=DocumentType.INVOICE,
        fields={
            field_name: ExtractedField(
                name=field_name,
                value=value,
                confidence=0.95,
                source_page=1,
                source_snippet=value,
                reasoning="Test extraction.",
                is_present=True,
            )
            for field_name, value in values.items()
        },
        model_used="test-model",
        latency_ms=10,
        cost_usd=0.02,
        raw_response_id="test-response",
    )
