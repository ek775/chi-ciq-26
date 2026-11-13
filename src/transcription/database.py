import math
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import sessionmaker

from .metadata import MeetingMetadata
from .models import Base, Meeting, Speaker, TranscriptSegment


def create_database(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("This library requires PostgreSQL with pgvector")
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(connection)
    return engine


def validate_vector(values: Sequence[float]) -> list[float]:
    vector = [float(value) for value in values]
    if not vector or not all(math.isfinite(value) for value in vector):
        raise ValueError("Embeddings must be nonempty and contain only finite numbers")
    if not any(vector):
        raise ValueError("Embeddings must not be zero vectors")
    return vector


class TranscriptStore:
    def __init__(self, engine: Engine):
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    def start(
        self,
        source: Path,
        metadata: MeetingMetadata,
        *,
        transcription_model: str,
        diarization_model: str,
        text_embedding_model: str,
        voice_embedding_model: str,
    ) -> UUID:
        with self.sessions.begin() as session:
            meeting = Meeting(
                source_path=str(source.resolve()),
                filename=metadata.filename,
                meeting_date=metadata.meeting_date,
                meeting_type=metadata.meeting_type,
                transcription_model=transcription_model,
                diarization_model=diarization_model,
                text_embedding_model=text_embedding_model,
                voice_embedding_model=voice_embedding_model,
            )
            session.add(meeting)
            session.flush()
            return meeting.id

    def set_recording_duration(self, meeting_id: UUID, duration_seconds: float) -> None:
        """Store decoded audio length before appending recording-relative segments."""
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("Recording duration must be finite and nonnegative")
        with self.sessions.begin() as session:
            meeting = session.get(Meeting, meeting_id)
            if meeting is None or meeting.status != "processing":
                raise ValueError("Meeting must exist and be processing")
            existing = meeting.recording_duration_seconds
            if existing is not None and existing != duration_seconds:
                raise ValueError("Recording duration has already been set")
            meeting.recording_duration_seconds = duration_seconds

    def append(
        self,
        meeting_id: UUID,
        *,
        sequence: int,
        speaker: str,
        start: float,
        end: float,
        transcript: str,
        text_embedding: Sequence[float],
        voice_embedding: Sequence[float],
    ) -> TranscriptSegment:
        if not speaker.strip() or not transcript.strip():
            raise ValueError("Speaker label and transcript must not be empty")
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise ValueError(
                "Segment timestamps must be finite and satisfy 0 <= start < end"
            )
        text_vector = validate_vector(text_embedding)
        voice_vector = validate_vector(voice_embedding)
        with self.sessions.begin() as session:
            meeting = session.get(Meeting, meeting_id)
            if meeting is None or meeting.status != "processing":
                raise ValueError("Meeting must exist and be processing")
            if meeting.recording_duration_seconds is None:
                raise ValueError(
                    "Recording duration must be set before appending segments"
                )
            if end > meeting.recording_duration_seconds:
                raise ValueError("Segment end offset exceeds recording duration")
            for field, size in (
                ("text_embedding_dimensions", len(text_vector)),
                ("voice_embedding_dimensions", len(voice_vector)),
            ):
                existing = getattr(meeting, field)
                if existing is not None and existing != size:
                    raise ValueError(
                        f"Inconsistent {field}: expected {existing}, got {size}"
                    )
                setattr(meeting, field, size)
            speaker_row = session.scalar(
                select(Speaker).where(
                    Speaker.meeting_id == meeting_id, Speaker.label == speaker
                )
            )
            if speaker_row is None:
                speaker_row = Speaker(meeting_id=meeting_id, label=speaker)
                session.add(speaker_row)
                session.flush()
            segment = TranscriptSegment(
                meeting_id=meeting_id,
                speaker_id=speaker_row.id,
                sequence=sequence,
                start_seconds=start,
                end_seconds=end,
                text=transcript,
                text_embedding=text_vector,
                voice_embedding=voice_vector,
            )
            session.add(segment)
            session.flush()
        return segment

    def finish(self, meeting_id: UUID, *, completed: bool) -> None:
        with self.sessions.begin() as session:
            meeting = session.get(Meeting, meeting_id)
            if meeting is None:
                raise ValueError(f"Unknown meeting: {meeting_id}")
            meeting.status = "completed" if completed else "incomplete"
