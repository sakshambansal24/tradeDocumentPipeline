from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from nova.api.app import create_app
from nova.api.dependencies import get_query_agent, get_runner
from nova.query import QueryAgent
from nova.schemas.pipeline import PipelineRun, PipelineRunStatus, StageEvent, StageName, StageStatus
from nova.schemas.query import QueryPlan, QueryToolCall
from nova.storage import PipelineRunRepository, init_db, session_scope


class FakeRunner:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def run(self, loaded_document, customer_id: str) -> PipelineRun:
        now = datetime.now(UTC)
        run_id = uuid4()
        run = PipelineRun(
            run_id=run_id,
            document_id=loaded_document.doc_id,
            customer_id=customer_id,
            status=PipelineRunStatus.COMPLETED,
            stages=[
                StageEvent(
                    stage=StageName.STORAGE,
                    status=StageStatus.COMPLETED,
                    started_at=now,
                    completed_at=now,
                    latency_ms=1,
                    cost_usd=0.0,
                    trace_id=str(run_id),
                )
            ],
            started_at=now,
            completed_at=now,
            cost_total_usd=0.0,
            trace_id=str(run_id),
        )
        with session_scope(self.session_factory) as session:
            PipelineRunRepository(session).save_run(run)
        return run


class StaticPlanner:
    def __init__(self, plan: QueryPlan) -> None:
        self._plan = plan

    def plan(self, question: str) -> QueryPlan:
        return self._plan


def test_post_runs_upload_returns_run_and_get_is_queryable(tmp_path) -> None:
    app = create_app()
    session_factory = init_db(f"sqlite:///{tmp_path / 'api-runs.db'}")
    app.dependency_overrides[get_runner] = lambda: FakeRunner(session_factory)

    with TestClient(app) as client:
        app.state.session_factory = session_factory
        response = client.post(
            "/runs",
            data={"customer_id": "acme_corp"},
            files={"file": ("invoice.png", _sample_png_bytes(), "image/png")},
        )

        assert response.status_code == 200
        run_id = response.json()["run_id"]

        get_response = client.get(f"/runs/{run_id}")
        assert get_response.status_code == 200
        assert get_response.json()["run_id"] == run_id
        assert get_response.json()["customer_id"] == "acme_corp"


def test_post_query_returns_actual_stored_count(tmp_path) -> None:
    app = create_app()
    session_factory = init_db(f"sqlite:///{tmp_path / 'api-query.db'}")
    app.dependency_overrides[get_query_agent] = lambda: QueryAgent(
        session_factory=session_factory,
        planner=StaticPlanner(
            QueryPlan(
                tool_calls=[
                    QueryToolCall(
                        name="count_runs",
                        args={"filters": {}},
                    )
                ]
            )
        ),
    )
    with session_scope(session_factory) as session:
        repo = PipelineRunRepository(session)
        repo.save_run(_run("acme_corp"))
        repo.save_run(_run("globex"))

    with TestClient(app) as client:
        app.state.session_factory = session_factory
        response = client.post("/query", json={"question": "how many runs total"})

    assert response.status_code == 200
    body = response.json()
    assert "2" in body["answer"]
    assert body["evidence"]["tool_calls"][0]["tool_name"] == "count_runs"
    assert body["evidence"]["tool_calls"][0]["result"] == 2


def _run(customer_id: str) -> PipelineRun:
    now = datetime.now(UTC)
    run_id = uuid4()
    return PipelineRun(
        run_id=run_id,
        document_id=f"doc-{run_id}",
        customer_id=customer_id,
        status=PipelineRunStatus.COMPLETED,
        stages=[
            StageEvent(
                stage=StageName.STORAGE,
                status=StageStatus.COMPLETED,
                started_at=now,
                completed_at=now,
                latency_ms=1,
                cost_usd=0.0,
                trace_id=str(run_id),
            )
        ],
        started_at=now,
        completed_at=now,
        cost_total_usd=0.0,
        trace_id=str(run_id),
    )


def _sample_png_bytes() -> bytes:
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 100), "Commercial Invoice", fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
