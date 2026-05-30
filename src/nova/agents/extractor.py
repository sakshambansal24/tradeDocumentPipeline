"""Extractor Agent.

Prompt strategy: use a trade-document-specific system prompt plus per-run user instructions
that repeat the anti-guessing rule at the end, where recency bias helps. The LLM returns a
structured draft, not free-text JSON; only after guardrails pass do we construct the strict
`ExtractionResult`. Confidence uses model self-reporting because it is cheap for the POC, then
post-checks reduce confidence when evidence is weak. Hallucination guards are named functions:
empty snippets demote fields to absent, and a cached OCR pass checks whether snippets appear in
the document text. OCR is not extraction input; it is only an evidence sanity check.
"""

import base64
import logging
import re
import time
from difflib import SequenceMatcher
from io import BytesIO
from typing import NoReturn

import pytesseract
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nova.llm import (
    LLMProviderError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    OpenAIVisionClient,
    StructuredLLMResponse,
    VisionLLMClient,
    VisionPage,
)
from nova.observability import calculate_model_cost_usd
from nova.prompts.extractor import REQUIRED_FIELDS, SYSTEM_PROMPT, build_user_instructions
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.ingestion import LoadedDocument
from nova.settings import get_settings

logger = logging.getLogger(__name__)

MAX_RATE_LIMIT_RETRIES = 3
OCR_FUZZY_THRESHOLD = 0.82


class ExtractorError(Exception):
    pass


class ExtractorFieldDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    source_page: int = Field(ge=1)
    source_snippet: str = ""
    reasoning: str
    is_present: bool

    @field_validator("name", "reasoning")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value


class ExtractorDraftResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    consignee_name: ExtractorFieldDraft
    hs_code: ExtractorFieldDraft
    port_of_loading: ExtractorFieldDraft
    port_of_discharge: ExtractorFieldDraft
    incoterms: ExtractorFieldDraft
    description_of_goods: ExtractorFieldDraft
    gross_weight: ExtractorFieldDraft
    invoice_number: ExtractorFieldDraft

    @property
    def fields(self) -> dict[str, ExtractorFieldDraft]:
        return {
            "consignee_name": self.consignee_name,
            "hs_code": self.hs_code,
            "port_of_loading": self.port_of_loading,
            "port_of_discharge": self.port_of_discharge,
            "incoterms": self.incoterms,
            "description_of_goods": self.description_of_goods,
            "gross_weight": self.gross_weight,
            "invoice_number": self.invoice_number,
        }


