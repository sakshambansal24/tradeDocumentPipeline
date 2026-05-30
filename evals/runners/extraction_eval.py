import json
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from nova.agents.extractor import ExtractorAgent
from nova.ingestion import DocumentLoader

DATASET_DIR = Path("evals/datasets/extraction_v1")
GOLD_PATH = DATASET_DIR / "gold.jsonl"
PREDICTIONS_PATH = DATASET_DIR / "predictions.jsonl"


def run_extraction_eval(*, live: bool = False) -> dict[str, Any]:
    gold_rows = _load_jsonl(GOLD_PATH)
    predictions = _live_predictions(gold_rows) if live else _fixture_predictions()

    field_stats: dict[str, dict[str, int]] = {}
    correct_confidences: list[float] = []
    incorrect_confidences: list[float] = []
    hallucinations = 0
    misses = 0
    total_absent = 0
    total_present = 0

    for row in gold_rows:
        predicted_fields = predictions[row["doc_id"]]["fields"]
        for field_name, gold_field in row["fields"].items():
            stats = field_stats.setdefault(
                field_name,
                {"total": 0, "exact": 0, "fuzzy": 0, "hallucination": 0, "miss": 0},
            )
            stats["total"] += 1
            predicted = predicted_fields.get(field_name, {})
            gold_present = bool(gold_field["is_present"])
            predicted_present = bool(predicted.get("is_present"))

            if not gold_present:
                total_absent += 1
                if predicted_present:
                    hallucinations += 1
                    stats["hallucination"] += 1
                continue

            total_present += 1
            if not predicted_present:
                misses += 1
                stats["miss"] += 1
                incorrect_confidences.append(float(predicted.get("confidence", 0.0)))
                continue

            gold_value = _normalize(gold_field["value"])
            predicted_value = _normalize(predicted.get("value"))
            exact = gold_value == predicted_value
            fuzzy = fuzz.ratio(gold_value, predicted_value) > 90
            confidence = float(predicted.get("confidence", 0.0))

            if exact:
                stats["exact"] += 1
            if fuzzy:
                stats["fuzzy"] += 1
            if exact:
                correct_confidences.append(confidence)
            else:
                incorrect_confidences.append(confidence)

    per_field = {
        field_name: {
            "accuracy": _ratio(stats["exact"], stats["total"]),
            "fuzzy_accuracy": _ratio(stats["fuzzy"], stats["total"]),
            "hallucinations": stats["hallucination"],
            "misses": stats["miss"],
            "total": stats["total"],
        }
        for field_name, stats in field_stats.items()
    }
    mean_correct = _mean(correct_confidences)
    mean_incorrect = _mean(incorrect_confidences)
    return {
        "mode": "live" if live else "fixture",
        "document_count": len(gold_rows),
        "per_field": per_field,
        "hallucination_rate": _ratio(hallucinations, total_absent),
        "miss_rate": _ratio(misses, total_present),
        "mean_confidence_correct": mean_correct,
        "mean_confidence_incorrect": mean_incorrect,
        "confidence_delta": round(mean_correct - mean_incorrect, 4),
    }


def _live_predictions(gold_rows: list[dict[str, Any]]) -> dict[str, Any]:
    loader = DocumentLoader()
    extractor = ExtractorAgent()
    predictions: dict[str, Any] = {}
    for row in gold_rows:
        document = loader.load(row["path"])
        extraction = extractor.extract(document)
        predictions[row["doc_id"]] = {
            "fields": {
                name: {
                    "value": field.value,
                    "is_present": field.is_present,
                    "confidence": field.confidence,
                }
                for name, field in extraction.fields.items()
            }
        }
    return predictions


def _fixture_predictions() -> dict[str, Any]:
    return {row["doc_id"]: row for row in _load_jsonl(PREDICTIONS_PATH)}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _normalize(value: Any) -> str:
    return "" if value is None else str(value).strip().casefold()


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0
