import shutil
import subprocess
import wave
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import func, select

from chi_ciq_transcription import (
    Meeting,
    Speaker,
    SpeakerTurn,
    TranscriptionWorkflow,
    TranscriptSegment,
)
from chi_ciq_transcription.backends import SAMPLE_RATE
from chi_ciq_transcription.workflow import extract_audio


class FakeDiarizer:
    model_name = "fake-diarizer"

    def diarize(self, audio_path):
        assert Path(audio_path).is_file()
        return [
            SpeakerTurn(0, 1.5, "SPEAKER_00"),
            SpeakerTurn(1.5, 2, "SPEAKER_01"),
            SpeakerTurn(2, 3, "SPEAKER_00"),
        ]


class FakeTranscriber:
    model_name = "fake-whisper"

    def transcribe(self, audio):
        assert audio.dtype == np.float32
        assert 0 < len(audio) <= SAMPLE_RATE
        return f"Transcript for {len(audio)} samples."


class FakeTextEmbedder:
    model_name = "fake-text"

    def embed(self, text):
        assert text.startswith("Transcript")
        return [0.1, 0.2, 0.3]


class FakeVoiceEmbedder:
    model_name = "fake-voice"

    def embed(self, audio):
        return [0.4, 0.5]


@pytest.fixture
def source(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for audio extraction tests")
    source = tmp_path / "2026_november_10_-_committee_on_budget (240p).mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    return source


@pytest.fixture
def workflow(store):
    return TranscriptionWorkflow(
        store,
        FakeDiarizer(),
        FakeTranscriber(),
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
        max_chunk_seconds=1,
    )


def test_iteration_commits_each_chunk_with_relational_metadata(workflow, source, store):
    expected_audio = source.parent / "expected.wav"
    extract_audio(source, expected_audio)
    with wave.open(str(expected_audio), "rb") as audio:
        expected_duration = audio.getnframes() / audio.getframerate()
    with closing(workflow.transcribe(source)) as stream:
        first = next(stream)
        with store.sessions() as session:
            assert (
                session.scalar(select(func.count()).select_from(TranscriptSegment)) == 1
            )
            meeting = session.get(Meeting, first.meeting_id)
            assert meeting.status == "processing"
            assert meeting.meeting_type == "committee on budget"
            assert str(meeting.meeting_date) == "2026-11-10"
            assert meeting.text_embedding_model == "fake-text"
            assert meeting.text_embedding_dimensions == 3
            assert meeting.voice_embedding_dimensions == 2
            assert meeting.recording_duration_seconds == expected_duration
            assert meeting.recording_duration_seconds >= 3
            assert meeting.created_at.tzinfo is not None
            assert meeting.updated_at.tzinfo is not None
        segments = [first, *stream]
    assert [(s.start_seconds, s.end_seconds) for s in segments] == [
        (0, 1),
        (1, 1.5),
        (1.5, 2),
        (2, 3),
    ]
    assert [s.sequence for s in segments] == [0, 1, 2, 3]
    assert all(
        0 <= s.start_seconds < s.end_seconds <= expected_duration for s in segments
    )
    assert segments[0].speaker_id == segments[1].speaker_id == segments[3].speaker_id
    assert segments[2].speaker_id != segments[0].speaker_id
    with store.sessions() as session:
        assert session.get(Meeting, first.meeting_id).status == "completed"
        assert session.scalar(select(func.count()).select_from(Speaker)) == 2
        saved = session.get(TranscriptSegment, first.id)
        np.testing.assert_allclose(saved.text_embedding, [0.1, 0.2, 0.3])
        np.testing.assert_allclose(saved.voice_embedding, [0.4, 0.5])
        assert saved.created_at.tzinfo is not None
        distance = session.scalar(
            select(
                TranscriptSegment.text_embedding.cosine_distance([0.1, 0.2, 0.3])
            ).where(TranscriptSegment.id == first.id)
        )
        assert abs(distance) < 1e-6


def test_early_close_preserves_committed_progress(workflow, source, store):
    stream = workflow.transcribe(source)
    first = next(stream)
    stream.close()
    with store.sessions() as session:
        assert session.get(Meeting, first.meeting_id).status == "incomplete"
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 1


def test_model_failure_keeps_prior_rows_and_propagates(workflow, source, store):
    stream = workflow.transcribe(source)
    first = next(stream)

    def fail(_audio):
        raise RuntimeError("model failed")

    workflow.transcriber.transcribe = fail
    with pytest.raises(RuntimeError, match="model failed"):
        next(stream)
    with store.sessions() as session:
        assert session.get(Meeting, first.meeting_id).status == "incomplete"
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 1


def test_empty_transcript_is_not_silently_discarded(workflow, source, store):
    workflow.transcriber.transcribe = lambda audio: " "
    with pytest.raises(ValueError, match="empty text"):
        list(workflow.transcribe(source))
    with store.sessions() as session:
        assert session.scalar(select(Meeting.status)) == "incomplete"
        assert session.scalar(select(Meeting.recording_duration_seconds)) >= 3
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0


def test_ffmpeg_errors_are_visible(workflow, tmp_path, store):
    source = tmp_path / "2026-11-10_budget.mp4"
    source.write_bytes(b"not an mp4")
    with pytest.raises(RuntimeError, match="FFmpeg audio extraction failed"):
        list(workflow.transcribe(source))
    with store.sessions() as session:
        assert session.scalar(select(Meeting.status)) == "incomplete"
        assert session.scalar(select(Meeting.recording_duration_seconds)) is None


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_chunk_limit(seconds):
    with pytest.raises(ValueError, match="max_chunk_seconds"):
        TranscriptionWorkflow(
            None,
            FakeDiarizer(),
            FakeTranscriber(),
            FakeTextEmbedder(),
            FakeVoiceEmbedder(),
            max_chunk_seconds=seconds,
        )


def test_invalid_turn_timestamps(workflow, source, store):
    workflow.diarizer.diarize = lambda path: [SpeakerTurn(0, 100, "SPEAKER_00")]
    with pytest.raises(ValueError, match="invalid timestamps"):
        list(workflow.transcribe(source))


def test_duration_includes_audio_without_speaker_turns(workflow, source, store):
    workflow.diarizer.diarize = lambda path: []
    assert list(workflow.transcribe(source)) == []
    with store.sessions() as session:
        meeting = session.scalar(select(Meeting))
        assert meeting.status == "completed"
        assert meeting.recording_duration_seconds >= 3


def test_duration_persists_before_diarization(workflow, source, store):
    def fail(_path):
        with store.sessions() as session:
            assert session.scalar(select(Meeting.recording_duration_seconds)) >= 3
        raise RuntimeError("diarization failed")

    workflow.diarizer.diarize = fail
    with pytest.raises(RuntimeError, match="diarization failed"):
        list(workflow.transcribe(source))
    with store.sessions() as session:
        assert session.scalar(select(Meeting.status)) == "incomplete"
        assert session.scalar(select(Meeting.recording_duration_seconds)) >= 3


def test_directory_batch_processes_all_recordings(workflow, source, store):
    from examples.batch_transcribe import discover_recordings, transcribe_recordings

    second = source.parent / "2026-11-11_budget.mp4"
    shutil.copyfile(source, second)
    transcribe_recordings(workflow, discover_recordings(source.parent))
    with store.sessions() as session:
        meetings = session.scalars(select(Meeting).order_by(Meeting.meeting_date)).all()
        assert [meeting.filename for meeting in meetings] == [source.name, second.name]
        assert all(meeting.status == "completed" for meeting in meetings)
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 8
