import shutil
import smtplib
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import make_msgid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from nova.observability import get_logger
from nova.trigger.schemas import EmailAttachment, IncomingEmail
from nova.trigger.shipment_pipeline import ShipmentPipeline

logger = get_logger(__name__)


class SimulatedMailAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    path: Path

    @field_validator("filename")
    @classmethod
    def require_non_empty_filename(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("filename must be non-empty")
        return value


class SimulatedMailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender: str = "supplier@example.com"
    recipient: str = "cg@gocomet.local"
    subject: str
    customer_id: str
    attachments: list[SimulatedMailAttachment]
    body: str = "Please find attached the shipment documents for review."
    email_id: str | None = None
    in_reply_to: str | None = None

    @field_validator("sender", "recipient", "subject", "customer_id", "body", "in_reply_to")
    @classmethod
    def require_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be non-empty")
        return stripped

    @field_validator("attachments")
    @classmethod
    def require_attachments(
        cls,
        value: list[SimulatedMailAttachment],
    ) -> list[SimulatedMailAttachment]:
        if not value:
            raise ValueError("attachments must include at least one document")
        return value


class LocalMailDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_id: str
    message_id: str
    mailbox_path: str
    status: str
    message: str


@dataclass(frozen=True)
class LocalMailAttachment:
    filename: str
    data: bytes


class LocalMailSimulator:
    def __init__(self, *, incoming_dir: str | Path) -> None:
        self.incoming_dir = Path(incoming_dir)

    def deliver(self, request: SimulatedMailRequest) -> LocalMailDelivery:
        attachments = [
            LocalMailAttachment(filename=attachment.filename, data=attachment.path.read_bytes())
            for attachment in request.attachments
        ]
        return self.deliver_bytes(
            email_id=request.email_id,
            sender=request.sender,
            recipient=request.recipient,
            subject=request.subject,
            customer_id=request.customer_id,
            body=request.body,
            in_reply_to=request.in_reply_to,
            attachments=attachments,
        )

    def deliver_bytes(
        self,
        *,
        email_id: str | None,
        sender: str,
        recipient: str,
        subject: str,
        customer_id: str,
        body: str,
        attachments: Sequence[LocalMailAttachment],
        in_reply_to: str | None = None,
    ) -> LocalMailDelivery:
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        email_id = email_id or f"mail-{datetime.now(UTC).timestamp():.6f}".replace(".", "-")
        message_id = make_msgid(idstring=email_id, domain="nova.local")

        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = message_id
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message["X-Nova-Email-ID"] = email_id
        message["X-Nova-Customer-ID"] = customer_id
        message.set_content(body)

        for attachment in attachments:
            message.add_attachment(
                attachment.data,
                maintype="application",
                subtype="octet-stream",
                filename=attachment.filename,
            )

        destination = self._unique_destination(self.incoming_dir / f"{email_id}.eml")
        temp_destination = destination.with_suffix(f"{destination.suffix}.tmp")
        temp_destination.write_bytes(message.as_bytes(policy=policy.default))
        temp_destination.replace(destination)
        return LocalMailDelivery(
            email_id=email_id,
            message_id=message_id,
            mailbox_path=str(destination),
            status="QUEUED",
            message="Simulated mail delivered to local inbox; listener will process it.",
        )

    def _unique_destination(self, destination: Path) -> Path:
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        for index in range(1, 10_000):
            candidate = destination.with_name(f"{stem}-{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not create unique mail path for {destination}")


class LocalMailWatcher:
    def __init__(
        self,
        *,
        incoming_dir: str | Path,
        attachments_dir: str | Path,
        processed_dir: str | Path,
        failed_dir: str | Path,
        poll_seconds: float,
        shipment_pipeline: ShipmentPipeline,
    ) -> None:
        self.incoming_dir = Path(incoming_dir)
        self.attachments_dir = Path(attachments_dir)
        self.processed_dir = Path(processed_dir)
        self.failed_dir = Path(failed_dir)
        self.poll_seconds = poll_seconds
        self.shipment_pipeline = shipment_pipeline
        self._seen: set[Path] = set()
        self._inflight: set[Path] = set()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ensure_directories()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="nova-local-mail-watcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def poll_once(self) -> None:
        self._ensure_directories()
        for mail_path in sorted(self.incoming_dir.glob("*.eml")):
            if not self._claim(mail_path):
                continue
            try:
                self._process_file(mail_path)
                self._seen.add(mail_path.resolve())
            finally:
                with self._lock:
                    self._inflight.discard(mail_path.resolve())

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception(
                    "local_mail_watcher.poll_failed",
                    incoming_dir=str(self.incoming_dir),
                )
            self._stop_event.wait(self.poll_seconds)

    def _process_file(self, mail_path: Path) -> None:
        try:
            incoming_email = parse_mail_to_incoming_email(
                mail_path,
                attachments_dir=self.attachments_dir,
            )
            self.shipment_pipeline.process(incoming_email)
        except Exception as exc:
            logger.exception("local_mail_watcher.email_failed", path=str(mail_path))
            self._move_failed(mail_path, exc)
            return
        self._move_processed(mail_path)

    def _claim(self, mail_path: Path) -> bool:
        resolved = mail_path.resolve()
        with self._lock:
            if resolved in self._seen or resolved in self._inflight:
                return False
            self._inflight.add(resolved)
            return True

    def _move_processed(self, mail_path: Path) -> None:
        shutil.move(str(mail_path), self._unique_destination(self.processed_dir / mail_path.name))

    def _move_failed(self, mail_path: Path, error: Exception) -> None:
        destination = self._unique_destination(self.failed_dir / mail_path.name)
        if mail_path.exists():
            shutil.move(str(mail_path), destination)
        sidecar = destination.with_suffix(f"{destination.suffix}.error.txt")
        sidecar.write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")

    def _unique_destination(self, destination: Path) -> Path:
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        for index in range(1, 10_000):
            candidate = destination.with_name(f"{stem}-{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not create unique destination for {destination}")

    def _ensure_directories(self) -> None:
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)


