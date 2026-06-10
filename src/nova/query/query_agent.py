import json
import re
from datetime import UTC, date, datetime
from typing import Any, Protocol

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import JSON

from nova.schemas.decision import DecisionType
from nova.schemas.extraction import DocumentType
from nova.schemas.pipeline import PipelineRunStatus, StageName, StageStatus
from nova.schemas.query import (
    QueryAnswer,
    QueryEvidence,
    SqlQueryPlan,
)
from nova.schemas.shipment import CrossFieldStatus, ShipmentStatus
from nova.schemas.validation import FieldValidationStatus, ValidationOverallStatus
from nova.settings import get_settings
from nova.storage import session_scope
from nova.storage.models import Base

ALLOWED_TABLES = {
    "customers",
    "decisions",
    "documents",
    "extractions",
    "pipeline_runs",
    "shipments",
    "validations",
}
ALLOWED_TABLE_FUNCTIONS = {"json_each"}
ALLOWED_TABLE_ORDER = (
    "shipments",
    "pipeline_runs",
    "documents",
    "extractions",
    "validations",
    "decisions",
    "customers",
)
CATEGORICAL_COLUMNS = {
    "content_type",
    "customer_id",
    "decision",
    "overall_status",
    "rule_set_version",
    "source_filename",
    "status",
}
JSON_DOMAIN_LEAF_KEYS = {
    "decision",
    "document_type",
    "field_name",
    "overall_consistent",
    "status",
}
MAX_ROWS = 100
UNSUPPORTED_DOMAIN_VALUES = {
    "AUTO": "Use AUTO_APPROVE for decisions or PASSED for validation status.",
    "REJECTED": (
        "There is no REJECTED decision; use AMEND, HUMAN_REVIEW, "
        "or failing validation statuses."
    ),
    "REVIEW": "Use HUMAN_REVIEW for routing decisions or NEEDS_REVIEW for validation/run status.",
}

SQL_SYSTEM_PROMPT = """You convert user questions into one read-only SQLite SELECT query.
Return only structured output matching the requested schema.

Query planning rules:
- Generate exactly one SELECT statement.
- Use only the tables, columns, JSON paths, and values supplied in the runtime
  schema context.
- Never use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, PRAGMA, ATTACH,
  DETACH, multiple statements, or SQL comments.
- Prefer explicit column names instead of SELECT *.
- Add LIMIT 100 for non-aggregate result sets.
- Use json_each(...) and json_extract(...) for JSON arrays/objects when needed.
- For shipment-level questions, prefer shipments.
- For "last shipment" or "latest shipment", query shipments ordered by
  COALESCE(completed_at, triggered_at) DESC, unless the user explicitly asks for a
  document/pipeline run.
- For "why did/explain why the last/latest shipment needed review/amendment",
  first retrieve the last/latest shipment without filtering by review/amendment
  status, then inspect shipments.status, shipments.overall_decision,
  shipments.cross_validation_result, and draft_reply.
- Only filter shipments by status or overall_decision when the wording asks for
  "the last shipment that/which was ..." or "shipments with ..." a specific outcome.
- Use pipeline_runs for document/run-level questions such as stage history, trace,
  per-document extraction, validation, document decision, and document cost.
- Join shipments to pipeline_runs on pipeline_runs.shipment_id = shipments.shipment_id
  only when a shipment question also needs document-run details, filenames, stage
  history, extraction, validation, or per-document cost.
- For "this week", use the current UTC date context supplied by the user.
- For "auto approved" decisions, use AUTO_APPROVE, not APPROVED.
- For "rejected" or failure reason questions, inspect validation field results;
  there is no REJECTED decision value."""

SUMMARY_SYSTEM_PROMPT = """You summarize grounded SQL query results for Nova document pipeline data.
Use only the provided question, SQL, and rows. Do not invent missing facts.
Mention when the result set is empty. Keep the answer concise.
Translate stored enum values into user-friendly language:
- AUTO_APPROVE -> auto approved
- HUMAN_REVIEW -> sent to human review
- AMEND -> amendment required
- PASSED -> passed validation
- FAILED -> failed validation
- NEEDS_REVIEW -> needs review
- MATCH -> matched
- MISMATCH -> mismatched
- MISSING -> missing
- UNCERTAIN -> uncertain
Do not expose raw enum names unless the user explicitly asks for stored values."""


