# Nova Evals

## Offline Eval

`make eval` runs the extraction and routing evals against `evals/datasets/extraction_v1`.
The dataset contains five hand-labeled ACME commercial-invoice examples, mixing clean and
messy documents. `gold.jsonl` is the source of truth; `predictions.jsonl` is a committed
fixture so the eval can run offline without API keys or token spend. Use
`python -m evals.runners.run_all --live` when you want to call the real ExtractorAgent.

Extraction metrics:
- Per-field exact accuracy with case-insensitive trimmed comparison.
- Per-field fuzzy accuracy with RapidFuzz ratio greater than 90.
- Hallucination rate when gold says a field is absent but the model marks it present.
- Miss rate when gold says a field is present but the model marks it absent.
- Mean confidence on correct vs. incorrect extractions. The delta is the calibration signal.

Decision metrics:
- Full validator/router decision accuracy against the hand-labeled expected outcome.
- The eval uses deterministic validation adjudication and amendment drafting so routing is
  measured without live LLM variability.

This eval is a measurement harness, not a training target. Do not tune prompts against this
small dataset; add new blind documents before using the score to justify prompt changes.

## Online Metric

Production autopilot safety metric: percentage of `AUTO_APPROVE` decisions confirmed correct
by human review.

Sampling plan: review 20% of auto-approved runs weekly, with a minimum of 30 sampled runs per
customer per week when volume allows. A reviewer checks extracted fields, validation outcome,
and whether the document should truly have bypassed manual review.

Action threshold: if confirmed-correct auto-approves fall below 98% for any customer in a week,
disable auto-approve for that customer, route to human review, and inspect the failing fields
before re-enabling.
