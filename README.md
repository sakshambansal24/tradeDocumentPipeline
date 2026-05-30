# Nova Trade Document Pipeline

Multi-agent trade-document pipeline for GoComet Nova Part 1. It extracts structured fields from shipping documents, validates them against customer-specific rules, and routes each run to `AUTO_APPROVE`, `HUMAN_REVIEW`, or `AMEND`. The implementation emphasizes grounded evidence, confidence, observability, cost awareness, and evals.

## Reviewer Path

1. Read https://docs.google.com/document/d/1GsuuTrot-A2L1m-eJUh4PE9YCITq25rndVdWW_VjhEs/edit?tab=t.0 for the product narrative.
2. Read https://docs.google.com/document/d/1miH8ir7PN75Z8jLc0j_FTv_qPaZ-0negJIgMwxn6cEg/edit?tab=t.0 for the architecture diagram, failure modes, cost, latency, and trace design.
3. See my demo videos to follow the 2-3 minute demo.
4. Run the system locally and try the sample documents in `samples/`.

## Quick Start

Prereqs: Python 3.11+, Make, Node.js 20+, npm, and OpenAI API access.

```bash
cp .env.example .env
# Add OPENAI_API_KEY to .env. Langfuse keys are optional; tracing no-ops without them.
make setup
make dev
```

Run the backend:

```bash
source .venv/bin/activate
uvicorn nova.api.app:app --reload --app-dir src --port 8001
```

Run the UI:

```bash
cd ui
npm install
VITE_API_BASE_URL=http://localhost:8001 npm run dev
```

Run tests and evals:

```bash
make test
make lint
make eval
```

## Interesting Code

- `src/nova/agents/`: extractor, validator, and router agent boundaries.
- `src/nova/orchestration/`: LangGraph pipeline, stage events, and checkpointed state.
- `src/nova/schemas/`: strict Pydantic contracts for every agent handoff.
- `src/nova/rules/`: declarative ACME rule set and deterministic validation engine.
- `src/nova/storage/`: SQLite/SQLAlchemy persistence for documents, runs, validations, and decisions.
- `src/nova/query/`: grounded natural-language query layer using safe tool calls, not text-to-SQL.
- `src/nova/observability/`: Langfuse wrapper, structured logging, and cost metering.
- `evals/`: hand-labeled offline eval dataset, runners, and result artifacts.
- `ui/`: React/Vite operator workflow.
- `samples/`: one clean and one deliberately degraded trade document.

## Known Limitations

- SQLite and in-memory LangGraph checkpoints are POC choices; production needs Postgres and durable checkpoints.
- The eval dataset is intentionally small for the assignment. It proves the harness, not broad model quality.
- UI upload is synchronous today; production should return a run ID immediately and stream/poll stage updates.
- Langfuse tracing requires keys. Without them, local tracing falls back to no-op while JSON logs still work.
- The grounded query layer only supports curated tools. It is safer than text-to-SQL, but not a general analytics engine.
- Email ingestion and supplier reply handling are not built yet; those belong to the next workflow layer.
