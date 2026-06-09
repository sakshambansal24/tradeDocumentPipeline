from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from nova.settings import get_settings
from nova.storage.models import Base


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def init_db(database_url: str | None = None) -> sessionmaker[Session]:
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    ensure_sqlite_compatibility(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_sqlite_compatibility(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "pipeline_runs" not in table_names:
        pipeline_columns = set()
    else:
        pipeline_columns = {
            column["name"] for column in inspector.get_columns("pipeline_runs")
        }

    shipment_columns = (
        {column["name"] for column in inspector.get_columns("shipments")}
        if "shipments" in table_names
        else set()
    )

    with engine.begin() as connection:
        if "pipeline_runs" in table_names and "shipment_id" not in pipeline_columns:
            connection.execute(text("ALTER TABLE pipeline_runs ADD COLUMN shipment_id VARCHAR"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_pipeline_runs_shipment_id ON pipeline_runs (shipment_id)"
                )
            )
        if "pipeline_runs" in table_names and "source_filename" not in pipeline_columns:
            connection.execute(text("ALTER TABLE pipeline_runs ADD COLUMN source_filename VARCHAR"))
        if "pipeline_runs" in table_names and "extraction_id" not in pipeline_columns:
            connection.execute(text("ALTER TABLE pipeline_runs ADD COLUMN extraction_id VARCHAR"))
        if "pipeline_runs" in table_names and "validation_id" not in pipeline_columns:
            connection.execute(text("ALTER TABLE pipeline_runs ADD COLUMN validation_id VARCHAR"))
        if "shipments" in table_names and "triggered_by" not in shipment_columns:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN triggered_by VARCHAR"))
        if "shipments" in table_names and "subject" not in shipment_columns:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN subject VARCHAR"))
        if "shipments" in table_names and "recipient" not in shipment_columns:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN recipient VARCHAR"))
        if "shipments" in table_names and "original_message_id" not in shipment_columns:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN original_message_id VARCHAR"))
        if "shipments" in table_names and "email_references" not in shipment_columns:
            connection.execute(
                text("ALTER TABLE shipments ADD COLUMN email_references JSON DEFAULT '[]'")
            )
        if "shipments" in table_names and "reply_message_id" not in shipment_columns:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN reply_message_id VARCHAR"))
        if "shipments" in table_names and "reply_mail_path" not in shipment_columns:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN reply_mail_path VARCHAR"))
