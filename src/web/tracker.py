"""Read published, reviewed tracker batches from the analytics database."""

from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Path

from .analytics import Database, _rows


def _public_statement(record: dict) -> dict | None:
    if record.get("verified") is not True or not record.get("quote"):
        return None
    result = dict(record)
    if result.get("speaker_confidence") not in {"named", "continuing"}:
        result["speaker"] = None
        result["person_id"] = None
        if isinstance(result.get("commitment"), dict):
            result["commitment"] = {**result["commitment"], "who": None}
    try:
        url = urlparse(result.get("video_link") or "")
        valid_url = (
            url.scheme == "https"
            and url.hostname in {"vimeo.com", "www.vimeo.com"}
            and not url.username
            and not url.password
            and url.port in (None, 443)
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        result["video_link"] = None
    return result


def tracker_router() -> APIRouter:
    router = APIRouter(prefix="/api/tracker", tags=["tracker"])

    def require_tracker(database: Database):
        exists = database.exec_driver_sql(
            "SELECT to_regclass('tracker.batches') IS NOT NULL "
            "AND to_regclass('tracker.statements') IS NOT NULL "
            "AND to_regclass('tracker.actions') IS NOT NULL"
        ).scalar()
        if not exists:
            raise HTTPException(503, "Tracker has not been published")

    @router.get("/batches")
    def batches(database: Database):
        require_tracker(database)
        return _rows(
            database,
            "SELECT batch_id, details->>'coverage' AS coverage, "
            "details->'digest'->>'title' AS title FROM tracker.batches "
            "ORDER BY batch_id LIMIT 200",
        )

    @router.get("/batches/{batch_id}")
    def batch(
        batch_id: Annotated[str, Path(min_length=1, max_length=200)], database: Database
    ):
        require_tracker(database)
        batches = _rows(
            database, "SELECT details FROM tracker.batches WHERE batch_id = :id",
            id=batch_id,
        )
        if not batches:
            raise HTTPException(404, "Tracker batch not found")
        statements = [
            statement
            for row in _rows(
                database,
                "SELECT record FROM tracker.statements WHERE batch_id = :id "
                "ORDER BY record_id",
                id=batch_id,
            )
            if (statement := _public_statement(row["record"])) is not None
        ]
        actions = _rows(
            database,
            "SELECT record FROM tracker.actions WHERE batch_id = :id ORDER BY record_id",
            id=batch_id,
        )
        details = batches[0]["details"]
        return {
            "batch_id": batch_id,
            "coverage": details.get("coverage"),
            "digest": details.get("digest"),
            "follow_up": details.get("follow_up"),
            "statements": statements,
            "actions": [row["record"] for row in actions],
        }

    return router
