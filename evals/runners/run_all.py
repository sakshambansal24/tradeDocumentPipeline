import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from evals.runners.decision_eval import run_decision_eval
from evals.runners.extraction_eval import run_extraction_eval


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Nova offline evals.")
    parser.add_argument("--live", action="store_true", help="Call the live ExtractorAgent.")
    args = parser.parse_args()

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "extraction": run_extraction_eval(live=args.live),
        "decision": run_decision_eval(),
    }
    output_path = _write_result(report)
    _print_report(report, output_path)


def _write_result(report: dict) -> Path:
    results_dir = Path("evals/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir / f"{timestamp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    return path


def _print_report(report: dict, output_path: Path) -> None:
    extraction = report["extraction"]
    decision = report["decision"]
    print("Nova Eval Report")
    print("================")
    print(f"Mode: {extraction['mode']}")
    print(f"Documents: {extraction['document_count']}")
    print(f"Hallucination rate: {extraction['hallucination_rate']:.2%}")
    print(f"Miss rate: {extraction['miss_rate']:.2%}")
    print(f"Mean confidence, correct: {extraction['mean_confidence_correct']:.3f}")
    print(f"Mean confidence, incorrect: {extraction['mean_confidence_incorrect']:.3f}")
    print(f"Confidence delta: {extraction['confidence_delta']:.3f}")
    print("")
    print("Per-field extraction")
    for field_name, stats in extraction["per_field"].items():
        print(
            f"- {field_name}: exact={stats['accuracy']:.2%}, "
            f"fuzzy={stats['fuzzy_accuracy']:.2%}, "
            f"misses={stats['misses']}, hallucinations={stats['hallucinations']}"
        )
    print("")
    print(
        f"Decision accuracy: {decision['accuracy']:.2%} "
        f"({decision['passed']}/{decision['document_count']} passed)"
    )
    print(f"JSON artifact: {output_path}")


if __name__ == "__main__":
    main()
