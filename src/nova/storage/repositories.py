from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.extraction import ExtractionResult
from nova.schemas.ingestion import LoadedDocument
from nova.schemas.pipeline import PipelineRun, PipelineRunStatus, StageEvent
from nova.schemas.validation import ValidationResult
from nova.storage.models import Decision, Document, Extraction, PipelineRunRecord, Validation

if TYPE_CHECKING:
    from nova.orchestration.state import PipelineState


@dataclass(frozen=True)
class RunFilters:
    customer_id: str | None = None
    decision: DecisionType | None = None
    status: PipelineRunStatus | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, loaded_document: LoadedDocument) -> Document:
        existing = self.session.get(Document, loaded_document.doc_id)
        if existing is not None:
            return existing

        document = Document(
            id=loaded_document.doc_id,
            filename=loaded_document.source_filename,
            content_type=infer_content_type(loaded_document.source_filename),
            source_hash=loaded_document.original_bytes_hash,
            page_count=loaded_document.page_count,
        )
        self.session.add(document)
        return document


class PipelineRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.documents = DocumentRepository(session)

    def save_run(
        self,
        run: PipelineRun,
        *,
        state: "PipelineState | None" = None,
    ) -> PipelineRunRecord:
        if state is not None and state.get("loaded_document") is not None:
            self.documents.save(state["loaded_document"])
            extraction_id = self._save_extraction(state)
            validation_id = self._save_validation(state, extraction_id=extraction_id)
            self._save_decision(state, validation_id=validation_id)
        elif self.session.get(Document, run.document_id) is None:
            self.session.add(
                Document(
                    id=run.document_id,
                    filename=None,
                    content_type="application/octet-stream",
                    source_hash=run.document_id,
                    page_count=0,
                )
            )

        existing = self.session.scalar(
            select(PipelineRunRecord).where(PipelineRunRecord.run_id == str(run.run_id))
        )
        decision = state.get("router_decision") if state is not None else None
        decision_value = decision.decision.value if decision is not None else None
        stage_history = [stage.model_dump(mode="json") for stage in run.stages]

        if existing is None:
            existing = PipelineRunRecord(run_id=str(run.run_id), document_id=run.document_id)
            self.session.add(existing)

        existing.customer_id = run.customer_id
        existing.status = run.status.value
        existing.decision = decision_value
        existing.decision_details = decision.model_dump(mode="json") if decision is not None else None
        existing.started_at = run.started_at
        existing.completed_at = run.completed_at
        existing.cost_total_usd = run.cost_total_usd
        existing.stage_history = stage_history
        existing.trace_id = run.trace_id
        return existing

    def get(self, run_id: str) -> PipelineRun:
        record = self.session.scalar(
            select(PipelineRunRecord).where(PipelineRunRecord.run_id == run_id)
        )
        if record is None:
            raise LookupError(f"Pipeline run not found: {run_id}")
        return pipeline_run_from_record(record)

    def list(self, filters: RunFilters) -> list[PipelineRun]:
        query: Select[tuple[PipelineRunRecord]] = select(PipelineRunRecord)
        if filters.customer_id is not None:
            query = query.where(PipelineRunRecord.customer_id == filters.customer_id)
        if filters.decision is not None:
            query = query.where(PipelineRunRecord.decision == filters.decision.value)
        if filters.status is not None:
            query = query.where(PipelineRunRecord.status == filters.status.value)
        if filters.date_from is not None:
            query = query.where(PipelineRunRecord.completed_at >= filters.date_from)
        if filters.date_to is not None:
            query = query.where(PipelineRunRecord.completed_at <= filters.date_to)
        query = query.order_by(PipelineRunRecord.completed_at.desc())
        return [pipeline_run_from_record(record) for record in self.session.scalars(query).all()]

    def _save_extraction(self, state: "PipelineState") -> str | None:
        extraction = state.get("extraction_result")
        if extraction is None:
            return None

        extraction_id = extraction.raw_response_id
        if self.session.get(Extraction, extraction_id) is None:
            self.session.add(
                Extraction(
                    id=extraction_id,
                    document_id=extraction.document_id,
                    model_used=extraction.model_used,
                    latency_ms=extraction.latency_ms,
                    cost_usd=extraction.cost_usd,
                    raw_response_id=extraction.raw_response_id,
                    payload=extraction.model_dump(mode="json"),
                )
            )
        return extraction_id

    def _save_validation(self, state: "PipelineState", *, extraction_id: str | None) -> str | None:
        validation = state.get("validation_result")
        if validation is None or extraction_id is None:
            return None

        validation_id = f"{extraction_id}:{validation.customer_id}:{validation.rule_set_version}"
        if self.session.get(Validation, validation_id) is None:
            self.session.add(
                Validation(
                    id=validation_id,
                    extraction_id=extraction_id,
                    customer_id=validation.customer_id,
                    rule_set_version=validation.rule_set_version,
                    overall_status=validation.overall_status.value,
                    validator_confidence=validation.validator_confidence,
                    field_results=[
                        field.model_dump(mode="json") for field in validation.field_results
                    ],
                )
            )
        return validation_id

    def _save_decision(self, state: "PipelineState", *, validation_id: str | None) -> str | None:
        decision = state.get("router_decision")
        if decision is None or validation_id is None:
            # Decision is already stored in PipelineRunRecord.decision_details
            # Only save to Decision table if validation exists (for relational integrity)
            return None

        decision_id = f"{validation_id}:{decision.decision.value}"
        if self.session.get(Decision, decision_id) is None:
            self.session.add(
                Decision(
                    id=decision_id,
                    validation_id=validation_id,
                    decision=decision.decision.value,
                    reasoning=decision.reasoning,
                    drafted_message=decision.drafted_message,
                    risk_flags=decision.risk_flags,
                )
            )
        return decision_id


