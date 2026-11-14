from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_pipelines import FakeWorkflow

from pipelines.transcription.pipeline import IngestionPipeline
from web.app import create_app


@pytest.fixture
def web(engine, tmp_path):
    path = tmp_path / "2026-11-10_budget.mp4"
    path.write_text("Budget 100% <script>not executable</script>")
    pipeline = IngestionPipeline(engine, FakeWorkflow)
    pipeline.ingest(path)
    with TestClient(create_app(engine)) as client:
        yield client, pipeline, path


def test_ui_health_and_readiness(web):
    client, _, _ = web
    assert client.get("/").status_code == 200
    assert "Chicago meeting transcripts" in client.get("/").text
    assert "words.textContent" in client.get("/static/app.js").text
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    assert client.get("/api/stats").json() == {"meetings": 1}


def test_browse_search_pagination_and_no_sensitive_fields(web):
    client, _, _ = web
    rows = client.get("/api/meetings").json()
    assert len(rows) == 1
    meeting = rows[0]
    assert "source_path" not in meeting
    assert "text_embedding" not in meeting
    assert client.get("/api/meetings?q=budget").json() == rows
    assert client.get("/api/meetings?q=100%25").json() == rows
    assert client.get("/api/meetings?q=_").json() == []
    assert client.get("/api/meetings?q=missing").json() == []
    assert client.get("/api/meetings?offset=1").json() == []
    assert client.get(f"/api/meetings/{meeting['id']}").json() == meeting
    segments = client.get(f"/api/meetings/{meeting['id']}/segments").json()
    assert segments[0]["speaker"] == "SPEAKER_00"
    assert segments[0]["start_seconds"] == 1
    assert "text_embedding" not in segments[0]
    assert segments[0]["text"].endswith("<script>not executable</script>")
    assert client.get(f"/api/meetings/{meeting['id']}/segments?q=missing").json() == []
    assert client.get(f"/api/meetings/{meeting['id']}/segments?offset=1").json() == []
    assert client.get("/api/meetings").headers["cache-control"] == "no-store"
    assert client.post("/api/meetings", json={}).status_code == 405


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=201", "offset=-1", "q=" + "x" * 501]
)
def test_api_validation(web, query):
    client, _, _ = web
    assert client.get(f"/api/meetings?{query}").status_code == 422
    assert client.get("/api/meetings/not-a-uuid").status_code == 422


def test_staged_failed_and_superseded_runs_are_not_public(web):
    client, pipeline, path = web
    old = client.get("/api/meetings").json()[0]["id"]
    path.write_text("Updated")

    def during(snapshot):
        assert client.get("/api/meetings").json()[0]["id"] == old
        assert (
            client.get(f"/api/meetings/{pipeline.store.meeting_id}").status_code == 404
        )

    pipeline.workflow.during = during
    pipeline.workflow.fail = True
    with pytest.raises(RuntimeError, match="inference failed"):
        pipeline.ingest(path)
    assert client.get("/api/meetings").json()[0]["id"] == old
    pipeline.workflow.fail = False
    pipeline.ingest(path)
    assert client.get("/api/meetings").json()[0]["id"] != old
    assert client.get(f"/api/meetings/{old}").status_code == 404
    assert client.get(f"/api/meetings/{uuid4()}/segments").status_code == 404


def test_readiness_reports_missing_schema_without_details(web):
    client, pipeline, _ = web
    with pipeline.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE meetings DROP COLUMN recording_duration_seconds")
        )
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database schema is not ready"}