class QueryPlanner(Protocol):
    def plan(self, question: str, *, schema_context: str = "") -> SqlQueryPlan:
        ...


class QuerySummarizer(Protocol):
    def summarize(self, *, question: str, sql: str, rows: list[dict[str, Any]]) -> str:
        ...


class OpenAIQueryPlanner:
    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.non_vision_model
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def plan(self, question: str, *, schema_context: str = "") -> SqlQueryPlan:
        now = datetime.now(UTC)
        dated_question = (
            f"Current UTC datetime: {now.isoformat()}\n"
            f"Current UTC date: {now.date().isoformat()}\n\n"
            f"Runtime schema context:\n{schema_context}\n\n"
            f"Question: {question}"
        )
        try:
            response = self._client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": SQL_SYSTEM_PROMPT},
                    {"role": "user", "content": dated_question},
                ],
                text_format=SqlQueryPlan,
            )
        except OpenAIError as exc:
            raise QueryAgentError(f"Query planning failed: {exc}") from exc

        if response.output_parsed is None:
            raise QueryAgentError("Query planning returned no parsed output")
        return response.output_parsed


class QuerySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)


class OpenAIQuerySummarizer:
    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.non_vision_model
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def summarize(self, *, question: str, sql: str, rows: list[dict[str, Any]]) -> str:
        prompt = {
            "question": question,
            "sql": sql,
            "rows": rows,
            "row_count": len(rows),
        }
        try:
            response = self._client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(prompt, default=str)},
                ],
                text_format=QuerySummary,
            )
        except OpenAIError as exc:
            raise QueryAgentError(f"Query summarization failed: {exc}") from exc

        if response.output_parsed is None:
            raise QueryAgentError("Query summarization returned no parsed output")
        return response.output_parsed.answer


class QueryAgentError(Exception):
    pass


class QueryAgent:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        planner: QueryPlanner | None = None,
        summarizer: QuerySummarizer | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.planner = planner or OpenAIQueryPlanner()
        self.summarizer = summarizer or OpenAIQuerySummarizer()

    def ask(self, question: str) -> QueryAnswer:
        with session_scope(self.session_factory) as session:
            override_sql = deterministic_query_for(question)
            if override_sql is None:
                schema_context = build_schema_context(session)
                plan = self.planner.plan(question, schema_context=schema_context)
                sql = validate_sql(plan.sql)
            else:
                sql = validate_sql(override_sql)
            rows = execute_sql(session, sql)

        evidence = QueryEvidence(sql=sql, rows=rows, row_count=len(rows))
        answer = self.summarizer.summarize(question=question, sql=sql, rows=rows)
        return QueryAnswer(answer=answer, evidence=evidence)


def deterministic_query_for(question: str) -> str | None:
    if is_latest_shipment_explanation_question(question):
        return (
            "SELECT shipment_id, email_id, customer_id, subject, status, "
            "overall_decision, cross_validation_result, draft_reply, triggered_at, completed_at "
            "FROM shipments "
            "ORDER BY COALESCE(completed_at, triggered_at) DESC "
            "LIMIT 1"
        )
    return None


def is_latest_shipment_explanation_question(question: str) -> bool:
    normalized = re.sub(r"\s+", " ", question.casefold()).strip()
    if "shipment" not in normalized:
        return False
    if not re.search(r"\b(last|latest)\b", normalized):
        return False
    if not re.search(r"\b(why|explain|reason)\b", normalized):
        return False
    if not re.search(r"\b(review|amend|amendment|action)\b", normalized):
        return False
    return re.search(r"\b(last|latest) shipment (that|which)\b", normalized) is None


def build_schema_context(session: Session) -> str:
    lines = ["Database tables and columns:"]
    lines.extend(table_schema_lines())

    relationship_lines = foreign_key_lines()
    if relationship_lines:
        lines.append("")
        lines.append("Foreign keys:")
        lines.extend(relationship_lines)

    json_lines = observed_json_schema_lines(session)
    if json_lines:
        lines.append("")
        lines.append("Observed JSON paths:")
        lines.extend(json_lines)

    known_domain_lines = known_domain_value_lines()
    if known_domain_lines:
        lines.append("")
        lines.append("Known domain values:")
        lines.extend(known_domain_lines)

    domain_lines = observed_domain_value_lines(session)
    if domain_lines:
        lines.append("")
        lines.append("Observed domain values:")
        lines.extend(domain_lines)

    return "\n".join(lines)


