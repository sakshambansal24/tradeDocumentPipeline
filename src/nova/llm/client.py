import time
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from openai import OpenAI, OpenAIError, RateLimitError
from pydantic import BaseModel, ValidationError

from nova.observability import calculate_model_cost_usd, record_llm_call
from nova.settings import get_settings

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class LLMRateLimitError(Exception):
    pass


class LLMStructuredOutputError(Exception):
    pass


class LLMProviderError(Exception):
    pass


@dataclass(frozen=True)
class VisionPage:
    page_number: int
    base64_image: str


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class StructuredLLMResponse(Generic[StructuredModel]):
    parsed: StructuredModel
    raw_response_id: str
    usage: LLMUsage


class VisionLLMClient(Protocol):
    def structured_vision_call(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        pages: list[VisionPage],
        response_model: type[StructuredModel],
    ) -> StructuredLLMResponse[StructuredModel]:
        ...


class OpenAIVisionClient:
    def __init__(self, *, api_key: str | None = None) -> None:
        settings = get_settings()
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def structured_vision_call(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        pages: list[VisionPage],
        response_model: type[StructuredModel],
    ) -> StructuredLLMResponse[StructuredModel]:
        user_content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
        user_content.extend(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{page.base64_image}",
            }
            for page in pages
        )

        started = time.perf_counter()
        try:
            # Prefer provider-native structured output: the SDK validates directly into Pydantic,
            # avoiding fragile free-text JSON parsing while keeping provider swap localized here.
            response = self._client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": user_content},
                ],
                text_format=response_model,
            )
        except RateLimitError as exc:
            raise LLMRateLimitError(str(exc)) from exc
        except ValidationError as exc:
            raise LLMStructuredOutputError(str(exc)) from exc
        except OpenAIError as exc:
            raise LLMProviderError(str(exc)) from exc

        parsed = response.output_parsed
        if parsed is None:
            raise LLMStructuredOutputError(
                "OpenAI response did not include parsed structured output"
            )

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        latency_ms = round((time.perf_counter() - started) * 1000)
        cost_usd = calculate_model_cost_usd(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        record_llm_call(
            model=model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            response_id=response.id,
        )

        return StructuredLLMResponse(
            parsed=parsed,
            raw_response_id=response.id,
            usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )
