from datetime import date, datetime
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'completed', 'incomplete')"),
        CheckConstraint(
            "recording_duration_seconds >= 0 "
            "AND recording_duration_seconds < 'Infinity'::double precision"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_path: Mapped[str]
    filename: Mapped[str]
    meeting_date: Mapped[date]
    meeting_type: Mapped[str]
    status: Mapped[str] = mapped_column(default="processing")
    recording_duration_seconds: Mapped[float | None] = mapped_column(
        comment="Decoded first audio track duration in seconds; NULL before extraction."
    )
    transcription_model: Mapped[str]
    diarization_model: Mapped[str]
    text_embedding_model: Mapped[str]
    voice_embedding_model: Mapped[str]
    text_embedding_dimensions: Mapped[int | None]
    voice_embedding_dimensions: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="Database audit time, not a recording offset.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="Database audit time, not a recording offset.",
    )


class Speaker(Base):
    __tablename__ = "speakers"
    __table_args__ = (
        UniqueConstraint("meeting_id", "label"),
        UniqueConstraint("meeting_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="Database audit time, not a recording offset.",
    )


class PublishedSource(Base):
    __tablename__ = "published_sources"
    __table_args__ = (CheckConstraint("length(content_sha256) = 64"),)

    key: Mapped[str] = mapped_column(primary_key=True)
    content_sha256: Mapped[str]
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id"), unique=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["meeting_id", "speaker_id"],
            ["speakers.meeting_id", "speakers.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("meeting_id", "sequence"),
        CheckConstraint("start_seconds >= 0 AND end_seconds > start_seconds"),
        CheckConstraint("sequence >= 0"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    speaker_id: Mapped[UUID] = mapped_column(index=True)
    sequence: Mapped[int]
    start_seconds: Mapped[float] = mapped_column(
        comment="Inclusive offset in seconds from the decoded audio track's start."
    )
    end_seconds: Mapped[float] = mapped_column(
        comment="Exclusive offset in seconds from the decoded audio track's start."
    )
    text: Mapped[str]
    text_embedding: Mapped[NDArray[np.float32]] = mapped_column(Vector())
    voice_embedding: Mapped[NDArray[np.float32]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="Database audit time, not a recording offset.",
    )
