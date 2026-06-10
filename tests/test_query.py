from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nova.query import QueryAgent, QueryAgentError
from nova.query.query_agent import (
    SQL_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    build_schema_context,
    validate_sql,
)
from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.ingestion import LoadedDocument, PageImage
from nova.schemas.pipeline import PipelineRun, PipelineRunStatus, StageEvent, StageName, StageStatus
from nova.schemas.query import SqlQueryPlan
from nova.schemas.shipment import Shipment, ShipmentStatus
from nova.schemas.validation import (
    FieldValidation,
    FieldValidationStatus,
    ValidationOverallStatus,
    ValidationResult,
)
from nova.storage import PipelineRunRepository, ShipmentRepository, init_db, session_scope


def test_sql_prompt_uses_runtime_schema_context() -> None:
    assert "Runtime schema context" not in SQL_SYSTEM_PROMPT
    assert "Use only the tables, columns, JSON paths, and values" in SQL_SYSTEM_PROMPT
    assert "For shipment-level questions, prefer shipments." in SQL_SYSTEM_PROMPT
    assert "For \"last shipment\" or \"latest shipment\"" in SQL_SYSTEM_PROMPT
    assert "without filtering by review/amendment" in SQL_SYSTEM_PROMPT


def test_runtime_schema_context_exposes_tables_and_observed_values(tmp_path) -> None:
    session_factory = init_db(f"sqlite:///{tmp_path / 'query-schema.db'}")
    now = datetime.now(UTC)
    with session_scope(session_factory) as session:
        repo = PipelineRunRepository(session)
        run = _run(customer_id="acme_corp", completed_at=now)
        repo.save_run(run, state=_state(run, decision=DecisionType.AMEND))

        context = build_schema_context(session)

    assert "shipments(shipment_id, email_id, customer_id" in context
    assert "pipeline_runs(id, run_id, document_id" in context
    assert "pipeline_runs.shipment_id -> shipments.shipment_id" in context
    assert "$.document_id" in context
    assert "validations.field_results: $[]" in context
    assert "shipments.status: PENDING, PROCESSING, REQUIRES_REVIEW, APPROVED, AMENDED" in context
    assert "pipeline_runs.decision: AUTO_APPROVE, HUMAN_REVIEW, AMEND" in context
    assert "decisions.decision: AMEND" in context
    assert "validations.field_results[].status: MATCH" in context


def test_summary_prompt_translates_enum_values_to_user_language() -> None:
    assert "AUTO_APPROVE -> auto approved" in SUMMARY_SYSTEM_PROMPT
    assert "HUMAN_REVIEW -> sent to human review" in SUMMARY_SYSTEM_PROMPT
    assert "AMEND -> amendment required" in SUMMARY_SYSTEM_PROMPT


def test_query_rejects_common_hallucinated_domain_values() -> None:
    with pytest.raises(QueryAgentError, match="unsupported domain value APPROVED"):
        validate_sql("SELECT COUNT(*) FROM pipeline_runs WHERE decision = 'APPROVED'")


def test_query_allows_shipment_table_for_shipment_level_questions() -> None:
    sql = validate_sql(
        "SELECT shipment_id, customer_id, status, overall_decision, triggered_at, completed_at "
        "FROM shipments "
        "WHERE customer_id = 'acme_corp' "
        "ORDER BY COALESCE(completed_at, triggered_at) DESC "
        "LIMIT 1"
    )

    assert "FROM shipments" in sql
    assert sql.endswith("LIMIT 1")


