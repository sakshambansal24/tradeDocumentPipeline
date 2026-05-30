import base64
from io import BytesIO
from typing import Any

from PIL import Image
from pydantic import BaseModel

from nova.agents.extractor import (
    ExtractorAgent,
    ExtractorDraftResult,
    ExtractorFieldDraft,
    penalize_unverified_snippet,
)
from nova.llm import LLMUsage, StructuredLLMResponse, VisionPage
from nova.schemas.extraction import DocumentType
from nova.schemas.ingestion import LoadedDocument, PageImage


class FakeVisionClient:
    def __init__(self, parsed: ExtractorDraftResult) -> None:
        self.parsed = parsed

    def structured_vision_call(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        pages: list[VisionPage],
        response_model: type[BaseModel],
    ) -> StructuredLLMResponse[Any]:
        return StructuredLLMResponse(
            parsed=self.parsed,
            raw_response_id="fake-response-1",
            usage=LLMUsage(input_tokens=1_000, output_tokens=500),
        )


def test_extractor_clean_sample_returns_required_fields(monkeypatch) -> None:
    snippets = {
        "consignee_name": "Consignee: Acme Imports Ltd",
        "hs_code": "HS Code: 090121",
        "port_of_loading": "Port of Loading: Nhava Sheva",
        "port_of_discharge": "Port of Discharge: Rotterdam",
        "incoterms": "Incoterms: CIF",
        "description_of_goods": "Description: Roasted coffee beans",
        "gross_weight": "Gross Weight: 1200 KG",
        "invoice_number": "Invoice No: INV-001",
    }
    parsed = ExtractorDraftResult(
        document_type=DocumentType.INVOICE,
        **{
            name: _present_field(name=name, snippet=snippet)
            for name, snippet in snippets.items()
        },
    )
    monkeypatch.setattr(
        "nova.agents.extractor.pytesseract.image_to_string",
        lambda image: "\n".join(snippets.values()),
    )

    result = ExtractorAgent(llm_client=FakeVisionClient(parsed), model="gpt-4o").extract(
        _loaded_document()
    )

    assert result.document_type == DocumentType.INVOICE
    assert set(result.fields) == set(snippets)
    assert all(field.is_present for field in result.fields.values())
    assert all(field.confidence >= 0.8 for field in result.fields.values())
    assert all(field.source_snippet for field in result.fields.values())
    assert result.latency_ms >= 0
    assert result.cost_usd > 0


def test_extractor_missing_fields_are_not_hallucinated(monkeypatch) -> None:
    present_snippet = "Invoice No: INV-001"
    fields = {
        "consignee_name": _missing_field("consignee_name"),
        "hs_code": _missing_field("hs_code"),
        "port_of_loading": _missing_field("port_of_loading"),
        "port_of_discharge": _missing_field("port_of_discharge"),
        "incoterms": _missing_field("incoterms"),
        "description_of_goods": _missing_field("description_of_goods"),
        "gross_weight": _missing_field("gross_weight"),
        "invoice_number": ExtractorFieldDraft(
            name="invoice_number",
            value="INV-001",
            confidence=0.92,
            source_page=1,
            source_snippet=present_snippet,
            reasoning="Directly labeled invoice number.",
            is_present=True,
        ),
    }
    parsed = ExtractorDraftResult(document_type=DocumentType.INVOICE, **fields)
    monkeypatch.setattr(
        "nova.agents.extractor.pytesseract.image_to_string",
        lambda image: present_snippet,
    )

    result = ExtractorAgent(llm_client=FakeVisionClient(parsed), model="gpt-4o").extract(
        _loaded_document()
    )

    missing_fields = [field for field in result.fields.values() if not field.is_present]
    assert missing_fields
    assert all(field.value is None for field in missing_fields)


def test_ocr_unavailable_does_not_flatten_confidence() -> None:
    field = ExtractorFieldDraft(
        name="hs_code",
        value="84713000",
        confidence=0.96,
        source_page=1,
        source_snippet="HS Code: 84713000",
        reasoning="Clearly labeled.",
        is_present=True,
    )

    guarded = penalize_unverified_snippet(field, "", document_id="doc-1")

    assert guarded.confidence == 0.96


def _missing_field(name: str) -> ExtractorFieldDraft:
    return ExtractorFieldDraft(
        name=name,
        value=None,
        confidence=0.0,
        source_page=1,
        source_snippet="",
        reasoning="Field is not visible in the document.",
        is_present=False,
    )


def _present_field(name: str, snippet: str) -> ExtractorFieldDraft:
    return ExtractorFieldDraft(
        name=name,
        value=snippet.split(": ", 1)[-1],
        confidence=0.9,
        source_page=1,
        source_snippet=snippet,
        reasoning="Directly labeled in the document.",
        is_present=True,
    )


def _loaded_document() -> LoadedDocument:
    return LoadedDocument(
        doc_id="doc-1",
        source_filename="invoice.png",
        page_count=1,
        pages=[
            PageImage(
                page_number=1,
                width=1000,
                height=1400,
                dpi=200,
                base64_image=_blank_png_base64(),
                quality_score=0.8,
                rotation_applied_deg=0,
                warnings=[],
            )
        ],
        original_bytes_hash="hash-1",
    )


def _blank_png_base64() -> str:
    image = Image.new("RGB", (20, 20), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