def table_schema_lines() -> list[str]:
    lines = []
    for table_name in ALLOWED_TABLE_ORDER:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        column_names = ", ".join(column.name for column in table.columns)
        lines.append(f"- {table_name}({column_names})")
    return lines


def foreign_key_lines() -> list[str]:
    lines = []
    for table_name in ALLOWED_TABLE_ORDER:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        for column in table.columns:
            for foreign_key in column.foreign_keys:
                target = foreign_key.column
                lines.append(
                    f"- {table_name}.{column.name} -> {target.table.name}.{target.name}"
                )
    return lines


def observed_json_schema_lines(session: Session) -> list[str]:
    lines = []
    for table_name, column_name in json_columns():
        samples = json_samples(session, table_name=table_name, column_name=column_name)
        paths = sorted({path for sample in samples for path in collect_json_paths(sample)})
        if paths:
            lines.append(f"- {table_name}.{column_name}: {', '.join(paths[:50])}")
    return lines


def observed_domain_value_lines(session: Session) -> list[str]:
    lines = []
    for table_name in ALLOWED_TABLE_ORDER:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        for column in table.columns:
            if column.name not in CATEGORICAL_COLUMNS:
                continue
            values = distinct_column_values(session, table_name=table_name, column_name=column.name)
            if values:
                lines.append(f"- {table_name}.{column.name}: {', '.join(values)}")

    json_values = observed_json_domain_values(session)
    for location, values in sorted(json_values.items()):
        if values:
            lines.append(f"- {location}: {', '.join(sorted(values)[:20])}")
    return lines


def known_domain_value_lines() -> list[str]:
    return [
        enum_values_line("pipeline_runs.status", PipelineRunStatus),
        enum_values_line("pipeline_runs.decision", DecisionType),
        enum_values_line("decisions.decision", DecisionType),
        enum_values_line("shipments.status", ShipmentStatus),
        enum_values_line("shipments.overall_decision.decision", DecisionType),
        enum_values_line("validations.overall_status", ValidationOverallStatus),
        enum_values_line("validations.field_results[].status", FieldValidationStatus),
        enum_values_line("extractions.payload.document_type", DocumentType),
        enum_values_line("pipeline_runs.stage_history[].stage", StageName),
        enum_values_line("pipeline_runs.stage_history[].status", StageStatus),
        enum_values_line(
            "shipments.cross_validation_result.checked_fields[].status",
            CrossFieldStatus,
        ),
    ]


def enum_values_line(location: str, enum_type) -> str:
    return f"- {location}: {', '.join(item.value for item in enum_type)}"


def json_columns() -> list[tuple[str, str]]:
    columns = []
    for table_name in ALLOWED_TABLE_ORDER:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        for column in table.columns:
            if isinstance(column.type, JSON):
                columns.append((table_name, column.name))
    return columns


def json_samples(
    session: Session,
    *,
    table_name: str,
    column_name: str,
    limit: int = 20,
) -> list[Any]:
    rows = session.execute(
        text(
            f"SELECT {column_name} AS value "
            f"FROM {table_name} "
            f"WHERE {column_name} IS NOT NULL "
            f"LIMIT :limit"
        ),
        {"limit": limit},
    ).mappings()
    samples = []
    for row in rows:
        samples.append(parse_json_value(row["value"]))
    return samples


def parse_json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def collect_json_paths(value: Any, *, prefix: str = "$", depth: int = 0) -> set[str]:
    if depth > 4:
        return set()
    if isinstance(value, dict):
        paths = set()
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}"
            paths.add(child_prefix)
            paths.update(collect_json_paths(child, prefix=child_prefix, depth=depth + 1))
        return paths
    if isinstance(value, list):
        paths = {f"{prefix}[]"}
        for item in value[:5]:
            paths.update(collect_json_paths(item, prefix=f"{prefix}[]", depth=depth + 1))
        return paths
    return set()


def distinct_column_values(
    session: Session,
    *,
    table_name: str,
    column_name: str,
    limit: int = 20,
) -> list[str]:
    rows = session.execute(
        text(
            f"SELECT DISTINCT {column_name} AS value "
            f"FROM {table_name} "
            f"WHERE {column_name} IS NOT NULL "
            f"ORDER BY {column_name} "
            f"LIMIT :limit"
        ),
        {"limit": limit},
    ).mappings()
    return [str(row["value"]) for row in rows if row["value"] is not None]


