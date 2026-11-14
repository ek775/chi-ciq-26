"""Read-only snapshots of published meetings and mirrored documents.

Each load runs in one ``REPEATABLE READ, READ ONLY`` transaction so a concurrent
ingestion replacement cannot split a source across two runs. Readers return
plain dataclasses; they never write to the source databases.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine

from databases.documents.schema import chunk_table, directories, documents
from databases.meetings.models import (
    Meeting,
    PublishedSource,
    Speaker,
    TranscriptSegment,
)

from .units import (
    Segment,
    SpeakerSummary,
    Unit,
    build_turns,
    clean_document_chunk,
    normalize_body,
    normalized,
    parse_document_title,
    summarize_speakers,
)

MEETING_PREFIX = "meetings:"
DOCUMENT_PREFIX = "documents:"


@dataclass(frozen=True)
class SourceSnapshot:
    key: str
    kind: str
    source_ref: str
    signature: str
    title: str
    body: str
    meeting_date: date | None
    text_embedding_model: str
    duration_seconds: float | None = None
    voice_embedding_model: str | None = None
    speakers: list[SpeakerSummary] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)


@contextmanager
def snapshot(engine: Engine) -> Iterator[Connection]:
    with engine.begin() as connection:
        connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        yield connection


def _published_meetings():
    return (
        select(
            PublishedSource.key,
            Meeting.id,
            Meeting.filename,
            Meeting.meeting_date,
            Meeting.meeting_type,
            Meeting.recording_duration_seconds,
            Meeting.text_embedding_model,
            Meeting.voice_embedding_model,
        )
        .join(Meeting, Meeting.id == PublishedSource.meeting_id)
        .where(Meeting.status == "completed")
    )


class MeetingsReader:
    kind = "meeting"
    prefix = MEETING_PREFIX

    def __init__(
        self,
        engine: Engine,
        *,
        max_turn_characters: int = 1500,
        max_gap_seconds: float = 30.0,
    ):
        self.engine = engine
        self.max_turn_characters = max_turn_characters
        self.max_gap_seconds = max_gap_seconds

    def signatures(self) -> dict[str, str]:
        with snapshot(self.engine) as connection:
            rows = connection.execute(_published_meetings()).mappings()
            return {self.prefix + row["key"]: str(row["id"]) for row in rows}

    def load(self, key: str) -> SourceSnapshot | None:
        published = key.removeprefix(self.prefix)
        with snapshot(self.engine) as connection:
            meeting = (
                connection.execute(
                    _published_meetings().where(PublishedSource.key == published)
                )
                .mappings()
                .one_or_none()
            )
            if meeting is None:
                return None
            rows = connection.execute(
                select(
                    Speaker.label,
                    TranscriptSegment.start_seconds,
                    TranscriptSegment.end_seconds,
                    TranscriptSegment.text,
                    TranscriptSegment.text_embedding,
                    TranscriptSegment.voice_embedding,
                )
                .join(
                    Speaker,
                    (Speaker.id == TranscriptSegment.speaker_id)
                    & (Speaker.meeting_id == TranscriptSegment.meeting_id),
                )
                .where(TranscriptSegment.meeting_id == meeting["id"])
                .order_by(TranscriptSegment.sequence)
            ).all()
        segments = [
            Segment(
                speaker=row.label,
                start=row.start_seconds,
                end=row.end_seconds,
                text=row.text,
                text_embedding=row.text_embedding,
                voice_embedding=row.voice_embedding,
            )
            for row in rows
        ]
        return SourceSnapshot(
            key=key,
            kind=self.kind,
            source_ref=str(meeting["id"]),
            signature=str(meeting["id"]),
            title=meeting["filename"],
            body=normalize_body(meeting["meeting_type"]),
            meeting_date=meeting["meeting_date"],
            duration_seconds=meeting["recording_duration_seconds"],
            text_embedding_model=meeting["text_embedding_model"],
            voice_embedding_model=meeting["voice_embedding_model"],
            speakers=summarize_speakers(segments),
            units=build_turns(
                segments,
                max_characters=self.max_turn_characters,
                max_gap_seconds=self.max_gap_seconds,
            ),
        )


def _document_signature(row) -> str:
    return (
        f"{row['content_sha256']};{row['processing_signature']};"
        f"{row['text_embedding_model']}"
    )


class DocumentsReader:
    kind = "document"
    prefix = DOCUMENT_PREFIX

    def __init__(self, engine: Engine):
        self.engine = engine

    def _documents(self):
        return select(
            documents.c.key,
            documents.c.relative_path,
            documents.c.filename,
            documents.c.content_sha256,
            documents.c.processing_signature,
            documents.c.text_embedding_model,
            directories.c.table_name,
        ).join(directories, directories.c.key == documents.c.directory_key)

    def signatures(self) -> dict[str, str]:
        with snapshot(self.engine) as connection:
            rows = connection.execute(self._documents()).mappings()
            return {self.prefix + row["key"]: _document_signature(row) for row in rows}

    def load(self, key: str) -> SourceSnapshot | None:
        document_key = key.removeprefix(self.prefix)
        with snapshot(self.engine) as connection:
            row = (
                connection.execute(
                    self._documents().where(documents.c.key == document_key)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            table = chunk_table(row["table_name"])
            chunks = connection.execute(
                select(table.c.sequence, table.c.text, table.c.text_embedding)
                .where(table.c.document_key == document_key)
                .order_by(table.c.sequence)
            ).all()
        units = []
        for chunk in chunks:
            cleaned, start, end = clean_document_chunk(chunk.text)
            if not cleaned:
                continue
            units.append(
                Unit(
                    sequence=len(units),
                    text=cleaned,
                    embedding=normalized(chunk.text_embedding).tolist(),
                    start=start,
                    end=end,
                )
            )
        meeting_date, body = parse_document_title(row["relative_path"])
        return SourceSnapshot(
            key=key,
            kind=self.kind,
            source_ref=document_key,
            signature=_document_signature(row),
            title=row["filename"],
            body=body,
            meeting_date=meeting_date,
            text_embedding_model=row["text_embedding_model"],
            units=units,
        )
