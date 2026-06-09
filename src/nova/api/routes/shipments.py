from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from nova.api.dependencies import get_session
from nova.api.errors import NOT_FOUND, ApiError
from nova.mail import (
    LocalMailAttachment,
    LocalMailDelivery,
    LocalMailSimulator,
    LocalReplySender,
    SimulatedMailRequest,
    SMTPReplySender,
)
from nova.schemas.shipment import Shipment, ShipmentStatus
from nova.settings import get_settings
from nova.storage import ShipmentFilters, ShipmentRepository
from nova.trigger import (
    ShipmentEventBus,
    shipment_event_stream,
)

router = APIRouter(tags=["shipments"])


class ConfirmDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_reply: str

    @field_validator("draft_reply")
    @classmethod
    def require_non_empty_draft(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("draft_reply must be non-empty")
        return value


@router.get("/shipments", response_model=list[Shipment])
def list_shipments(
    session: Annotated[Session, Depends(get_session)],
    customer_id: str | None = None,
    status: ShipmentStatus | None = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> list[Shipment]:
    return ShipmentRepository(session).list_shipments(
        ShipmentFilters(
            customer_id=customer_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )
    )


@router.get("/shipments/events")
def shipment_events(request: Request) -> StreamingResponse:
    event_bus = getattr(request.app.state, "shipment_event_bus", None)
    if event_bus is None:
        event_bus = ShipmentEventBus()
        request.app.state.shipment_event_bus = event_bus
    return StreamingResponse(
        shipment_event_stream(event_bus),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/shipments/{shipment_id}", response_model=Shipment)
def get_shipment(
    shipment_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> Shipment:
    try:
        return ShipmentRepository(session).get_shipment(shipment_id)
    except LookupError as exc:
        raise ApiError(status_code=404, error_code=NOT_FOUND, message=str(exc)) from exc


@router.delete("/shipments/{shipment_id}", status_code=204)
def delete_shipment(
    shipment_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    repository = ShipmentRepository(session)
    try:
        shipment = repository.get_shipment(shipment_id)
        repository.delete_shipment(shipment_id)
    except LookupError as exc:
        raise ApiError(status_code=404, error_code=NOT_FOUND, message=str(exc)) from exc
    publish_route_event(request=request, shipment=shipment, event_type="shipment_deleted")
    return Response(status_code=204)


@router.post("/shipments/{shipment_id}/confirm-draft", response_model=Shipment)
def confirm_draft(
    shipment_id: str,
    payload: ConfirmDraftRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Shipment:
    try:
        shipment = ShipmentRepository(session).get_shipment(shipment_id)
    except LookupError as exc:
        raise ApiError(status_code=404, error_code=NOT_FOUND, message=str(exc)) from exc

    if shipment.reply_message_id:
        return shipment

    agent_decision = shipment.overall_decision.decision if shipment.overall_decision else None
    if agent_decision == "AUTO_APPROVE":
        shipment.status = ShipmentStatus.APPROVED
    elif agent_decision == "AMEND":
        shipment.status = ShipmentStatus.AMENDED
    else:
        shipment.status = ShipmentStatus.REQUIRES_REVIEW
    shipment.draft_reply = payload.draft_reply
    shipment.completed_at = datetime.now(UTC)
    reply_delivery = send_confirmed_reply(
        shipment=shipment,
        draft_reply=payload.draft_reply,
    )
    shipment.reply_message_id = reply_delivery.message_id
    shipment.reply_mail_path = reply_delivery.mailbox_path
    ShipmentRepository(session).save_shipment(shipment)
    publish_route_event(request=request, shipment=shipment, event_type="draft_confirmed")
    return ShipmentRepository(session).get_shipment(shipment_id)


@router.post("/mail/simulate", response_model=LocalMailDelivery)
def simulate_mail(
    mail: SimulatedMailRequest,
    request: Request,
) -> LocalMailDelivery:
    watcher = getattr(request.app.state, "mail_watcher", None)
    incoming_dir = getattr(watcher, "incoming_dir", get_settings().mail_inbox_folder)
    return LocalMailSimulator(incoming_dir=incoming_dir).deliver(mail)


@router.post("/mail/simulate-upload", response_model=LocalMailDelivery)
async def simulate_uploaded_mail(
    request: Request,
    sender: Annotated[str, Form()],
    recipient: Annotated[str, Form()],
    subject: Annotated[str, Form()],
    customer_id: Annotated[str, Form()],
    body: Annotated[str, Form()],
    filenames: Annotated[list[str], Form()],
    files: Annotated[list[UploadFile], File()],
    email_id: Annotated[str | None, Form()] = None,
) -> LocalMailDelivery:
    if len(files) != len(filenames):
        raise ApiError(
            status_code=400,
            error_code="invalid_mail_upload",
            message="filenames must match uploaded files",
        )

    attachments = [
        LocalMailAttachment(filename=filename, data=await upload.read())
        for filename, upload in zip(filenames, files, strict=True)
    ]
    watcher = getattr(request.app.state, "mail_watcher", None)
    incoming_dir = getattr(watcher, "incoming_dir", get_settings().mail_inbox_folder)
    return LocalMailSimulator(incoming_dir=incoming_dir).deliver_bytes(
        email_id=email_id,
        sender=sender,
        recipient=recipient,
        subject=subject,
        customer_id=customer_id,
        body=body,
        attachments=attachments,
    )


def send_confirmed_reply(*, shipment: Shipment, draft_reply: str) -> LocalMailDelivery:
    settings = get_settings()
    mode = settings.reply_delivery_mode.strip().lower()
    if mode == "local":
        return LocalReplySender(
            sent_dir=settings.mail_sent_folder,
            sender=settings.smtp_from_email,
        ).send_reply(shipment=shipment, draft_reply=draft_reply)

    smtp_sender = SMTPReplySender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        sender=settings.smtp_from_email,
        starttls=settings.smtp_starttls,
        timeout_seconds=settings.smtp_timeout_seconds,
    )
    if mode == "smtp":
        return smtp_sender.send_reply(shipment=shipment, draft_reply=draft_reply)
    if mode == "local_and_smtp":
        LocalReplySender(
            sent_dir=settings.mail_sent_folder,
            sender=settings.smtp_from_email,
        ).send_reply(shipment=shipment, draft_reply=draft_reply)
        return smtp_sender.send_reply(shipment=shipment, draft_reply=draft_reply)
    raise ApiError(
        status_code=500,
        error_code="invalid_reply_delivery_mode",
        message="reply_delivery_mode must be local, smtp, or local_and_smtp",
    )


def publish_route_event(*, request: Request, shipment: Shipment, event_type: str) -> None:
    event_bus = getattr(request.app.state, "shipment_event_bus", None)
    if event_bus is None:
        return
    event_bus.publish(
        event_type,
        shipment_id=str(shipment.shipment_id),
        email_id=shipment.email_id,
        customer_id=shipment.customer_id,
        status=shipment.status.value,
        payload={},
    )
