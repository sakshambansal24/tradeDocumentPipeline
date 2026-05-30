from datetime import UTC, datetime
from uuid import uuid4

from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.ingestion import LoadedDocument, PageImage
from nova.schemas.pipeline import PipelineRun, PipelineRunStatus, StageEvent, StageName, StageStatus
from nova.storage import PipelineRunRepository, RunFilters, init_db, session_scope


def test_pipeline_run_repository_round_trip(tmp_path) -> None:
    session_factory = init_db(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    run = _pipeline_run(customer_id="acme_corp", status=PipelineRunStatus.COMPLETED)

    with session_scope(session_factory) as session:
        PipelineRunRepository(session).save_run(run)

    with session_scope(session_factory) as session:
        loaded = PipelineRunRepository(session).get(str(run.run_id))

    assert loaded.run_id == run.run_id
    assert loaded.document_id == run.document_id
    assert loaded.customer_id == run.customer_id
    assert loaded.status == run.status
    assert loaded.stages[0].stage == StageName.EXTRACTION
    assert loaded.stages[0].status == StageStatus.COMPLETED
    assert loaded.cost_total_usd == run.cost_total_usd


def test_pipeline_run_repository_filters_by_customer_and_decision(tmp_path) -> None:
    session_factory = init_db(f"sqlite:///{tmp_path / 'filters.db'}")
    runs = [
        _pipeline_run(customer_id="acme_corp", status=PipelineRunStatus.COMPLETED),
        _pipeline_run(customer_id="acme_corp", status=PipelineRunStatus.COMPLETED),
        _pipeline_run(customer_id="globex", status=PipelineRunStatus.COMPLETED),
    ]

    with session_scope(session_factory) as session:
        repo = PipelineRunRepository(session)
        repo.save_run(runs[0], state=_state_for_decision(runs[0], DecisionType.AMEND))
        repo.save_run(runs[1], state=_state_for_decision(runs[1], DecisionType.AUTO_APPROVE))
        repo.save_run(runs[2], state=_state_for_decision(runs[2], DecisionType.AMEND))

    with session_scope(session_factory) as session:
        results = PipelineRunRepository(session).list(
            RunFilters(customer_id="acme_corp", decision=DecisionType.AMEND)
        )

    assert len(results) == 1
    assert results[0].customer_id == "acme_corp"
    assert results[0].run_id == runs[0].run_id


def _pipeline_run(*, customer_id: str, status: PipelineRunStatus) -> PipelineRun:
    run_id = uuid4()
    now = datetime.now(UTC)
    return PipelineRun(
        run_id=run_id,
        document_id=f"doc-{run_id}",
        customer_id=customer_id,
        status=status,
        stages=[
            StageEvent(
                stage=StageName.EXTRACTION,
                status=StageStatus.COMPLETED,
                started_at=now,
                completed_at=now,
                latency_ms=10,
                cost_usd=0.01,
                trace_id=str(run_id),
            )
        ],
        started_at=now,
        completed_at=now,
        cost_total_usd=0.01,
        trace_id=str(run_id),
    )


def _state_for_decision(run: PipelineRun, decision: DecisionType):
    return {
        "run_id": str(run.run_id),
        "document_id": run.document_id,
        "customer_id": run.customer_id,
        "loaded_document": LoadedDocument(
            doc_id=run.document_id,
            source_filename="fixture.png",
            page_count=1,
            pages=[
                PageImage(
                    page_number=1,
                    width=100,
                    height=100,
                    dpi=200,
                    base64_image="fixture",
                    quality_score=0.9,
                    rotation_applied_deg=0,
                    warnings=[],
                )
            ],
            original_bytes_hash=run.document_id,
        ),
        "extraction_result": None,
        "validation_result": None,
        "router_decision": RouterDecision(
            decision=decision,
            reasoning="Test decision.",
            drafted_message="Please amend." if decision == DecisionType.AMEND else None,
            risk_flags=[],
        ),
        "stage_history": run.stages,
        "cost_total_usd": run.cost_total_usd,
        "current_stage": StageName.STORAGE.value,
    }
