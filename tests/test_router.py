from nova.agents.router import RouterAgent
from nova.schemas.decision import DecisionType
from nova.schemas.validation import (
    FieldValidation,
    FieldValidationStatus,
    ValidationOverallStatus,
    ValidationResult,
)


class FakeDrafter:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.calls = 0

    def draft(self, *, validation: ValidationResult, discrepancies: list[FieldValidation]) -> str:
        message = self.messages[min(self.calls, len(self.messages) - 1)]
        self.calls += 1
        return message


def test_router_auto_approves_all_matches_without_llm_call() -> None:
    drafter = FakeDrafter(["should not be used"])
    decision = RouterAgent(drafter=drafter).decide(
        _validation(
            [
                _field("consignee_name", FieldValidationStatus.MATCH),
                _field("hs_code", FieldValidationStatus.MATCH),
                _field("invoice_number", FieldValidationStatus.MATCH),
            ],
            overall_status=ValidationOverallStatus.PASSED,
            validator_confidence=0.95,
        )
    )

    assert decision.decision == DecisionType.AUTO_APPROVE
    assert decision.drafted_message is None
    assert "All 3 required fields matched" in decision.reasoning
    assert drafter.calls == 0


def test_router_routes_uncertain_only_to_human_review_without_llm_call() -> None:
    drafter = FakeDrafter(["should not be used"])
    decision = RouterAgent(drafter=drafter).decide(
        _validation(
            [
                _field("consignee_name", FieldValidationStatus.MATCH),
                _field("incoterms", FieldValidationStatus.UNCERTAIN),
            ],
            overall_status=ValidationOverallStatus.NEEDS_REVIEW,
            validator_confidence=0.75,
        )
    )

    assert decision.decision == DecisionType.HUMAN_REVIEW
    assert decision.drafted_message is None
    assert "UNCERTAIN" in decision.reasoning
    assert "uncertain_fields_present" in decision.risk_flags
    assert drafter.calls == 0


def test_router_amends_critical_mismatch_with_grounded_draft() -> None:
    drafter = FakeDrafter(
        [
            (
                "Dear Supplier,\n"
                "For customer_id acme_corp and document_id doc-1, please amend "
                "hs_code. Found '1234', expected '^\\d{6,10}$'. "
                "Rule reference: regex:^\\d{6,10}$. "
                "Please correct and resubmit with the above changes."
            )
        ]
    )

    decision = RouterAgent(drafter=drafter).decide(
        _validation(
            [
                _field("hs_code", FieldValidationStatus.MISMATCH, found="1234"),
                _field("invoice_number", FieldValidationStatus.MATCH, found="INV-001"),
            ],
            overall_status=ValidationOverallStatus.FAILED,
            validator_confidence=0.95,
        )
    )

    assert decision.decision == DecisionType.AMEND
    assert decision.drafted_message is not None
    assert "hs_code" in decision.drafted_message
    assert "Critical mismatches detected" in decision.reasoning
    assert drafter.calls == 1


def test_router_falls_back_when_amendment_draft_mentions_unvalidated_field() -> None:
    drafter = FakeDrafter(
        [
            "Please fix hs_code and port_of_loading.",
            "Please fix hs_code and port_of_loading again.",
        ]
    )

    decision = RouterAgent(drafter=drafter).decide(
        _validation(
            [
                _field("hs_code", FieldValidationStatus.MISMATCH, found="1234"),
                _field("invoice_number", FieldValidationStatus.MATCH, found="INV-001"),
            ],
            overall_status=ValidationOverallStatus.FAILED,
            validator_confidence=0.4,
        )
    )

    assert decision.decision == DecisionType.AMEND
    assert decision.drafted_message is not None
    assert "hs_code" in decision.drafted_message
    assert "port_of_loading" not in decision.drafted_message
    assert "validator_confidence_low" in decision.risk_flags
    assert drafter.calls == 2


def _validation(
    fields: list[FieldValidation],
    *,
    overall_status: ValidationOverallStatus,
    validator_confidence: float,
) -> ValidationResult:
    return ValidationResult(
        extraction_id="doc-1",
        customer_id="acme_corp",
        rule_set_version="v1",
        field_results=fields,
        overall_status=overall_status,
        validator_confidence=validator_confidence,
    )


def _field(
    name: str,
    status: FieldValidationStatus,
    *,
    found: str | None = "value",
    expected: str | None = "expected",
    confidence: float | None = 0.95,
) -> FieldValidation:
    return FieldValidation(
        field_name=name,
        status=status,
        found_value=found,
        expected_value=expected,
        expected_rule="test_rule",
        reason="Test fixture.",
        extraction_confidence=confidence,
    )