def observed_json_domain_values(session: Session) -> dict[str, set[str]]:
    values_by_path: dict[str, set[str]] = {}
    for table_name, column_name in json_columns():
        for sample in json_samples(session, table_name=table_name, column_name=column_name):
            collect_json_leaf_values(
                sample,
                location=f"{table_name}.{column_name}",
                values_by_path=values_by_path,
            )
    return values_by_path


def collect_json_leaf_values(
    value: Any,
    *,
    location: str,
    values_by_path: dict[str, set[str]],
    depth: int = 0,
) -> None:
    if depth > 4:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in JSON_DOMAIN_LEAF_KEYS and not isinstance(child, dict | list):
                values_by_path.setdefault(child_location, set()).add(str(child))
            collect_json_leaf_values(
                child,
                location=child_location,
                values_by_path=values_by_path,
                depth=depth + 1,
            )
        return
    if isinstance(value, list):
        for item in value[:20]:
            collect_json_leaf_values(
                item,
                location=f"{location}[]",
                values_by_path=values_by_path,
                depth=depth + 1,
            )


def validate_sql(sql: str) -> str:
    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if not stripped:
        raise QueryAgentError("Generated SQL was empty")
    if ";" in stripped:
        raise QueryAgentError("Generated SQL must contain exactly one statement")
    if "--" in stripped or "/*" in stripped or "*/" in stripped:
        raise QueryAgentError("Generated SQL comments are not allowed")

    normalized = re.sub(r"\s+", " ", stripped).casefold()
    if not normalized.startswith("select "):
        raise QueryAgentError("Generated SQL must be a SELECT statement")

    blocked = (
        "alter",
        "attach",
        "create",
        "delete",
        "detach",
        "drop",
        "insert",
        "pragma",
        "replace",
        "update",
        "vacuum",
    )
    if re.search(rf"\b({'|'.join(blocked)})\b", normalized):
        raise QueryAgentError("Generated SQL contains a prohibited operation")
    reject_unsupported_domain_values(stripped)

    referenced_relations = extract_referenced_relations(stripped)
    if not referenced_relations:
        raise QueryAgentError("Generated SQL must reference at least one allowed table")
    unknown_tables = referenced_relations - ALLOWED_TABLES - ALLOWED_TABLE_FUNCTIONS
    if unknown_tables:
        names = ", ".join(sorted(unknown_tables))
        raise QueryAgentError(f"Generated SQL referenced unsupported table(s): {names}")

    if not re.search(r"\blimit\b", normalized):
        stripped = f"{stripped} LIMIT {MAX_ROWS}"
    return stripped


def reject_unsupported_domain_values(sql: str) -> None:
    if uses_approved_as_decision(sql):
        raise QueryAgentError(
            "Generated SQL used unsupported domain value APPROVED. "
            "Use AUTO_APPROVE for auto-approved decisions."
        )

    quoted_literals = {
        match.group(1).strip().casefold()
        for match in re.finditer(r"'([^']*)'", sql)
    }
    for value, message in UNSUPPORTED_DOMAIN_VALUES.items():
        if value.casefold() in quoted_literals:
            raise QueryAgentError(
                f"Generated SQL used unsupported domain value {value}. {message}"
            )


def uses_approved_as_decision(sql: str) -> bool:
    return re.search(
        r"(?:\bdecision\b|[\w.]+\.decision)\s*(?:=|in\s*\()\s*'APPROVED'",
        sql,
        flags=re.IGNORECASE,
    ) is not None


def extract_referenced_relations(sql: str) -> set[str]:
    relations: set[str] = set()
    clause_pattern = re.compile(
        r"\bfrom\s+(.+?)(?=\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for clause_match in clause_pattern.finditer(sql):
        clause = clause_match.group(1)
        for relation_part in re.split(r"\bjoin\b|,", clause, flags=re.IGNORECASE):
            relation_match = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)", relation_part)
            if relation_match:
                relations.add(relation_match.group(1).casefold())
    return relations


def execute_sql(session: Session, sql: str) -> list[dict[str, Any]]:
    result = session.execute(text(sql))
    rows = result.mappings().fetchmany(MAX_ROWS)
    return [
        {key: serialize_sql_value(value) for key, value in row.items()}
        for row in rows
    ]


def serialize_sql_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
