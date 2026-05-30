from typing import Any, Protocol

from nova.observability.logging import get_logger

logger = get_logger(__name__)


class Tracer(Protocol):
    def emit(self, stage: str, event_type: str, payload: dict[str, Any]) -> None:
        ...


class LoggingTracer:
    def emit(self, stage: str, event_type: str, payload: dict[str, Any]) -> None:
        logger.info(
            "pipeline.trace",
            stage=stage,
            event_type=event_type,
            payload=payload,
        )
