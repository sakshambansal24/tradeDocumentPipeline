Graph: `ingest -> extract -> validate -> route -> persist`.
After `extract`, the graph checks required-field quality before spending validator/router cost.
If more than half the required fields are missing or confidence `<0.3`, it routes to `human_handoff`.
State is a typed `PipelineState` carrying each Pydantic artifact as the pipeline progresses.
Stage history records completed/failed stages with timestamps and error messages.
The POC uses LangGraph `MemorySaver` checkpoints for resume/inspection.
Production should swap this to `SqliteSaver` or `PostgresSaver` for durable crash recovery.
Nodes are thin adapters around ingestion output, Extractor, Validator, Router, and persistence.
The runner owns run IDs, checkpoint config, failure recording, and `PipelineRun` conversion.
Every node emits tracer events through `tracer.emit(stage, event_type, payload)`.
