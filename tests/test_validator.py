from nova.agents.validator import LLMValidationVerdict, ValidatorAgent
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.rules import CustomerRuleSet, FieldRule, RuleType
from nova.schemas.validation import FieldValidation, FieldValidationStatus, ValidationOverallStatus


class FakeAdjudicator:
    def __init__(self) -> None:
        self.calls: list[FieldValidation] = []

    def adjudicate(self, validation: FieldValidation) -> LLMValidationVerdict:
        self.calls.append(validation)
        return LLMValidationVerdict(
            status=FieldValidationStatus.MATCH,
            reason="The names are equivalent despite abbreviation.",
        )


def test_validator_hybrid_flow_merges_uncertain_llm_verdict() -> None:
    rules = CustomerRuleSet(
        customer_id="demo_customer",
        version="v1",
        fields={
            "consignee_name": FieldRule(
                type=RuleType.EXACT,
                value="Globex Corporation Limited",
                case_insensitive=True,
                trim=True,
            ),
            "incoterms": FieldRule(type=RuleType.ALLOWED_VALUES, values=["FOB", "CIF", "DAP"]),
            "hs_code": FieldRule(type=RuleType.REGEX, pattern=r"^\d{6,10}$"),
            "gross_weight": FieldRule(type=RuleType.NUMERIC_RANGE, min_kg=100, max_kg=25_000),
            "invoice_number": FieldRule(type=RuleType.PRESENCE, required=True),
        },
    )
    adjudicator = FakeAdjudicator()
    extraction = _extraction(
        {
            "consignee_name": "Globex Corp Ltd",
            "incoterms": "CIF",
            "hs_code": "090121",
            "gross_weight": "1200 KG",
            "invoice_number": "INV-001",
        }
    )

    result = ValidatorAgent(adjudicator=adjudicator).validate(
        extraction,
        customer_id="demo_customer",
        rules=rules,
    )

    by_field = {field.field_name: field for field in result.field_results}
    assert len(adjudicator.calls) == 1
    assert by_field["consignee_name"].status == FieldValidationStatus.MATCH
    assert by_field["incoterms"].status == FieldValidationStatus.MATCH
    assert by_field["hs_code"].status == FieldValidationStatus.MATCH
    assert by_field["gross_weight"].status == FieldValidationStatus.MATCH
    assert by_field["invoice_number"].status == FieldValidationStatus.MATCH
    assert result.overall_status == ValidationOverallStatus.PASSED
    assert result.validator_confidence == 0.75


def _extraction(values: dict[str, str]) -> ExtractionResult:
    return ExtractionResult(
        document_id="doc-1",
        document_type=DocumentType.INVOICE,
        fields={
            field_name: ExtractedField(
                name=field_name,
                value=value,
                confidence=0.95,
                source_page=1,
                source_snippet=value,
                reasoning="Test fixture.",
                is_present=True,
            )
            for field_name, value in values.items()
        },
        model_used="test-model",
        latency_ms=1,
        cost_usd=0.0,
        raw_response_id="response-1",
    )
