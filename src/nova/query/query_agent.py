from datetime import UTC, datetime
from typing import Any, Protocol

from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session, sessionmaker

from nova.schemas.query import (
    CountFieldValidationsArgs,
    CountRunsArgs,
    GetRunDetailArgs,
    ListRunsArgs,
    QueryAnswer,
    QueryEvidence,
    QueryEvidenceItem,
    QueryPlan,
    QueryToolCall,
    TopFailingFieldsArgs,
)
from nova.settings import get_settings
from nova.storage import session_scope

from .tools import QueryTools

SYSTEM_PROMPT = """You answer questions over persisted Nova document pipeline runs.
You may only use the curated tools listed below. Do not generate SQL.
Never guess counts, customers, dates, run IDs, or field names.
If the tools cannot answer the question, return no tool calls.

Tools:
- count_runs(filters: {customer_id?, decision?, date_from?, date_to?})
- list_runs(filters, limit=10)
- get_run_detail(run_id)
- count_field_validations(filters: {customer_id?, field_name?, status?, date_from?, date_to?})
- top_failing_fields(date_from, date_to, limit=5, customer_id?)

For "this week", include an explicit date_from and date_to.
For amendment/flagged amendment questions, use decision=AMEND.
For rejected/failing field reasons, use top_failing_fields."""


class QueryPlanner(Protocol):
    def plan(self, question: str) -> QueryPlan:
        ...


class OpenAIQueryPlanner:
    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.non_vision_model
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def plan(self, question: str) -> QueryPlan:
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
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": dated_question},
                ],
                text_format=QueryPlan,
            )
        except OpenAIError as exc:
            raise QueryAgentError(f"Query planning failed: {exc}") from exc

        if response.output_parsed is None:
            raise QueryAgentError("Query planning returned no parsed output")
        return response.output_parsed


class QueryAgentError(Exception):
    pass


class QueryAgent:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        planner: QueryPlanner | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.planner = planner or OpenAIQueryPlanner()

    def ask(self, question: str) -> QueryAnswer:
        plan = self.planner.plan(question)
        if not plan.tool_calls:
            return QueryAnswer(
                answer="I could not answer that from the available grounded query tools.",
                evidence=QueryEvidence(tool_calls=[]),
            )

        evidence_items: list[QueryEvidenceItem] = []
        with session_scope(self.session_factory) as session:
            tools = QueryTools(session)
            for tool_call in plan.tool_calls:
                result = execute_tool_call(tools, tool_call)
                evidence_items.append(
                    QueryEvidenceItem(
                        tool_name=tool_call.name,
                        args=serialize_tool_args(tool_call.args),
                        result=serialize_tool_result(result),
                    )
                )

        evidence = QueryEvidence(tool_calls=evidence_items)
        return QueryAnswer(answer=compose_grounded_answer(question, evidence), evidence=evidence)


def execute_tool_call(tools: QueryTools, tool_call: QueryToolCall) -> Any:
    match tool_call.name:
        case "count_runs":
            return tools.count_runs(CountRunsArgs.model_validate(tool_call.args))
        case "list_runs":
            return tools.list_runs(ListRunsArgs.model_validate(tool_call.args))
        case "get_run_detail":
            return tools.get_run_detail(GetRunDetailArgs.model_validate(tool_call.args))
        case "count_field_validations":
            return tools.count_field_validations(
                CountFieldValidationsArgs.model_validate(tool_call.args)
            )
        case "top_failing_fields":
            return tools.top_failing_fields(TopFailingFieldsArgs.model_validate(tool_call.args))


def serialize_tool_args(args: Any) -> dict[str, Any]:
    if hasattr(args, "model_dump"):
        return args.model_dump()
    return args


def serialize_tool_result(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, list):
        return [serialize_tool_result(item) for item in result]
    return result


def compose_grounded_answer(question: str, evidence: QueryEvidence) -> str:
    first_call = evidence.tool_calls[0]
    if first_call.tool_name == "count_runs":
        return f"The grounded count is {first_call.result}, based on count_runs."
    if first_call.tool_name == "top_failing_fields":
        if not first_call.result:
            return "No failing fields were found in the grounded query result."
        top = first_call.result[0]
        return (
            f"The top failing field is {top['field_name']} with "
            f"{top['mismatch_count']} mismatches, based on top_failing_fields."
        )
    if first_call.tool_name == "list_runs":
        return f"Found {len(first_call.result)} runs, based on list_runs."
    if first_call.tool_name == "get_run_detail":
        return f"Found details for run {first_call.result['summary']['run_id']}."
    if first_call.tool_name == "count_field_validations":
        return f"The grounded validation count is {first_call.result}."
    return f"I could not compose an answer for: {question}"
