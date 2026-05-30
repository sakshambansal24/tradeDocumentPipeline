import logging
import sys
from typing import Any

import structlog


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_context(
    *,
    run_id: str | None = None,
    stage: str | None = None,
    customer_id: str | None = None,
) -> None:
    values: dict[str, Any] = {}
    if run_id is not None:
        values["run_id"] = run_id
    if stage is not None:
        values["stage"] = stage
    if customer_id is not None:
        values["customer_id"] = customer_id
    structlog.contextvars.bind_contextvars(**values)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str):
    return structlog.get_logger(name)
