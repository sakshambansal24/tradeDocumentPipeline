from pathlib import Path

import pytest

from nova.rules.engine import apply_rules
from nova.rules.loader import RuleLoadError, load_rules
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.rules import CustomerRuleSet, FieldRule, RuleType
from nova.schemas.validation import FieldValidationStatus


def test_exact_match_rule() -> None:
    result = _single_rule_result(
        "consignee_name",
        FieldRule(type=RuleType.EXACT, value="Globex Corporation Ltd"),
        " globex corporation ltd ",
    )

    assert result.status == FieldValidationStatus.MATCH


def test_allowed_values_rule() -> None:
    result = _single_rule_result(
        "incoterms",
        FieldRule(type=RuleType.ALLOWED_VALUES, values=["FOB", "CIF", "DAP"]),
        "CIF",
    )

    assert result.status == FieldValidationStatus.MATCH


def test_regex_rule() -> None:
    result = _single_rule_result(
        "hs_code",
        FieldRule(type=RuleType.REGEX, pattern=r"^\d{6,10}$"),
        "090121",
    )

    assert result.status == FieldValidationStatus.MATCH


def test_numeric_range_rule() -> None:
    result = _single_rule_result(
        "gross_weight",
        FieldRule(type=RuleType.NUMERIC_RANGE, min_kg=100, max_kg=25_000),
        "1,200 KG",
    )

    assert result.status == FieldValidationStatus.MATCH


def test_presence_rule_marks_missing_field() -> None:
    rules = CustomerRuleSet(
        customer_id="demo_customer",
        version="v1",
        fields={"invoice_number": FieldRule(type=RuleType.PRESENCE, required=True)},
    )
    extraction = _extraction({})

    result = apply_rules(extraction, rules)[0]

    assert result.status == FieldValidationStatus.MISSING


def test_loader_rejects_unknown_rule_keys(tmp_path: Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "demo_customer.yaml").write_text(
        """
customer_id: demo_customer
version: v1
fields:
  invoice_number:
    type: presence
    required: true
    unknown_key: nope
""",
        encoding="utf-8",
    )

    with pytest.raises(RuleLoadError):
        load_rules("demo_customer", rules_root=rules_dir)


def _single_rule_result(field_name: str, rule: FieldRule, value: str):
    rules = CustomerRuleSet(customer_id="demo_customer", version="v1", fields={field_name: rule})
    return apply_rules(extraction=_extraction({field_name: value}), rules=rules)[0]


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
