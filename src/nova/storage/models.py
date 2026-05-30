from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_id", "id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)

    extractions: Mapped[list["Extraction"]] = relationship(back_populates="document")


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (Index("ix_extractions_document_id", "document_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    raw_response_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    document: Mapped[Document] = relationship(back_populates="extractions")
    validations: Mapped[list["Validation"]] = relationship(back_populates="extraction")


class Validation(Base):
    __tablename__ = "validations"
    __table_args__ = (Index("ix_validations_extraction_id", "extraction_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    extraction_id: Mapped[str] = mapped_column(ForeignKey("extractions.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(String, nullable=False)
    rule_set_version: Mapped[str] = mapped_column(String, nullable=False)
    overall_status: Mapped[str] = mapped_column(String, nullable=False)
    validator_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    field_results: Mapped[list[dict]] = mapped_column(JSON, nullable=False)

    extraction: Mapped[Extraction] = relationship(back_populates="validations")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="validation")


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_validation_id", "validation_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    validation_id: Mapped[str] = mapped_column(ForeignKey("validations.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    drafted_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    validation: Mapped[Validation] = relationship(back_populates="decisions")


class PipelineRunRecord(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_customer_decision", "customer_id", "decision"),
        Index("ix_pipeline_runs_status_completed_at", "status", "completed_at"),
        Index("ix_pipeline_runs_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_total_usd: Mapped[float] = mapped_column(Float, nullable=False)
    stage_history: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    trace_id: Mapped[str] = mapped_column(String, nullable=False)

    document: Mapped[Document] = relationship()


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    rule_set_path: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
