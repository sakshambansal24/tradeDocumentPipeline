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
        shipment = Shipment(
            shipment_id=uuid4(),
            email_id=email.email_id,
            customer_id=email.customer_id,
            triggered_by=email.sender,
            recipient=email.recipient,
            subject=email.subject,
            original_message_id=email.message_id,
            references=email.references,
            triggered_at=email.received_at,
            status=ShipmentStatus.PROCESSING,
            document_runs=[],
        )
        self._save(shipment)
        self._publish_event(
            "shipment_started",
            shipment,
            {"attachment_count": len(email.attachments)},
        )
        self.tracer.emit(
            "shipment",
            "shipment_started",
            {
                "shipment_id": str(shipment.shipment_id),
                "email_id": email.email_id,
                "attachment_count": len(email.attachments),
            },
        )

        try:
            for attachment in email.attachments:
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
