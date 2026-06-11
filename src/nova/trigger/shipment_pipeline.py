from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from nova.agents import CrossValidatorAgent, ShipmentRouter
from nova.ingestion import DocumentLoader
from nova.observability import LoggingTracer, Tracer
from nova.orchestration import PipelineRunner
from nova.schemas.decision import DecisionType, RouterDecision
from nova.schemas.shipment import Shipment, ShipmentStatus
from nova.storage import ShipmentRepository, session_scope
from nova.trigger.events import ShipmentEventBus
from nova.trigger.schemas import IncomingEmail


class ShipmentPipeline:
    def __init__(
        self,
        *,
        runner: PipelineRunner,
        storage_session_factory: sessionmaker[Session],
        document_loader: DocumentLoader | None = None,
        shipment_router: ShipmentRouter | None = None,
        tracer: Tracer | None = None,
        event_bus: ShipmentEventBus | None = None,
    ) -> None:
        self.runner = runner
        self.storage_session_factory = storage_session_factory
        self.document_loader = document_loader or DocumentLoader()
        self.shipment_router = shipment_router or ShipmentRouter()
        self.tracer = tracer or LoggingTracer()
        self.event_bus = event_bus

    def process(self, email: IncomingEmail) -> Shipment:
        shipment = self._shipment_for_email(email)
        existing_filenames = {
            run.source_filename
            for run in shipment.document_runs
            if run.source_filename is not None
        }
        attachments_to_process = [
            attachment
            for attachment in email.attachments
            if attachment.filename not in existing_filenames
        ]
        self._save(shipment)
        self._publish_event(
            "shipment_started",
            shipment,
            {
                "attachment_count": len(attachments_to_process),
                "skipped_duplicate_count": len(email.attachments) - len(attachments_to_process),
                "is_thread_update": len(existing_filenames) > 0,
            },
        )
        self.tracer.emit(
            "shipment",
            "shipment_started",
            {
                "shipment_id": str(shipment.shipment_id),
                "email_id": email.email_id,
                "attachment_count": len(attachments_to_process),
            },
        )

        try:
            for attachment in attachments_to_process:
                loaded_document = self.document_loader.load(
                    Path(attachment.path),
                    source_filename=attachment.filename,
                )
                run = self.runner.run(loaded_document, email.customer_id)
                shipment.document_runs.append(run)
                self._save(shipment)
                self._publish_event(
                    "doc_processed",
                    shipment,
                    {
                        "filename": attachment.filename,
                        "run_id": str(run.run_id),
                        "document_status": run.status.value,
                    },
                )
                self.tracer.emit(
                    "shipment",
                    "doc_processed",
                    {
                        "shipment_id": str(shipment.shipment_id),
                        "email_id": email.email_id,
                        "filename": attachment.filename,
                        "run_id": str(run.run_id),
                        "status": run.status.value,
                    },
                )

            shipment.cross_validation_result = CrossValidatorAgent(
                shipment_id=shipment.shipment_id
            ).validate(shipment.document_runs)
            self._publish_event(
                "cross_validation_done",
                shipment,
                {"overall_consistent": shipment.cross_validation_result.overall_consistent},
            )
            self.tracer.emit(
                "shipment",
                "cross_validation_done",
                {
                    "shipment_id": str(shipment.shipment_id),
                    "overall_consistent": shipment.cross_validation_result.overall_consistent,
                },
            )
            shipment.overall_decision, draft_reply = self.shipment_router.decide(shipment)
            shipment.draft_reply = draft_reply or None
            shipment.status = self._status_from_decision(shipment.overall_decision)
            shipment.completed_at = datetime.now(UTC)
            self._save(shipment)
            self._publish_event(
                "shipment_completed",
                shipment,
                {"decision": shipment.overall_decision.decision.value},
            )
            self.tracer.emit(
                "shipment",
                "shipment_completed",
                {
                    "shipment_id": str(shipment.shipment_id),
                    "email_id": email.email_id,
                    "status": shipment.status.value,
                    "decision": shipment.overall_decision.decision.value,
                },
            )
            return shipment
        except Exception as exc:
            shipment.status = ShipmentStatus.REQUIRES_REVIEW
            shipment.completed_at = datetime.now(UTC)
            shipment.draft_reply = (
                f"Shipment processing failed before verification completed: {exc}"
            )
            self._save(shipment)
            self._publish_event(
                "shipment_failed",
                shipment,
                {"error_message": str(exc)},
            )
            self.tracer.emit(
                "shipment",
                "shipment_failed",
                {
                    "shipment_id": str(shipment.shipment_id),
                    "email_id": email.email_id,
                    "error_message": str(exc),
                },
            )
            raise

    def _shipment_for_email(self, email: IncomingEmail) -> Shipment:
        existing_shipment = self._find_thread_shipment(email)
        if existing_shipment is not None:
            existing_shipment.email_id = email.email_id
            existing_shipment.triggered_by = email.sender or existing_shipment.triggered_by
            existing_shipment.recipient = email.recipient or existing_shipment.recipient
            existing_shipment.subject = email.subject or existing_shipment.subject
            existing_shipment.references = merge_references(
                existing_shipment.references,
                [existing_shipment.original_message_id, *email.references, email.message_id],
            )
            existing_shipment.status = ShipmentStatus.PROCESSING
            existing_shipment.completed_at = None
            return existing_shipment

        return Shipment(
            shipment_id=uuid4(),
            email_id=email.email_id,
            customer_id=email.customer_id,
            triggered_by=email.sender,
            recipient=email.recipient,
            subject=email.subject,
            original_message_id=email.message_id,
            references=merge_references(email.references, [email.message_id]),
            triggered_at=email.received_at,
            status=ShipmentStatus.PROCESSING,
            document_runs=[],
        )

    def _find_thread_shipment(self, email: IncomingEmail) -> Shipment | None:
        message_ids = {
            value
            for value in [email.message_id, *email.references]
            if value is not None and value.strip()
        }
        with session_scope(self.storage_session_factory) as session:
            return ShipmentRepository(session).find_by_thread_references(
                customer_id=email.customer_id,
                message_ids=message_ids,
            )

    def _status_from_decision(self, decision: RouterDecision) -> ShipmentStatus:
        match decision.decision:
            case DecisionType.AUTO_APPROVE:
                return ShipmentStatus.APPROVED
            case DecisionType.AMEND:
                return ShipmentStatus.AMENDED
            case DecisionType.HUMAN_REVIEW:
                return ShipmentStatus.REQUIRES_REVIEW

    def _save(self, shipment: Shipment) -> None:
        with session_scope(self.storage_session_factory) as session:
            ShipmentRepository(session).save_shipment(shipment)

    def _publish_event(
        self,
        event_type: str,
        shipment: Shipment,
        payload: dict,
    ) -> None:
        if self.event_bus is None:
            return
        self.event_bus.publish(
            event_type,
            shipment_id=str(shipment.shipment_id),
            email_id=shipment.email_id,
            customer_id=shipment.customer_id,
            status=shipment.status.value,
            payload=payload,
        )


def merge_references(existing: list[str], new_values: list[str | None]) -> list[str]:
    merged = list(existing)
    seen = set(merged)
    for value in new_values:
        if value is None:
            continue
        stripped = value.strip()
        if not stripped or stripped in seen:
            continue
        merged.append(stripped)
        seen.add(stripped)
    return merged
