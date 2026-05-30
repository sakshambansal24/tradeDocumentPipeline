from datetime import UTC, datetime, timedelta
from uuid import uuid4

from nova.query import QueryAgent
from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.ingestion import LoadedDocument, PageImage
from nova.schemas.pipeline import PipelineRun, PipelineRunStatus, StageEvent, StageName, StageStatus
from nova.schemas.query import QueryPlan, QueryToolCall
from nova.schemas.validation import (
    FieldValidation,
    FieldValidationStatus,
    ValidationOverallStatus,
    ValidationResult,
)
from nova.storage import PipelineRunRepository, init_db, session_scope


class FakePlanner:
    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan
        self.questions: list[str] = []

    def plan(self, question: str) -> QueryPlan:
        self.questions.append(question)
        return self.plan


def test_query_counts_amendments_this_week_with_evidence(tmp_path) -> None:
    session_factory = init_db(f"sqlite:///{tmp_path / 'query-count.db'}")
    now = datetime.now(UTC)
    with session_scope(session_factory) as session:
        repo = PipelineRunRepository(session)
        amend_run = _run(customer_id="acme_corp", completed_at=now)
        approve_run = _run(customer_id="acme_corp", completed_at=now)
        repo.save_run(amend_run, state=_state(amend_run, decision=DecisionType.AMEND))
        repo.save_run(approve_run, state=_state(approve_run, decision=DecisionType.AUTO_APPROVE))

    date_from = now - timedelta(days=7)
    date_to = now + timedelta(days=1)
    agent = QueryAgent(
        session_factory=session_factory,
        planner=StaticPlanner(
            QueryPlan(
                tool_calls=[
                    QueryToolCall(
                        name="count_runs",
                        args={
                            "filters": {
                                "decision": "AMEND",
                                "date_from": date_from,
                                "date_to": date_to,
                            }
                        },
                    )
                ]
            )
        ),
    )

    answer = agent.ask("How many shipments were flagged for amendment this week?")

    assert "1" in answer.answer
    evidence = answer.evidence.tool_calls[0]
    assert evidence.tool_name == "count_runs"
    assert evidence.args["filters"]["decision"] == "AMEND"
    assert evidence.args["filters"]["date_from"] == date_from
    assert evidence.args["filters"]["date_to"] == date_to
    assert evidence.result == 1


def test_query_top_rejection_reason_filters_to_customer(tmp_path) -> None:
    session_factory = init_db(f"sqlite:///{tmp_path / 'query-top-fields.db'}")
    now = datetime.now(UTC)
    with session_scope(session_factory) as session:
        repo = PipelineRunRepository(session)
        acme_run = _run(customer_id="acme_corp", completed_at=now)
        globex_run = _run(customer_id="globex", completed_at=now)
        repo.save_run(
            acme_run,
            state=_state(
                acme_run,
                decision=DecisionType.AMEND,
                field_name="hs_code",
                field_status=FieldValidationStatus.MISMATCH,
            ),
        )
        repo.save_run(
            globex_run,
            state=_state(
                globex_run,
                decision=DecisionType.AMEND,
                field_name="gross_weight",
                field_status=FieldValidationStatus.MISMATCH,
            ),
        )

    date_from = now - timedelta(days=7)
    date_to = now + timedelta(days=1)
    agent = QueryAgent(
        session_factory=session_factory,
        planner=StaticPlanner(
            QueryPlan(
                tool_calls=[
                    QueryToolCall(
                        name="top_failing_fields",
                        args={
                            "customer_id": "acme_corp",
                            "date_from": date_from,
                            "date_to": date_to,
                            "limit": 5,
                        },
                    )
                ]
            )
        ),
    )

    answer = agent.ask("What's the top reason ACME documents get rejected?")

    evidence = answer.evidence.tool_calls[0]
    assert evidence.tool_name == "top_failing_fields"
    assert evidence.args["customer_id"] == "acme_corp"
    assert evidence.result == [{"field_name": "hs_code", "mismatch_count": 1}]
    assert "hs_code" in answer.answer


def test_query_normalizes_customer_name_to_configured_customer_id(tmp_path) -> None:
    session_factory = init_db(f"sqlite:///{tmp_path / 'query-customer-normalize.db'}")
    now = datetime.now(UTC)
    with session_scope(session_factory) as session:
        repo = PipelineRunRepository(session)
        acme_run = _run(customer_id="acme_corp", completed_at=now)
        repo.save_run(
            acme_run,
            state=_state(
                acme_run,
                decision=DecisionType.AMEND,
                field_name="hs_code",
                field_status=FieldValidationStatus.MISMATCH,
            ),
        )

    agent = QueryAgent(
        session_factory=session_factory,
        planner=StaticPlanner(
            QueryPlan(
                tool_calls=[
                    QueryToolCall(
                        name="top_failing_fields",
                        args={
                            "customer_id": "acme",
                            "date_from": now - timedelta(days=1),
                            "date_to": now + timedelta(days=1),
                            "limit": 5,
                        },
                    )
                ]
            )
        ),
    )

    answer = agent.ask("What is the top reason ACME documents got rejected?")

    evidence = answer.evidence.tool_calls[0]
    assert evidence.result == [{"field_name": "hs_code", "mismatch_count": 1}]
    assert "hs_code" in answer.answer


class StaticPlanner:
    def __init__(self, plan: QueryPlan) -> None:
        self._plan = plan

    def plan(self, question: str) -> QueryPlan:
        return self._plan


def _run(*, customer_id: str, completed_at: datetime) -> PipelineRun:
    run_id = uuid4()
    return PipelineRun(
        run_id=run_id,
        document_id=f"doc-{run_id}",
        customer_id=customer_id,
        status=PipelineRunStatus.COMPLETED,
        stages=[
            StageEvent(
                stage=StageName.STORAGE,
                status=StageStatus.COMPLETED,
                started_at=completed_at,
                completed_at=completed_at,
                latency_ms=1,
                cost_usd=0.0,
                trace_id=str(run_id),
            )
        ],
        started_at=completed_at,
        completed_at=completed_at,
        cost_total_usd=0.01,
        trace_id=str(run_id),
    )


def _state(
    run: PipelineRun,
    *,
    decision: DecisionType,
    field_name: str = "hs_code",
    field_status: FieldValidationStatus = FieldValidationStatus.MATCH,
):
    extraction = ExtractionResult(
        document_id=run.document_id,
        document_type=DocumentType.INVOICE,
        fields={
            field_name: ExtractedField(
                name=field_name,
                value="1234",
                confidence=0.95,
                source_page=1,
                source_snippet="1234",
                reasoning="Test fixture.",
                is_present=True,
            )
        },
        model_used="test-model",
        latency_ms=1,
        cost_usd=0.01,
        raw_response_id=f"response-{run.run_id}",
    )
    validation = ValidationResult(
        extraction_id=run.document_id,
        customer_id=run.customer_id,
        rule_set_version="v1",
        field_results=[
            FieldValidation(
                field_name=field_name,
                status=field_status,
                found_value="1234",
                expected_value="expected",
                expected_rule="test_rule",
                reason="Test fixture.",
                extraction_confidence=0.95,
            )
        ],
        overall_status=(
            ValidationOverallStatus.FAILED
            if field_status == FieldValidationStatus.MISMATCH
            else ValidationOverallStatus.PASSED
        ),
        validator_confidence=0.95,
    )
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
        "extraction_result": extraction,
        "validation_result": validation,
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
