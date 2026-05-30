from datetime import UTC, datetime
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.orm import Session, sessionmaker

from nova.observability import (
    CostMeter,
    LoggingTracer,
    Tracer,
    bind_context,
    clear_context,
    get_logger,
    trace_run,
)
from nova.schemas.ingestion import LoadedDocument
from nova.schemas.pipeline import PipelineRun, PipelineRunStatus, StageEvent, StageName, StageStatus
from nova.storage import PipelineRunRepository, session_scope

from .graph import PipelineGraphDependencies, build_graph, default_dependencies
from .state import PipelineState

logger = get_logger(__name__)


class PipelineRunner:
    def __init__(
        self,
        *,
        dependencies: PipelineGraphDependencies | None = None,
        checkpointer: MemorySaver | None = None,
        tracer: Tracer | None = None,
        storage_session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.checkpointer = checkpointer or MemorySaver()
        self.tracer = tracer or LoggingTracer()
        self.state_store: dict[str, PipelineState] = {}
        self.storage_session_factory = storage_session_factory
        self.dependencies = dependencies or default_dependencies(
            tracer=self.tracer,
            persist_state=self._persist_state,
        )
        self.graph = build_graph(self.dependencies, checkpointer=self.checkpointer)

    def run(self, loaded_document: LoadedDocument, customer_id: str) -> PipelineRun:
        run_id = str(uuid4())
        cost_meter = CostMeter(run_id=run_id)
        state: PipelineState = {
            "run_id": run_id,
            "document_id": loaded_document.doc_id,
            "customer_id": customer_id,
            "loaded_document": loaded_document,
            "extraction_result": None,
            "validation_result": None,
            "router_decision": None,
            "stage_history": [],
            "cost_total_usd": 0.0,
            "current_stage": "",
            "cost_meter": cost_meter,
        }
        bind_context(run_id=run_id, customer_id=customer_id)
        try:
            with trace_run(run_id):
                return self._invoke(state, run_id=run_id)
        finally:
            clear_context()

    def resume(self, run_id: str) -> PipelineRun:
        return self._invoke(None, run_id=run_id)

    def _invoke(self, state: PipelineState | None, *, run_id: str) -> PipelineRun:
        config = {"configurable": {"thread_id": run_id}}
        started_at = datetime.now(UTC)
        try:
            final_state = self.graph.invoke(state, config=config)
            self.state_store[run_id] = final_state
            run = self._to_pipeline_run(final_state, started_at=started_at)
            self._persist_run_nonfatal(run, final_state)
            return run
        except Exception as exc:
            checkpoint_state = self.graph.get_state(config).values
            failed_state = self._record_failure(checkpoint_state, run_id=run_id, error=exc)
            self.state_store[run_id] = failed_state
            run = self._to_pipeline_run(failed_state, started_at=started_at, failed=True)
            self._persist_run_nonfatal(run, failed_state)
            return run

    def _record_failure(
        self,
        state: PipelineState | dict,
        *,
        run_id: str,
        error: Exception,
    ) -> PipelineState:
        now = datetime.now(UTC)
        failed_stage = infer_failed_stage(state.get("current_stage", ""))
        existing_history = list(state.get("stage_history", []))
        existing_history.append(
            StageEvent(
                stage=failed_stage,
                status=StageStatus.FAILED,
                started_at=now,
                completed_at=now,
                latency_ms=0,
                trace_id=run_id,
                error_message=str(error),
            )
        )
        self.tracer.emit(
            failed_stage.value,
            "failed",
            {"run_id": run_id, "error_message": str(error)},
        )
        return PipelineState(
            run_id=run_id,
            document_id=state.get("document_id", ""),
            customer_id=state.get("customer_id", ""),
            loaded_document=state.get("loaded_document"),
            extraction_result=state.get("extraction_result"),
            validation_result=state.get("validation_result"),
            router_decision=state.get("router_decision"),
            stage_history=existing_history,
            cost_total_usd=state.get("cost_total_usd", 0.0),
            current_stage=failed_stage.value,
        )

    def _persist_state(self, state: PipelineState) -> None:
        self.state_store[state["run_id"]] = state

    def _persist_run_nonfatal(self, run: PipelineRun, state: PipelineState) -> None:
        if self.storage_session_factory is None:
            return
        try:
            with session_scope(self.storage_session_factory) as session:
                PipelineRunRepository(session).save_run(run, state=state)
        except Exception:
            logger.exception(
                "pipeline.storage_persist_failed",
                run_id=str(run.run_id),
                document_id=run.document_id,
            )

    def _to_pipeline_run(
        self,
        state: PipelineState,
        *,
        started_at: datetime,
        failed: bool = False,
    ) -> PipelineRun:
        return PipelineRun(
            run_id=UUID(state["run_id"]),
            document_id=state["document_id"],
            customer_id=state["customer_id"],
            status=pipeline_status_from_state(state, failed=failed),
            stages=state.get("stage_history", []),
            started_at=started_at,
            completed_at=datetime.now(UTC),
            cost_total_usd=state.get("cost_total_usd", 0.0),
            trace_id=state["run_id"],
            extraction_result=state.get("extraction_result"),
            validation_result=state.get("validation_result"),
            router_decision=state.get("router_decision"),
        )


def pipeline_status_from_state(state: PipelineState, *, failed: bool) -> PipelineRunStatus:
    if failed:
        return PipelineRunStatus.FAILED
    decision = state.get("router_decision")
    if decision is not None and decision.decision.value == "HUMAN_REVIEW":
        return PipelineRunStatus.NEEDS_REVIEW
    return PipelineRunStatus.COMPLETED


def infer_failed_stage(current_stage: str) -> StageName:
    match current_stage:
        case "":
            return StageName.INGESTION
        case StageName.INGESTION.value:
            return StageName.EXTRACTION
        case StageName.EXTRACTION.value:
            return StageName.VALIDATION
        case StageName.VALIDATION.value:
            return StageName.ROUTING
        case StageName.ROUTING.value | "human_handoff":
            return StageName.STORAGE
        case _:
            return StageName.STORAGE
