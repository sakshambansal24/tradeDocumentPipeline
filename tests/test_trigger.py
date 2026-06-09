from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from nova.api.app import create_app
from nova.mail import LocalMailWatcher, LocalReplySender, SMTPReplySender
from nova.schemas.shipment import Shipment, ShipmentStatus
from nova.storage import ShipmentRepository, init_db, session_scope
from nova.trigger import ShipmentEventBus


class RecordingShipmentPipeline:
    def __init__(self) -> None:
        self.processed_email_ids: list[str] = []

    def process(self, email) -> None:
        self.processed_email_ids.append(email.email_id)


def test_post_mail_simulate_drops_email_for_mail_watcher(tmp_path) -> None:
    app = create_app()
    init_db(f"sqlite:///{tmp_path / 'trigger-api.db'}")
    incoming_dir = tmp_path / "mail" / "incoming"
    attachments_dir = tmp_path / "attachments"
    processed_dir = tmp_path / "mail" / "processed"
    failed_dir = tmp_path / "mail" / "failed"
    attachment_path = tmp_path / "invoice.png"
    attachment_path.write_bytes(_sample_png_bytes())
    pipeline = RecordingShipmentPipeline()

    with TestClient(app) as client:
        app.state.mail_watcher.stop()
        app.state.mail_watcher.incoming_dir = incoming_dir
        response = client.post(
            "/mail/simulate",
            json=_mail_payload(attachment_path, email_id="email-api-001"),
        )
        LocalMailWatcher(
            incoming_dir=incoming_dir,
            attachments_dir=attachments_dir,
            processed_dir=processed_dir,
            failed_dir=failed_dir,
            poll_seconds=0.1,
            shipment_pipeline=pipeline,
        ).poll_once()

    assert response.status_code == 200
    body = response.json()
    assert body["email_id"] == "email-api-001"
    assert body["status"] == "QUEUED"
    assert pipeline.processed_email_ids == ["email-api-001"]
    assert (processed_dir / "email-api-001.eml").exists()


def test_post_mail_simulate_upload_drops_uploaded_email_for_watcher(tmp_path) -> None:
    app = create_app()
    init_db(f"sqlite:///{tmp_path / 'trigger-upload-api.db'}")
    incoming_dir = tmp_path / "mail" / "incoming"
    attachments_dir = tmp_path / "attachments"
    processed_dir = tmp_path / "mail" / "processed"
    failed_dir = tmp_path / "mail" / "failed"
    pipeline = RecordingShipmentPipeline()

    with TestClient(app) as client:
        app.state.mail_watcher.stop()
        app.state.mail_watcher.incoming_dir = incoming_dir
        response = client.post(
            "/mail/simulate-upload",
            data={
                "email_id": "email-upload-001",
                "sender": "supplier@example.com",
                "recipient": "cg@gocomet.local",
                "subject": "Uploaded shipment docs",
                "customer_id": "acme_corp",
                "body": "Please review uploaded documents.",
                "filenames": ["uploaded_invoice.png"],
            },
            files={
                "files": ("source-name.png", _sample_png_bytes(), "image/png"),
            },
        )
        LocalMailWatcher(
            incoming_dir=incoming_dir,
            attachments_dir=attachments_dir,
            processed_dir=processed_dir,
            failed_dir=failed_dir,
            poll_seconds=0.1,
            shipment_pipeline=pipeline,
        ).poll_once()

    assert response.status_code == 200
    assert response.json()["email_id"] == "email-upload-001"
    assert pipeline.processed_email_ids == ["email-upload-001"]
    assert (attachments_dir / "email-upload-001" / "uploaded_invoice.png").exists()


def test_shipment_event_bus_publishes_to_subscribers() -> None:
    event_bus = ShipmentEventBus()
    subscriber = event_bus.subscribe()

    event_bus.publish(
        "shipment_started",
        shipment_id="shipment-123",
        email_id="email-123",
        customer_id="acme_corp",
        status="PROCESSING",
        payload={"attachment_count": 2},
    )

    event = subscriber.get_nowait()
    assert event.event_type == "shipment_started"
    assert event.shipment_id == "shipment-123"
    assert event.payload == {"attachment_count": 2}


