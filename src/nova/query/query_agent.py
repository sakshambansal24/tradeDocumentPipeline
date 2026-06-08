import json
import re
from datetime import UTC, date, datetime
from typing import Any, Protocol

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nova.schemas.query import (
    QueryAnswer,
    QueryEvidence,
    SqlQueryPlan,
)
from nova.settings import get_settings
from nova.storage import session_scope

ALLOWED_TABLES = {
    "customers",
    "decisions",
    "documents",
    "extractions",
    "pipeline_runs",
    "validations",
}
ALLOWED_TABLE_FUNCTIONS = {"json_each"}
MAX_ROWS = 100
UNSUPPORTED_DOMAIN_VALUES = {
    "APPROVED": "Use AUTO_APPROVE for auto-approved decisions.",
    "AUTO": "Use AUTO_APPROVE for decisions or PASSED for validation status.",
    "REJECTED": "There is no REJECTED decision; use AMEND, HUMAN_REVIEW, or failing validation statuses.",
    "REVIEW": "Use HUMAN_REVIEW for routing decisions or NEEDS_REVIEW for validation/run status.",
}

SQL_SYSTEM_PROMPT = """You convert user questions into one read-only SQLite SELECT query.
Return only structured output matching the requested schema.

Database schema:
- Generate exactly one SELECT statement.
- Use only these tables and columns:
  documents(id, filename, content_type, uploaded_at, source_hash, page_count)
  extractions(id, document_id, model_used, latency_ms, cost_usd, raw_response_id, created_at, payload)
  validations(id, extraction_id, customer_id, rule_set_version, overall_status, validator_confidence, created_at, field_results)
  decisions(id, validation_id, decision, reasoning, drafted_message, risk_flags, created_at)
  pipeline_runs(id, run_id, document_id, customer_id, status, decision, decision_details, started_at, completed_at, cost_total_usd, stage_history, trace_id)
  customers(id, name, rule_set_path, created_at)

JSON keys stored in the database:
- extractions.payload:
  document_id, document_type, fields, model_used, latency_ms, cost_usd, raw_response_id
- extractions.payload.fields is an object keyed by field name. Valid field names are:
  consignee_name, hs_code, port_of_loading, port_of_discharge, incoterms,
  description_of_goods, gross_weight, invoice_number
- Each extractions.payload.fields.<field_name> object has keys:
  name, value, confidence, source_page, source_snippet, reasoning, is_present
- validations.field_results is a JSON array. Use json_each(validations.field_results)
  and json_extract(field.value, '$.key') to inspect it.
- Each validations.field_results item has keys:
  field_name, status, found_value, expected_value, expected_rule, reason, extraction_confidence
- pipeline_runs.decision_details has keys:
  decision, reasoning, drafted_message, risk_flags
- pipeline_runs.stage_history is a JSON array. Each item has keys:
  stage, status, started_at, completed_at, latency_ms, cost_usd, trace_id,
  message, error_message
- decisions.risk_flags is a JSON array of strings.

Exact enum/domain values:
- pipeline_runs.decision and decisions.decision can only be:
  AUTO_APPROVE, HUMAN_REVIEW, AMEND
- validations.overall_status can only be:
  PASSED, FAILED, NEEDS_REVIEW
- validations.field_results[*].status can only be:
  MATCH, MISMATCH, UNCERTAIN, MISSING
- pipeline_runs.status can only be:
  PENDING, RUNNING, COMPLETED, FAILED, NEEDS_REVIEW
- stage_history[*].stage can only be:
  INGESTION, EXTRACTION, VALIDATION, ROUTING, STORAGE, QUERY
- stage_history[*].status can only be:
  PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
- extractions.payload.document_type can only be:
  BOL, INVOICE, PACKING_LIST, COO, UNKNOWN
- customers currently include:
  acme_corp

Customer rule values for acme_corp:
- rule_set_version: 2026-05-30
- configured field names:
  consignee_name, incoterms, hs_code, gross_weight, invoice_number,
  port_of_loading, port_of_discharge, description_of_goods
- critical fields:
  consignee_name, gross_weight, hs_code, invoice_number
- consignee_name exact expected value:
  ACME Corporation Pvt Ltd
- incoterms allowed values:
  FOB, CIF, DAP
- hs_code rule:
  regex "^\\d{6,10}$"
- gross_weight rule:
  min_kg 100, max_kg 25000
- invoice_number, port_of_loading, port_of_discharge, description_of_goods:
  presence required
- cross-field rule india_loading_requires_8_digit_hs_code:
  if port_of_loading is one of IN, India, Nhava Sheva, Mundra, Chennai,
  then hs_code must match "^\\d{8}$" and length 8.

User wording to stored values:
- "auto approved", "approved automatically", "straight through", "passed without action"
  means decision = 'AUTO_APPROVE'. Never use 'APPROVED' as a decision.
- "amendment", "needs amendment", "supplier amendment", "flagged for amendment"
  means decision = 'AMEND'.
- "human review", "manual review", "operator review" means decision = 'HUMAN_REVIEW'.
- "validation passed" means validations.overall_status = 'PASSED'.
- "validation failed" means validations.overall_status = 'FAILED'.
- "needs review" as validation status means validations.overall_status = 'NEEDS_REVIEW'.
- "matched field" means validations.field_results status 'MATCH'.
- "mismatched field" or "failing field" means validations.field_results status
  IN ('MISMATCH', 'MISSING') unless the user specifically asks for UNCERTAIN.
- "uncertain field" means validations.field_results status 'UNCERTAIN'.
- "rejected" is not a stored decision value. For rejection/failure reason questions,
  inspect validations.field_results with status IN ('MISMATCH', 'MISSING').

Query planning rules:
- Never use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, PRAGMA, ATTACH, DETACH, or multiple statements.
- Prefer explicit column names instead of SELECT *.
- Add LIMIT 100 for non-aggregate result sets.
- For document/run counts, decisions, dates, customers, and costs, prefer pipeline_runs.
- Use the normalized joins only when fields from documents, extractions, validations,
  or decisions are actually needed.
- For "this week", use the current UTC date context supplied by the user.
- For date filters on run/document questions, prefer pipeline_runs.completed_at.
- For amendment/flagged amendment questions, filter pipeline_runs.decision = 'AMEND'.
- For rejected/failing field reason questions, inspect validations.field_results JSON when needed."""

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
    def plan(self, question: str) -> SqlQueryPlan:
        ...


class QuerySummarizer(Protocol):
    def summarize(self, *, question: str, sql: str, rows: list[dict[str, Any]]) -> str:
        ...


class OpenAIQueryPlanner:
    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.non_vision_model
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def plan(self, question: str) -> SqlQueryPlan:
        now = datetime.now(UTC)
        dated_question = (
            f"Current UTC datetime: {now.isoformat()}\n"
            f"Current UTC date: {now.date().isoformat()}\n\n"
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
        plan = self.planner.plan(question)
        sql = validate_sql(plan.sql)
        with session_scope(self.session_factory) as session:
            rows = execute_sql(session, sql)

        evidence = QueryEvidence(sql=sql, rows=rows, row_count=len(rows))
        answer = self.summarizer.summarize(question=question, sql=sql, rows=rows)
        return QueryAnswer(answer=answer, evidence=evidence)


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
    quoted_literals = {
        match.group(1).strip().casefold()
        for match in re.finditer(r"'([^']*)'", sql)
    }
    for value, message in UNSUPPORTED_DOMAIN_VALUES.items():
        if value.casefold() in quoted_literals:
            raise QueryAgentError(
                f"Generated SQL used unsupported domain value {value}. {message}"
            )


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
