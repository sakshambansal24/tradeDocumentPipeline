from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from nova.settings import get_settings


class NoopLangfuseClient:
    def start_as_current_observation(self, **kwargs):
        return NoopObservation()


class NoopObservation:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


@lru_cache(maxsize=1)
def get_langfuse_client():
    settings = get_settings()
    missing_langfuse_config = (
        not settings.langfuse_host
        or not settings.langfuse_public_key
        or not settings.langfuse_secret_key
    )
    if missing_langfuse_config:
        return NoopLangfuseClient()
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception:
        return NoopLangfuseClient()


@contextmanager
def trace_run(run_id: str) -> Iterator[Any]:
    client = get_langfuse_client()
    with client.start_as_current_observation(
        name="pipeline_run",
        as_type="span",
        trace_context={"trace_id": run_id},
        metadata={"run_id": run_id},
    ) as trace:
        yield trace


@contextmanager
def trace_stage(stage_name: str) -> Iterator[Any]:
    client = get_langfuse_client()
    with client.start_as_current_observation(
        name=f"stage:{stage_name}",
        as_type="span",
        metadata={"stage": stage_name},
    ) as span:
        yield span


def record_llm_call(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    cost_usd: float,
    response_id: str,
) -> None:
    client = get_langfuse_client()
    with client.start_as_current_observation(
        name="llm_call",
        as_type="generation",
        model=model,
        usage_details={
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total": prompt_tokens + completion_tokens,
        },
        cost_details={"total_cost": cost_usd},
        metadata={"latency_ms": latency_ms, "cost_usd": cost_usd, "response_id": response_id},
    ):
        pass
