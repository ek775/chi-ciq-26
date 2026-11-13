from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from transcription import Meeting, Speaker, TranscriptSegment, parse_filename
from transcription.database import create_database, validate_vector


def start(store):
    path = Path("2026-11-10_budget.mp4")
    meeting_id = store.start(
        path,
        parse_filename(path),
        transcription_model="whisper",
        diarization_model="pyannote",
        text_embedding_model="text",
        voice_embedding_model="voice",
    )
    store.set_recording_duration(meeting_id, 10)
    return meeting_id


def append(store, meeting_id, **overrides):
    values = {
        "sequence": 0,
        "speaker": "SPEAKER_00",
        "start": 0,
        "end": 1,
        "transcript": "Hello",
        "text_embedding": [1, 2, 3],
        "voice_embedding": [1, 2],
    }
    values.update(overrides)
    return store.append(meeting_id, **values)


@pytest.mark.parametrize("vector", [[], [0, 0], [float("nan")], [float("inf")]])
def test_invalid_vectors(vector):
    with pytest.raises(ValueError, match="Embeddings"):
        validate_vector(vector)


def test_database_requires_postgres():
    with pytest.raises(ValueError, match="PostgreSQL"):
        create_database("sqlite://")


def test_inconsistent_dimensions_rollback(store):
    meeting_id = start(store)
    append(store, meeting_id)
    with pytest.raises(ValueError, match="Inconsistent"):
        append(store, meeting_id, sequence=1, text_embedding=[1, 2])
    with store.sessions() as session:
        assert len(session.scalars(select(TranscriptSegment)).all()) == 1
        assert session.get(Meeting, meeting_id).text_embedding_dimensions == 3


def test_speakers_are_scoped_to_meetings(store):
    first = append(store, start(store))
    second = append(store, start(store))
    assert first.speaker_id != second.speaker_id


def test_cross_meeting_speaker_reference_rejected_by_database(store):
    first = append(store, start(store))
    other_meeting = start(store)
    with pytest.raises(IntegrityError), store.sessions.begin() as session:
        session.add(
            TranscriptSegment(
                meeting_id=other_meeting,
                speaker_id=first.speaker_id,
                sequence=0,
                start_seconds=0,
                end_seconds=1,
                text="Hello",
                text_embedding=[1, 2, 3],
                voice_embedding=[1, 2],
            )
        )


def test_meeting_delete_cascades(store):
    segment = append(store, start(store))
    with store.sessions.begin() as session:
        meeting = session.get(Meeting, segment.meeting_id)
        session.delete(meeting)
    with store.sessions() as session:
        assert session.get(TranscriptSegment, segment.id) is None
        assert session.get(Speaker, segment.speaker_id) is None


def test_unknown_meeting_is_explicit(store):
    with pytest.raises(ValueError, match="Meeting must exist"):
        append(store, uuid4())


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf")])
def test_invalid_recording_duration(store, duration):
    meeting_id = start(store)
    with pytest.raises(ValueError, match="finite and nonnegative"):
        store.set_recording_duration(meeting_id, duration)


def test_segment_offsets_cannot_exceed_recording_duration(store):
    meeting_id = start(store)
    append(store, meeting_id, start=9, end=10)
    with pytest.raises(ValueError, match="exceeds recording duration"):
        append(store, meeting_id, sequence=1, start=9, end=10.1)


def test_recording_duration_cannot_be_changed(store):
    meeting_id = start(store)
    store.set_recording_duration(meeting_id, 10)
    with pytest.raises(ValueError, match="already been set"):
        store.set_recording_duration(meeting_id, 5)


def test_unknown_duration_blocks_segment_append(store):
    meeting_id = start(store)
    with store.sessions.begin() as session:
        session.get(Meeting, meeting_id).recording_duration_seconds = None
    with pytest.raises(ValueError, match="must be set"):
        append(store, meeting_id)
