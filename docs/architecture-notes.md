# Architecture Notes

## Stack Choices

- **Language + framework: Python + FastAPI.** Matches Nova's backend stack, gives strong Pydantic contracts, and keeps the later API layer straightforward.
- **Agent orchestration: LangGraph.** The pipeline is stateful, staged, and failure-prone; LangGraph gives explicit nodes, retries, persisted state, and traceable handoffs without inventing a runner.
- **Vision model strategy: GPT-4o primary, Gemini Flash/Pro fallback.** GPT-4o is the quality-first extractor for messy scans; Gemini Flash is cheaper and faster for retries or lower-risk docs, while Pro is a quality fallback when provider availability or confidence requires it.
- **Non-vision LLM: GPT-4.1-mini or equivalent small reasoning model.** Validation should be deterministic rules first, with a cheaper model reserved for ambiguity and explanations; routing needs reliable business reasoning but not vision.
- **Database: SQLite for POC.** SQLite is enough for local audit/query behavior; Postgres is needed for concurrent users and tenant isolation, while ClickHouse becomes useful for high-volume analytics, dashboards, and cost/latency reporting.
- **Frontend: React + Vite.** Part 2 needs an operator-grade workflow with interactive states, discrepancy drilldowns, and editable drafts; React is a better product signal than Streamlit.
- **Observability: Langfuse from day one.** Each run should trace model calls, response IDs, cost, latency, confidence, validation failures, and routing decisions so failures are debuggable and costs are visible.

## Three-Agent Boundary

Why not one prompt? Extraction, validation, and decisioning have different inputs, failure modes, cost profiles, and audit requirements.

Why not five? This POC needs sharp ownership, not ceremony; splitting normalization, drafting, or policy into extra agents would add coordination before the core chain works.

- **Extractor owns** reading document pixels/text and producing grounded fields with evidence, confidence, and absence signals.
- **Extractor does not own** customer rule interpretation, approval decisions, or amendment language.
- **Validator owns** comparing extracted facts against customer-specific rules and producing match, mismatch, uncertain, or missing results.
- **Validator does not own** reading documents, inventing values, or deciding the final disposition.
- **Router owns** turning validation outcomes into auto-approve, human review, or amendment, including a human-reviewable draft when needed.
- **Router does not own** changing validation outcomes, overriding missing evidence, or sending anything automatically.

The handoff should be structured Pydantic state, not conversational memory.

## Hallucination Strategy

Structured output is necessary but not sufficient. The Extractor must be evidence-grounded: every field carries `is_present`, confidence, `source_page`, and `source_snippet`, and the prompt must explicitly allow `null` when absent. Downstream validation treats missing snippets, low confidence, contradictory values, or unsupported "present" fields as uncertainty, never approval. The field catalog is constrained, extra schema fields are forbidden, raw response IDs are logged, and evals should include documents where expected fields are absent so invented consignee names, HS codes, Incoterms, or invoice numbers are caught early.