class ExtractorAgent:
    def __init__(
        self,
        *,
        llm_client: VisionLLMClient | None = None,
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        self.model = model or settings.primary_vision_model
        self.llm_client = llm_client or OpenAIVisionClient()
        self._ocr_cache: dict[str, str] = {}

    def extract(self, document: LoadedDocument) -> ExtractionResult:
        started = time.perf_counter()
        prompt = self._build_user_prompt(document)
        response = self._call_llm_with_resilience(document=document, user_prompt=prompt)
        ocr_text = self._get_cached_ocr_text(document)
        fields = self._guard_and_finalize_fields(response.parsed.fields, document, ocr_text)
        latency_ms = round((time.perf_counter() - started) * 1000)
        cost_usd = calculate_cost_usd(
            model=self.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

        result = ExtractionResult(
            document_id=document.doc_id,
            document_type=response.parsed.document_type,
            fields=fields,
            model_used=self.model,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            raw_response_id=response.raw_response_id,
        )

        logger.info(
            "extractor.completed",
            extra={
                "document_id": document.doc_id,
                "model": self.model,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "raw_response_id": response.raw_response_id,
            },
        )
        return result

    def _build_user_prompt(self, document: LoadedDocument, parser_error: str | None = None) -> str:
        schema_json = ExtractorDraftResult.model_json_schema()
        page_summaries = [
            (
                f"- Page {page.page_number}: {page.width}x{page.height}px, "
                f"dpi={page.dpi}, quality_score={page.quality_score}, "
                f"warnings={page.warnings}"
            )
            for page in document.pages
        ]
        prompt = build_user_instructions(str(schema_json), page_summaries)
        if parser_error:
            prompt += (
                "\n\nPrevious structured-output validation failed. Fix the response to satisfy "
                f"the schema exactly. Parser error: {parser_error}"
            )
        return prompt

    def _call_llm_with_resilience(
        self,
        *,
        document: LoadedDocument,
        user_prompt: str,
    ) -> StructuredLLMResponse[ExtractorDraftResult]:
        pages = [
            VisionPage(page_number=page.page_number, base64_image=page.base64_image)
            for page in document.pages
        ]
        rate_limit_attempt = 0
        parser_retry_used = False
        current_prompt = user_prompt

        while True:
            try:
                return self.llm_client.structured_vision_call(
                    model=self.model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=current_prompt,
                    pages=pages,
                    response_model=ExtractorDraftResult,
                )
            except LLMRateLimitError:
                if rate_limit_attempt >= MAX_RATE_LIMIT_RETRIES:
                    self._raise_extractor_error("Extractor LLM rate limit retries exhausted")
                sleep_seconds = 2**rate_limit_attempt
                logger.warning(
                    "extractor.rate_limit_retry",
                    extra={
                        "document_id": document.doc_id,
                        "attempt": rate_limit_attempt + 1,
                        "sleep_seconds": sleep_seconds,
                    },
                )
                time.sleep(sleep_seconds)
                rate_limit_attempt += 1
            except LLMStructuredOutputError as exc:
                if parser_retry_used:
                    raise ExtractorError(
                        f"Extractor structured-output validation failed after retry: {exc}"
                    ) from exc
                current_prompt = self._build_user_prompt(document, parser_error=str(exc))
                parser_retry_used = True
            except LLMProviderError as exc:
                raise ExtractorError(f"Extractor LLM provider failed: {exc}") from exc

    def _raise_extractor_error(self, message: str) -> NoReturn:
        raise ExtractorError(message)

    def _guard_and_finalize_fields(
        self,
        draft_fields: dict[str, ExtractorFieldDraft],
        document: LoadedDocument,
        ocr_text: str,
    ) -> dict[str, ExtractedField]:
        finalized: dict[str, ExtractedField] = {}

        for field_name in REQUIRED_FIELDS:
            draft = draft_fields[field_name]
            guarded = demote_missing_snippet(draft, document_id=document.doc_id)
            guarded = penalize_unverified_snippet(guarded, ocr_text, document_id=document.doc_id)
            finalized[field_name] = ExtractedField(
                name=field_name,
                value=guarded.value if guarded.is_present else None,
                confidence=guarded.confidence,
                source_page=guarded.source_page,
                source_snippet=guarded.source_snippet if guarded.is_present else "",
                reasoning=guarded.reasoning,
                is_present=guarded.is_present,
            )

        return finalized

    def _get_cached_ocr_text(self, document: LoadedDocument) -> str:
        cached = self._ocr_cache.get(document.original_bytes_hash)
        if cached is not None:
            return cached

        page_texts = []
        for page in document.pages:
            try:
                image = Image.open(BytesIO(base64.b64decode(page.base64_image)))
                page_texts.append(pytesseract.image_to_string(image))
            except (pytesseract.TesseractError, OSError, ValueError) as exc:
                logger.warning(
                    "extractor.ocr_failed",
                    extra={
                        "document_id": document.doc_id,
                        "page_number": page.page_number,
                        "reason": str(exc),
                    },
                )

        ocr_text = "\n".join(page_texts)
        self._ocr_cache[document.original_bytes_hash] = ocr_text
        return ocr_text


def demote_missing_snippet(field: ExtractorFieldDraft, *, document_id: str) -> ExtractorFieldDraft:
    if field.is_present and not field.source_snippet.strip():
        logger.warning(
            "extractor.demoted_missing_snippet",
            extra={"document_id": document_id, "field_name": field.name},
        )
        return field.model_copy(
            update={
                "value": None,
                "confidence": 0.0,
                "source_snippet": "",
                "reasoning": f"{field.reasoning} Demoted because no source snippet was provided.",
                "is_present": False,
            }
        )
    return field


def penalize_unverified_snippet(
    field: ExtractorFieldDraft,
    ocr_text: str,
    *,
    document_id: str,
) -> ExtractorFieldDraft:
    if not field.is_present or not field.source_snippet.strip():
        return field

    if not ocr_text.strip():
        logger.warning(
            "extractor.ocr_unavailable_skip_penalty",
            extra={"document_id": document_id, "field_name": field.name},
        )
        return field

    if not snippet_matches_ocr(field.source_snippet, ocr_text):
        logger.warning(
            "extractor.snippet_not_verified_by_ocr",
            extra={"document_id": document_id, "field_name": field.name},
        )
        return field.model_copy(update={"confidence": max(0.0, round(field.confidence - 0.5, 4))})

    return field


def snippet_matches_ocr(snippet: str, ocr_text: str) -> bool:
    normalized_snippet = normalize_text(snippet)
    normalized_ocr = normalize_text(ocr_text)

    if not normalized_snippet or not normalized_ocr:
        return False
    if normalized_snippet in normalized_ocr:
        return True

    snippet_length = len(normalized_snippet)
    if snippet_length < 8:
        return False

    best_ratio = 0.0
    stop = max(1, len(normalized_ocr) - snippet_length + 1)
    step = max(1, snippet_length // 3)
    for index in range(0, stop, step):
        candidate = normalized_ocr[index : index + snippet_length]
        best_ratio = max(best_ratio, SequenceMatcher(None, normalized_snippet, candidate).ratio())
        if best_ratio >= OCR_FUZZY_THRESHOLD:
            return True
    return False


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def calculate_cost_usd(*, model: str, input_tokens: int, output_tokens: int) -> float:
    cost = calculate_model_cost_usd(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    if cost == 0.0:
        logger.warning("extractor.unknown_model_cost", extra={"model": model})
    return cost
