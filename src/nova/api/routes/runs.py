from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from nova.api.dependencies import get_runner, get_session
from nova.api.errors import INVALID_INPUT, NOT_FOUND, ApiError
from nova.ingestion import DocumentLoader, UnreadableDocumentError
from nova.orchestration import PipelineRunner
from nova.schemas.decision import DecisionType
from nova.schemas.pipeline import PipelineRun
from nova.storage import PipelineRunRepository, RunFilters

router = APIRouter(prefix="/runs", tags=["runs"])

MAX_DOC_SIZE_BYTES = 15 * 1024 * 1024


@router.post("", response_model=PipelineRun)
async def create_run(
    customer_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    runner: Annotated[PipelineRunner, Depends(get_runner)],
) -> PipelineRun:
    content = await file.read()
    if len(content) > MAX_DOC_SIZE_BYTES:
        raise ApiError(
            status_code=400,
            error_code=INVALID_INPUT,
            message="Uploaded document exceeds max size",
        )
    try:
        loaded_document = DocumentLoader().load(
            content,
            content_type=file.content_type,
            source_filename=file.filename,
        )
    except UnreadableDocumentError as exc:
        raise ApiError(status_code=400, error_code=INVALID_INPUT, message=exc.reason) from exc

    # POC runs synchronously; prod would push to Temporal.
    return runner.run(loaded_document, customer_id)


@router.get("/{run_id}", response_model=PipelineRun)
def get_run(
    run_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> PipelineRun:
    try:
        return PipelineRunRepository(session).get(run_id)
    except LookupError as exc:
        raise ApiError(status_code=404, error_code=NOT_FOUND, message=str(exc)) from exc


@router.get("", response_model=list[PipelineRun])
def list_runs(
    session: Annotated[Session, Depends(get_session)],
    customer_id: str | None = None,
    decision: DecisionType | None = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> list[PipelineRun]:
    return PipelineRunRepository(session).list(
        RunFilters(
            customer_id=customer_id,
            decision=decision,
            date_from=date_from,
            date_to=date_to,
        )
    )
