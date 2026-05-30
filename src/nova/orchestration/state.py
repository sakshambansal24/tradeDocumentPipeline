import operator
from typing import Annotated, NotRequired, TypedDict

from nova.observability import CostMeter
from nova.schemas.decision import RouterDecision
from nova.schemas.extraction import ExtractionResult
from nova.schemas.ingestion import LoadedDocument
from nova.schemas.pipeline import StageEvent
from nova.schemas.validation import ValidationResult


class PipelineState(TypedDict):
    run_id: str
    document_id: str
    customer_id: str
    loaded_document: LoadedDocument | None
    extraction_result: NotRequired[ExtractionResult | None]
    validation_result: NotRequired[ValidationResult | None]
    router_decision: NotRequired[RouterDecision | None]
    stage_history: Annotated[list[StageEvent], operator.add]
    cost_total_usd: float
    cost_meter: NotRequired[CostMeter | None]
    current_stage: str
