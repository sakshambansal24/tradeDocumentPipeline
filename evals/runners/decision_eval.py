import json
from pathlib import Path
from typing import Any

from nova.agents.router import RouterAgent
from nova.agents.validator import LLMValidationVerdict, ValidatorAgent
from nova.schemas.extraction import DocumentType, ExtractedField, ExtractionResult
from nova.schemas.validation import FieldValidation, FieldValidationStatus

DATASET_DIR = Path("evals/datasets/extraction_v1")
GOLD_PATH = DATASET_DIR / "gold.jsonl"
PREDICTIONS_PATH = DATASET_DIR / "predictions.jsonl"


class EvalAdjudicator:
    def adjudicate(self, validation: FieldValidation) -> LLMValidationVerdict:
        return LLMValidationVerdict(
            status=FieldValidationStatus.MISMATCH,
            reason="Eval adjudicator treats ambiguous business values as mismatches.",
        )


class EvalDrafter:
    def draft(self, *, validation, discrepancies: list[FieldValidation]) -> str:
        fields = ", ".join(field.field_name for field in discrepancies)
        return (
            "Dear Supplier,\n\n"
            f"Please correct and resubmit the document for {validation.extraction_id}. "
            f"Discrepant fields: {fields}."
        )


def run_decision_eval() -> dict[str, Any]:
    gold_rows = _load_jsonl(GOLD_PATH)
    predictions = {row["doc_id"]: row for row in _load_jsonl(PREDICTIONS_PATH)}
    validator = ValidatorAgent(adjudicator=EvalAdjudicator())
    router = RouterAgent(drafter=EvalDrafter())

    cases = []
    passed = 0
    for row in gold_rows:
        extraction = _build_extraction(row["doc_id"], predictions[row["doc_id"]]["fields"])
        validation = validator.validate(extraction, customer_id=row["customer_id"])
        decision = router.decide(validation)
        actual = decision.decision.value
        expected = row["expected_decision"]
        ok = actual == expected
        passed += int(ok)
        cases.append(
            {
                "doc_id": row["doc_id"],
                "expected": expected,
                "actual": actual,
                "passed": ok,
                "risk_flags": decision.risk_flags,
            }
        )

    return {
        "document_count": len(gold_rows),
        "passed": passed,
        "failed": len(gold_rows) - passed,
        "accuracy": round(passed / len(gold_rows), 4) if gold_rows else 0.0,
        "cases": cases,
    }


def _build_extraction(doc_id: str, fields: dict[str, Any]) -> ExtractionResult:
    return ExtractionResult(
        document_id=doc_id,
        document_type=DocumentType.INVOICE,
        fields={
            name: ExtractedField(
                name=name,
                value=field["value"],
                confidence=float(field["confidence"]),
                source_page=1,
                source_snippet=field["value"] or "",
                reasoning="Eval fixture prediction.",
                is_present=bool(field["is_present"]),
            )
            for name, field in fields.items()
        },
        model_used="eval-fixture",
        latency_ms=0,
        cost_usd=0.0,
        raw_response_id=f"eval-{doc_id}",
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