def test_latest_shipment_explanation_does_not_filter_out_amended_shipments(tmp_path) -> None:
    session_factory = init_db(f"sqlite:///{tmp_path / 'query-last-shipment.db'}")
    now = datetime.now(UTC)
    shipment_id = uuid4()
    with session_scope(session_factory) as session:
        ShipmentRepository(session).save_shipment(
            Shipment(
                shipment_id=shipment_id,
                email_id="email-latest-shipment",
                customer_id="acme_corp",
                triggered_at=now,
                status=ShipmentStatus.AMENDED,
                document_runs=[],
                overall_decision=RouterDecision(
                    decision=DecisionType.AMEND,
                    reasoning="Cross-document inconsistencies require supplier amendment.",
                    drafted_message="Please amend.",
                    risk_flags=["cross_document_inconsistency"],
                ),
                draft_reply="Please amend the inconsistent documents.",
                completed_at=now,
            )
        )

    agent = QueryAgent(
        session_factory=session_factory,
        planner=StaticPlanner(
            SqlQueryPlan(
                sql=(
                    "SELECT shipment_id FROM shipments "
                    "WHERE status = 'REQUIRES_REVIEW' "
                    "ORDER BY COALESCE(completed_at, triggered_at) DESC "
                    "LIMIT 1"
                )
            )
        ),
        summarizer=StaticSummarizer("The latest shipment required amendment."),
    )

    answer = agent.ask("explain why last shipment needed review")

    assert "WHERE status = 'REQUIRES_REVIEW'" not in answer.evidence.sql
    assert answer.evidence.rows[0]["shipment_id"] == str(shipment_id)
    assert answer.evidence.rows[0]["status"] == ShipmentStatus.AMENDED.value


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
            SqlQueryPlan(
                sql=(
                    "SELECT COUNT(*) AS count "
                    "FROM pipeline_runs "
                    "WHERE decision = 'AMEND' "
                    f"AND completed_at >= '{date_from.isoformat()}' "
                    f"AND completed_at <= '{date_to.isoformat()}'"
                )
            )
        ),
        summarizer=StaticSummarizer("There was 1 amendment this week."),
    )

    answer = agent.ask("How many shipments were flagged for amendment this week?")

    assert "1" in answer.answer
    assert "FROM pipeline_runs" in answer.evidence.sql
    assert answer.evidence.rows == [{"count": 1}]
    assert answer.evidence.row_count == 1


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
            SqlQueryPlan(
                sql=(
                    "SELECT json_extract(field.value, '$.field_name') AS field_name, "
                    "COUNT(*) AS mismatch_count "
                    "FROM validations, json_each(validations.field_results) AS field "
                    "WHERE validations.customer_id = 'acme_corp' "
                    f"AND validations.created_at >= '{date_from.isoformat()}' "
                    f"AND validations.created_at <= '{date_to.isoformat()}' "
                    "AND json_extract(field.value, '$.status') IN ('MISMATCH', 'MISSING') "
                    "GROUP BY field_name "
                    "ORDER BY mismatch_count DESC "
                    "LIMIT 5"
                )
            )
        ),
        summarizer=StaticSummarizer("The top failing field is hs_code."),
    )

    answer = agent.ask("What's the top reason ACME documents get rejected?")

    assert answer.evidence.rows == [{"field_name": "hs_code", "mismatch_count": 1}]
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
            SqlQueryPlan(
                sql=(
                    "SELECT json_extract(field.value, '$.field_name') AS field_name, "
                    "COUNT(*) AS mismatch_count "
                    "FROM validations, json_each(validations.field_results) AS field "
                    "WHERE validations.customer_id = 'acme_corp' "
                    f"AND validations.created_at >= '{(now - timedelta(days=1)).isoformat()}' "
                    f"AND validations.created_at <= '{(now + timedelta(days=1)).isoformat()}' "
                    "AND json_extract(field.value, '$.status') IN ('MISMATCH', 'MISSING') "
                    "GROUP BY field_name "
                    "ORDER BY mismatch_count DESC "
                    "LIMIT 5"
                )
            )
        ),
        summarizer=StaticSummarizer("The top failing field is hs_code."),
    )

    answer = agent.ask("What is the top reason ACME documents got rejected?")

    assert answer.evidence.rows == [{"field_name": "hs_code", "mismatch_count": 1}]
    assert "hs_code" in answer.answer


class StaticPlanner:
    def __init__(self, plan: SqlQueryPlan) -> None:
        self._plan = plan

    def plan(self, question: str, *, schema_context: str = "") -> SqlQueryPlan:
        return self._plan


class StaticSummarizer:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def summarize(self, *, question: str, sql: str, rows: list[dict]) -> str:
        return self.answer


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
