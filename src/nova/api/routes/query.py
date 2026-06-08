from typing import Annotated

from fastapi import APIRouter, Depends

from nova.api.dependencies import get_query_agent
from nova.api.errors import INVALID_INPUT, ApiError
from nova.query import QueryAgent, QueryAgentError
from nova.schemas.api import QueryRequest
from nova.schemas.query import QueryAnswer

router = APIRouter(prefix="/query", tags=["query"])


@router.post("", response_model=QueryAnswer)
def ask_query(
    request: QueryRequest,
    query_agent: Annotated[QueryAgent, Depends(get_query_agent)],
) -> QueryAnswer:
    try:
        return query_agent.ask(request.question)
    except QueryAgentError as exc:
        raise ApiError(
            status_code=400,
            error_code=INVALID_INPUT,
            message=str(exc),
        ) from exc
