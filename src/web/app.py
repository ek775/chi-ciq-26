import logging
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from databases.meetings.models import (
    Meeting,
    PublishedSource,
    Speaker,
    TranscriptSegment,
)
from databases.meetings.schema import check_schema, connect_database

logger = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")


class MeetingView(BaseModel):
    id: UUID
    filename: str
    meeting_date: date
    meeting_type: str
    recording_duration_seconds: float | None


class SegmentView(BaseModel):
    id: UUID
    meeting_id: UUID
    sequence: int
    speaker: str
    start_seconds: float
    end_seconds: float
    text: str


def published_meetings():
    return (
        select(
            Meeting.id,
            Meeting.filename,
            Meeting.meeting_date,
            Meeting.meeting_type,
            Meeting.recording_duration_seconds,
        )
        .join(PublishedSource, PublishedSource.meeting_id == Meeting.id)
        .where(Meeting.status == "completed")
    )


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = engine or connect_database(os.environ["DATABASE_URL"])
        app.state.database = database
        try:
            check_schema(database)
            yield
        finally:
            if engine is None:
                database.dispose()

    app = FastAPI(title="Chicago meeting transcripts", lifespan=lifespan)

    def session(request: Request) -> Iterator[Session]:
        with Session(request.app.state.database) as database, database.begin():
            database.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            yield database

    Database = Annotated[Session, Depends(session)]

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError):
        logger.error("Database request failed: %s", request.url.path, exc_info=error)
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health(database: Database):
        database.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/ready")
    def ready(request: Request):
        try:
            check_schema(request.app.state.database)
        except RuntimeError as error:
            logger.error("Schema readiness failed", exc_info=error)
            raise HTTPException(503, "Database schema is not ready") from error
        return {"status": "ready"}

    @app.get("/api/meetings", response_model=list[MeetingView])
    def meetings(
        database: Database,
        q: Annotated[str, Query(max_length=500)] = "",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        query = published_meetings()
        if q.strip():
            matching = (
                select(TranscriptSegment.id)
                .where(
                    TranscriptSegment.meeting_id == Meeting.id,
                    TranscriptSegment.text.icontains(q.strip(), autoescape=True),
                )
                .exists()
            )
            query = query.where(
                Meeting.meeting_type.icontains(q.strip(), autoescape=True) | matching
            )
        rows = database.execute(
            query.order_by(Meeting.meeting_date.desc(), Meeting.id)
            .offset(offset)
            .limit(limit)
        ).mappings()
        return [MeetingView.model_validate(dict(row)) for row in rows]

    def require_meeting(database: Session, meeting_id: UUID) -> MeetingView:
        row = (
            database.execute(published_meetings().where(Meeting.id == meeting_id))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(404, "Published meeting not found")
        return MeetingView.model_validate(dict(row))

    @app.get("/api/meetings/{meeting_id}", response_model=MeetingView)
    def meeting(meeting_id: UUID, database: Database):
        return require_meeting(database, meeting_id)

    @app.get("/api/meetings/{meeting_id}/segments", response_model=list[SegmentView])
    def segments(
        meeting_id: UUID,
        database: Database,
        q: Annotated[str, Query(max_length=500)] = "",
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        require_meeting(database, meeting_id)
        query = (
            select(
                TranscriptSegment.id,
                TranscriptSegment.meeting_id,
                TranscriptSegment.sequence,
                Speaker.label.label("speaker"),
                TranscriptSegment.start_seconds,
                TranscriptSegment.end_seconds,
                TranscriptSegment.text,
            )
            .join(
                Speaker,
                (Speaker.id == TranscriptSegment.speaker_id)
                & (Speaker.meeting_id == TranscriptSegment.meeting_id),
            )
            .where(TranscriptSegment.meeting_id == meeting_id)
        )
        if q.strip():
            query = query.where(
                TranscriptSegment.text.icontains(q.strip(), autoescape=True)
            )
        rows = database.execute(
            query.order_by(TranscriptSegment.sequence).offset(offset).limit(limit)
        ).mappings()
        return [SegmentView.model_validate(dict(row)) for row in rows]

    @app.get("/api/stats")
    def stats(database: Database):
        total = database.scalar(
            select(func.count()).select_from(published_meetings().subquery())
        )
        return {"meetings": total}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    uvicorn.run(create_app(), host="0.0.0.0", port=8080)
