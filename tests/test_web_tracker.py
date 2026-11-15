from fastapi import FastAPI
from fastapi.testclient import TestClient

from web import tracker
from web.analytics import _connection


class Connection:
    def __init__(self, exists=True):
        self.exists = exists

    def exec_driver_sql(self, query):
        return self

    def scalar(self):
        return self.exists


def client_for(connection):
    app = FastAPI()
    app.include_router(tracker.tracker_router())
    app.dependency_overrides[_connection] = lambda: connection
    return TestClient(app)


def test_tracker_unavailable():
    with client_for(Connection(False)) as client:
        response = client.get("/api/tracker/batches")
    assert response.status_code == 503
    assert response.json()["detail"] == "Tracker has not been published"


def test_tracker_publishes_only_verified_quotes_and_confirmed_speakers(monkeypatch):
    def rows(connection, query, **params):
        if "SELECT details" in query:
            return [{"details": {"coverage": "selected", "digest": None}}] if params["id"] == "demo" else []
        if "tracker.statements" in query:
            return [
                {"record": {
                    "statement_id": "one", "verified": True, "quote": "A promise.",
                    "speaker_confidence": "unconfirmed", "speaker": "Wrong name",
                    "person_id": "wrong", "commitment": {"who": "Wrong name"},
                    "video_link": "https://vimeo.com/123#t=1m",
                }},
                {"record": {
                    "statement_id": "two", "verified": True, "quote": "Confirmed.",
                    "speaker_confidence": "named", "speaker": "Confirmed name",
                    "video_link": "https://vimeo.com.evil.example/123",
                }},
                {"record": {"statement_id": "three", "verified": False, "quote": "Invented"}},
            ]
        if "tracker.actions" in query:
            return [{"record": {"action_id": "vote", "vote": "Aye"}}]
        return [{"batch_id": "demo", "coverage": "selected", "title": None}]

    monkeypatch.setattr(tracker, "_rows", rows)
    with client_for(Connection()) as client:
        assert client.get("/api/tracker/batches").json()[0]["batch_id"] == "demo"
        result = client.get("/api/tracker/batches/demo").json()
        assert client.get("/api/tracker/batches/missing").status_code == 404

    assert result["coverage"] == "selected"
    assert result["actions"][0]["vote"] == "Aye"
    assert len(result["statements"]) == 2
    one, two = result["statements"]
    assert one["speaker"] is None and one["person_id"] is None
    assert one["commitment"]["who"] is None
    assert one["video_link"] == "https://vimeo.com/123#t=1m"
    assert two["speaker"] == "Confirmed name" and two["video_link"] is None
