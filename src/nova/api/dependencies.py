from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from nova.orchestration import PipelineRunner
from nova.query import QueryAgent


def get_session(request: Request) -> Iterator[Session]:
    session_factory = request.app.state.session_factory
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_runner(request: Request) -> PipelineRunner:
    return request.app.state.runner


def get_query_agent(request: Request) -> QueryAgent:
    return request.app.state.query_agent
