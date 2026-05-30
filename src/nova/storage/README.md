Canonical queries for the next layer:
- List runs by `customer_id`, newest first, to show a customer work queue.
- Filter by `decision=AMEND` to find supplier amendment requests pending action.
- Filter by `status=NEEDS_REVIEW` to show CG operator review backlog.
- Filter by `(status, completed_at)` for operational dashboards and SLA aging.
- Join run records to documents/extractions when drilling into evidence for one shipment.
SQLite is fine for the POC; Postgres is needed for concurrent users and tenant isolation.
ClickHouse becomes useful for high-volume analytics over latency, cost, and outcomes.
