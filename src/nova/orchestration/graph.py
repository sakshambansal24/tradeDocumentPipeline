from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from nova.agents.extractor import ExtractorAgent
from nova.agents.router import RouterAgent
from nova.agents.validator import ValidatorAgent
from nova.observability import LoggingTracer, Tracer, bind_context, trace_stage
from nova.prompts.extractor import REQUIRED_FIELDS
from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.pipeline import StageEvent, StageName, StageStatus

from .state import PipelineState

EXTRACTION_GARBAGE_CONFIDENCE_THRESHOLD = 0.3


@dataclass
class PipelineGraphDependencies:
    extractor: ExtractorAgent
    validator: ValidatorAgent
    router: RouterAgent
    tracer: Tracer
    persist_state: Callable[[PipelineState], None]


def build_graph(
    dependencies: PipelineGraphDependencies,
    *,
    checkpointer: MemorySaver | None = None,
):
    graph = StateGraph(PipelineState)
    graph.add_node("ingest", lambda state: ingest_node(state, dependencies))
    graph.add_node("extract", lambda state: extract_node(state, dependencies))
    graph.add_node("validate", lambda state: validate_node(state, dependencies))
    graph.add_node("route", lambda state: route_node(state, dependencies))
    graph.add_node("human_handoff", lambda state: human_handoff_node(state, dependencies))
    graph.add_node("persist", lambda state: persist_node(state, dependencies))

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "extract")
    graph.add_conditional_edges(
        "extract",
        route_after_extract,
        {
            "validate": "validate",
            "human_handoff": "human_handoff",
        },
    )
    graph.add_edge("validate", "route")
    graph.add_edge("route", "persist")
    graph.add_edge("human_handoff", "persist")
    graph.add_edge("persist", END)

    # POC checkpointing stays in memory.
    # Swap to SqliteSaver or PostgresSaver in prod for durability.
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def default_dependencies(
    *,
    tracer: Tracer | None = None,
    persist_state: Callable[[PipelineState], None] | None = None,
) -> PipelineGraphDependencies:
    return PipelineGraphDependencies(
        extractor=ExtractorAgent(),
        validator=ValidatorAgent(),
        router=RouterAgent(),
        tracer=tracer or LoggingTracer(),
        persist_state=persist_state or (lambda state: None),
    )


def ingest_node(state: PipelineState, dependencies: PipelineGraphDependencies) -> dict[str, Any]:
    stage = StageName.INGESTION

    def work() -> dict[str, Any]:
        if state.get("loaded_document") is None:
            raise ValueError("loaded_document is required before ingestion")
        return {
            "document_id": state["loaded_document"].doc_id,
            "current_stage": stage.value,
        }

    return run_stage(state, dependencies, stage=stage, work=work)


def extract_node(state: PipelineState, dependencies: PipelineGraphDependencies) -> dict[str, Any]:
    stage = StageName.EXTRACTION

    def work() -> dict[str, Any]:
        document = state.get("loaded_document")
        if document is None:
            raise ValueError("loaded_document is required for extraction")
        extraction = dependencies.extractor.extract(document)
        return {
            "extraction_result": extraction,
            "cost_total_usd": state.get("cost_total_usd", 0.0) + extraction.cost_usd,
            "current_stage": stage.value,
        }

    return run_stage(state, dependencies, stage=stage, work=work)


def validate_node(state: PipelineState, dependencies: PipelineGraphDependencies) -> dict[str, Any]:
    stage = StageName.VALIDATION

    def work() -> dict[str, Any]:
        extraction = state.get("extraction_result")
        if extraction is None:
            raise ValueError("extraction_result is required for validation")
        validation = dependencies.validator.validate(extraction, customer_id=state["customer_id"])
        return {
            "validation_result": validation,
            "current_stage": stage.value,
        }

    return run_stage(state, dependencies, stage=stage, work=work)


def route_node(state: PipelineState, dependencies: PipelineGraphDependencies) -> dict[str, Any]:
    stage = StageName.ROUTING

    def work() -> dict[str, Any]:
        validation = state.get("validation_result")
        if validation is None:
            raise ValueError("validation_result is required for routing")
        decision = dependencies.router.decide(validation)
        return {
            "router_decision": decision,
            "current_stage": stage.value,
        }

    return run_stage(state, dependencies, stage=stage, work=work)


def human_handoff_node(
    state: PipelineState,
    dependencies: PipelineGraphDependencies,
) -> dict[str, Any]:
    stage = StageName.ROUTING

    def work() -> dict[str, Any]:
        extraction = state.get("extraction_result")
        if extraction is None:
            raise ValueError("extraction_result is required for human handoff")
        missing_or_low = extraction_quality_failures(extraction)
        decision = RouterDecision(
            decision=DecisionType.HUMAN_REVIEW,
            reasoning=(
                f"Extraction quality gate failed on {missing_or_low} of "
                f"{len(REQUIRED_FIELDS)} required fields. Manual review recommended."
            ),
            drafted_message=None,
            risk_flags=["extraction_quality_gate_failed"],
        )
        return {
            "router_decision": decision,
            "current_stage": "human_handoff",
        }

    return run_stage(state, dependencies, stage=stage, work=work)


def persist_node(state: PipelineState, dependencies: PipelineGraphDependencies) -> dict[str, Any]:
    stage = StageName.STORAGE

    def work() -> dict[str, Any]:
        dependencies.persist_state(state)
        return {"current_stage": stage.value}

    return run_stage(state, dependencies, stage=stage, work=work)


def route_after_extract(state: PipelineState) -> str:
    extraction = state.get("extraction_result")
    if extraction is None:
        raise ValueError("extraction_result is required after extract")
    if extraction_quality_failures(extraction) > len(REQUIRED_FIELDS) / 2:
        return "human_handoff"
    return "validate"


def extraction_quality_failures(extraction) -> int:
    failures = 0
    for field_name in REQUIRED_FIELDS:
        field = extraction.fields.get(field_name)
        low_confidence = (
            field is not None and field.confidence < EXTRACTION_GARBAGE_CONFIDENCE_THRESHOLD
        )
        if field is None or not field.is_present or low_confidence:
            failures += 1
    return failures


def run_stage(
    state: PipelineState,
    dependencies: PipelineGraphDependencies,
    *,
    stage: StageName,
    work: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    bind_context(
        run_id=state["run_id"],
        stage=stage.value,
        customer_id=state.get("customer_id"),
    )
    dependencies.tracer.emit(stage.value, "started", {"run_id": state["run_id"]})
    with trace_stage(stage.value):
        update = work()
    completed_at = datetime.now(UTC)
    cost_usd = stage_cost(update)
    cost_meter = state.get("cost_meter")
    if cost_meter is not None:
        cost_meter.add_stage_cost(stage.value, cost_usd)
    event = StageEvent(
        stage=stage,
        status=StageStatus.COMPLETED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=round((completed_at - started_at).total_seconds() * 1000),
        cost_usd=cost_usd,
        trace_id=state["run_id"],
    )
    dependencies.tracer.emit(
        stage.value,
        "completed",
        {"run_id": state["run_id"], "latency_ms": event.latency_ms, "cost_usd": event.cost_usd},
    )
    final_update = update | {
        "stage_history": [event],
        "cost_total_usd": cost_meter.total_usd if cost_meter is not None else update.get(
            "cost_total_usd", state.get("cost_total_usd", 0.0)
        ),
    }
    dependencies.persist_state(state | final_update)
    return final_update


def stage_cost(update: dict[str, Any]) -> float:
    extraction = update.get("extraction_result")
    if extraction is not None:
        return extraction.cost_usd
    return 0.0