def pipeline_run_from_record(record: PipelineRunRecord) -> PipelineRun:
    extraction = record.document.extractions[-1] if record.document.extractions else None
    validation = extraction.validations[-1] if extraction and extraction.validations else None

    # Router decision: prefer decision_details (always present), fallback to Decision table
    router_decision = None
    if record.decision_details is not None:
        router_decision = RouterDecision.model_validate(record.decision_details)
    elif validation and validation.decisions:
        decision = validation.decisions[-1]
        router_decision = RouterDecision(
            decision=decision.decision,
            reasoning=decision.reasoning,
            drafted_message=decision.drafted_message,
            risk_flags=decision.risk_flags,
        )

    return PipelineRun(
        run_id=record.run_id,
        document_id=record.document_id,
        customer_id=record.customer_id,
        status=record.status,
        stages=[StageEvent.model_validate(stage) for stage in record.stage_history],
        started_at=record.started_at,
        completed_at=record.completed_at,
        cost_total_usd=record.cost_total_usd,
        trace_id=record.trace_id,
        extraction_result=(
            ExtractionResult.model_validate(extraction.payload) if extraction is not None else None
        ),
        validation_result=(
            ValidationResult(
                extraction_id=validation.extraction_id,
                customer_id=validation.customer_id,
                rule_set_version=validation.rule_set_version,
                field_results=validation.field_results,
                overall_status=validation.overall_status,
                validator_confidence=validation.validator_confidence,
            )
            if validation is not None
            else None
        ),
        router_decision=router_decision,
    )


def infer_content_type(filename: str | None) -> str:
    if filename is None:
        return "application/octet-stream"
    suffix = Path(filename).suffix.lower()
    match suffix:
        case ".pdf":
            return "application/pdf"
        case ".png":
            return "image/png"
        case ".jpg" | ".jpeg":
            return "image/jpeg"
        case ".tif" | ".tiff":
            return "image/tiff"
        case _:
            return "application/octet-stream"