def test_local_reply_sender_preserves_email_thread_headers(tmp_path) -> None:
    shipment = Shipment(
        shipment_id=uuid4(),
        email_id="email-thread-001",
        customer_id="acme_corp",
        triggered_by="supplier@example.com",
        recipient="cg@gocomet.local",
        subject="Shipment docs - ACME Corp",
        original_message_id="<original-message@nova.local>",
        references=["<previous-message@nova.local>"],
        triggered_at=datetime.now(UTC),
        status=ShipmentStatus.REQUIRES_REVIEW,
        document_runs=[],
    )

    delivery = LocalReplySender(sent_dir=tmp_path / "sent").send_reply(
        shipment=shipment,
        draft_reply="Subject: Re: Shipment docs - ACME Corp\n\nPlease correct and resubmit.",
    )

    message = BytesParser(policy=policy.default).parsebytes(
        Path(delivery.mailbox_path).read_bytes()
    )
    assert message["To"] == "supplier@example.com"
    assert message["In-Reply-To"] == "<original-message@nova.local>"
    assert "<original-message@nova.local>" in message["References"]
    assert "<previous-message@nova.local>" in message["References"]


def test_smtp_reply_sender_delivers_threaded_email(monkeypatch) -> None:
    sent_messages: list[EmailMessage] = []
    started_tls: list[bool] = []
    logins: list[tuple[str, str]] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def starttls(self) -> None:
            started_tls.append(True)

        def login(self, username: str, password: str) -> None:
            logins.append((username, password))

        def send_message(self, message: EmailMessage) -> None:
            sent_messages.append(message)

    monkeypatch.setattr("nova.mail.local_mailbox.smtplib.SMTP", FakeSMTP)
    shipment = Shipment(
        shipment_id=uuid4(),
        email_id="email-smtp-001",
        customer_id="acme_corp",
        triggered_by="recipient@gmail.com",
        recipient="cg@gocomet.local",
        subject="Shipment docs - ACME Corp",
        original_message_id="<original-message@nova.local>",
        references=[],
        triggered_at=datetime.now(UTC),
        status=ShipmentStatus.REQUIRES_REVIEW,
        document_runs=[],
    )

    delivery = SMTPReplySender(
        host="smtp.gmail.com",
        port=587,
        username="sender@gmail.com",
        password="app-password",
        sender="sender@gmail.com",
    ).send_reply(
        shipment=shipment,
        draft_reply="Subject: Re: Shipment docs - ACME Corp\n\nPlease correct and resubmit.",
    )

    assert delivery.status == "SENT_VIA_SMTP"
    assert started_tls == [True]
    assert logins == [("sender@gmail.com", "app-password")]
    assert sent_messages[0]["From"] == "sender@gmail.com"
    assert sent_messages[0]["To"] == "recipient@gmail.com"
    assert sent_messages[0]["In-Reply-To"] == "<original-message@nova.local>"


def test_delete_shipment_removes_unwanted_inbox_record(tmp_path) -> None:
    app = create_app()
    session_factory = init_db(f"sqlite:///{tmp_path / 'trigger-delete.db'}")
    shipment_id = uuid4()
    with session_scope(session_factory) as session:
        ShipmentRepository(session).save_shipment(
            Shipment(
                shipment_id=shipment_id,
                email_id="delete-email-001",
                customer_id="acme_corp",
                triggered_at=datetime.now(UTC),
                status=ShipmentStatus.REQUIRES_REVIEW,
                document_runs=[],
            )
        )

    with TestClient(app) as client:
        app.state.session_factory = session_factory
        response = client.delete(f"/shipments/{shipment_id}")
        get_response = client.get(f"/shipments/{shipment_id}")

    assert response.status_code == 204
    assert get_response.status_code == 404


def _mail_payload(attachment_path: Path, *, email_id: str) -> dict:
    return {
        "email_id": email_id,
        "sender": "supplier@example.com",
        "recipient": "cg@gocomet.local",
        "subject": "Shipment docs - ACME Corp - INV-2024-00123",
        "customer_id": "acme_corp",
        "attachments": [
            {
                "filename": attachment_path.name,
                "path": str(attachment_path),
            }
        ],
    }


def _sample_png_bytes() -> bytes:
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 100), "Commercial Invoice", fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
