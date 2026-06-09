from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nova.api.errors import install_exception_handlers
from nova.api.routes.customers import router as customers_router
from nova.api.routes.query import router as query_router
from nova.api.routes.runs import router as runs_router
from nova.api.routes.shipments import router as shipments_router
from nova.mail import LocalMailWatcher
from nova.observability import configure_logging
from nova.orchestration import PipelineRunner
from nova.query import QueryAgent
from nova.settings import get_settings
from nova.storage import init_db
from nova.trigger import ShipmentEventBus, ShipmentPipeline


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    session_factory = init_db()
    app.state.session_factory = session_factory
    app.state.runner = PipelineRunner(storage_session_factory=session_factory)
    app.state.query_agent = QueryAgent(session_factory=session_factory)
    app.state.shipment_event_bus = ShipmentEventBus()
    app.state.shipment_pipeline = ShipmentPipeline(
        runner=app.state.runner,
        storage_session_factory=session_factory,
        event_bus=app.state.shipment_event_bus,
    )
    app.state.mail_watcher = LocalMailWatcher(
        incoming_dir=settings.mail_inbox_folder,
        attachments_dir=settings.mail_attachments_folder,
        processed_dir=settings.mail_processed_folder,
        failed_dir=settings.mail_failed_folder,
        poll_seconds=settings.mail_poll_seconds,
        shipment_pipeline=app.state.shipment_pipeline,
    )
    app.state.mail_watcher.start()
    try:
        yield
    finally:
        app.state.mail_watcher.stop()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Nova Document Pipeline", lifespan=lifespan)
    # Prod auth belongs here: tenant-aware auth middleware before route handlers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_exception_handlers(app)
    app.include_router(runs_router)
    app.include_router(query_router)
    app.include_router(customers_router)
    app.include_router(shipments_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