class LocalReplySender:
    def __init__(self, *, sent_dir: str | Path, sender: str = "cg@gocomet.local") -> None:
        self.sent_dir = Path(sent_dir)
        self.sender = sender

    def send_reply(self, *, shipment, draft_reply: str) -> LocalMailDelivery:
        self.sent_dir.mkdir(parents=True, exist_ok=True)
        message, message_id = build_reply_message(
            shipment=shipment,
            draft_reply=draft_reply,
            sender=self.sender,
        )

        destination = self._unique_destination(self.sent_dir / f"{shipment.email_id}-reply.eml")
        destination.write_bytes(message.as_bytes(policy=policy.default))
        return LocalMailDelivery(
            email_id=shipment.email_id,
            message_id=message_id,
            mailbox_path=str(destination),
            status="SENT_TO_LOCAL_THREAD",
            message="CG reply written to local sent mailbox with same-thread headers.",
        )

    def _unique_destination(self, destination: Path) -> Path:
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        for index in range(1, 10_000):
            candidate = destination.with_name(f"{stem}-{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not create unique sent mail path for {destination}")


class SMTPReplySender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        sender: str,
        starttls: bool = True,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.starttls = starttls
        self.timeout_seconds = timeout_seconds

    def send_reply(self, *, shipment, draft_reply: str) -> LocalMailDelivery:
        if not self.host:
            raise RuntimeError("SMTP host is not configured")

        message, message_id = build_reply_message(
            shipment=shipment,
            draft_reply=draft_reply,
            sender=self.sender,
        )
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)

        return LocalMailDelivery(
            email_id=shipment.email_id,
            message_id=message_id,
            mailbox_path=f"smtp://{self.host}:{self.port}",
            status="SENT_VIA_SMTP",
            message="CG reply delivered through configured SMTP server.",
        )


def build_reply_message(*, shipment, draft_reply: str, sender: str) -> tuple[EmailMessage, str]:
    subject, body = split_subject_and_body(draft_reply)
    message_id = make_msgid(idstring=str(shipment.shipment_id), domain="nova.local")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = shipment.triggered_by or "supplier@example.com"
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if shipment.original_message_id:
        message["In-Reply-To"] = shipment.original_message_id
        references = list(shipment.references or [])
        if shipment.original_message_id not in references:
            references.append(shipment.original_message_id)
        message["References"] = " ".join(references)
    message.set_content(body)
    return message, message_id


def parse_mail_to_incoming_email(mail_path: Path, *, attachments_dir: Path) -> IncomingEmail:
    message = BytesParser(policy=policy.default).parsebytes(mail_path.read_bytes())
    email_id = message.get("X-Nova-Email-ID") or mail_path.stem
    customer_id = message.get("X-Nova-Customer-ID") or "acme_corp"
    message_attachment_dir = attachments_dir / email_id
    message_attachment_dir.mkdir(parents=True, exist_ok=True)

    attachments: list[EmailAttachment] = []
    for attachment in message.iter_attachments():
        filename = attachment.get_filename()
        if not filename:
            continue
        destination = message_attachment_dir / filename
        destination.write_bytes(attachment.get_payload(decode=True) or b"")
        attachments.append(EmailAttachment(filename=filename, path=destination))

    references = parse_references(message.get("References"))
    in_reply_to = message.get("In-Reply-To")
    if in_reply_to and in_reply_to not in references:
        references.append(in_reply_to)

    return IncomingEmail(
        email_id=email_id,
        sender=message.get("From", ""),
        recipient=message.get("To"),
        subject=message.get("Subject", ""),
        customer_id=customer_id,
        attachments=attachments,
        message_id=message.get("Message-ID"),
        references=references,
    )


def split_subject_and_body(draft_reply: str) -> tuple[str, str]:
    lines = draft_reply.splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        return lines[0].split(":", maxsplit=1)[1].strip(), "\n".join(lines[1:]).lstrip()
    return "Re: Shipment documents", draft_reply


def parse_references(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in value.split() if part.strip()]
