import logging
import shutil
import subprocess
import wave
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import func, select

from pipelines.transcription import (
    DiarizedChunk,
    Meeting,
    Speaker,
    SpeakerTurn,
    TranscribedWord,
    TranscriptionWorkflow,
    TranscriptSegment,
)
from pipelines.transcription.backends import SAMPLE_RATE
from pipelines.transcription.workflow import align_words, extract_audio


class FakeDiarizer:
    model_name = "fake-diarizer"

    def diarize(self, audio_path):
        assert Path(audio_path).is_file()
        return [
            SpeakerTurn(0, 1.5, "SPEAKER_00"),
            SpeakerTurn(1.5, 2, "SPEAKER_01"),
            SpeakerTurn(2, 3, "SPEAKER_00"),
        ]

    def diarize_chunks(self, audio_path, chunk_frames):
        with wave.open(audio_path, "rb") as audio:
            frame_count = audio.getnframes()
        turns = self.diarize(audio_path)
        for start in range(0, frame_count, chunk_frames):
            end = min(start + chunk_frames, frame_count)
            active = []
            for turn in turns:
                if turn.end > frame_count / SAMPLE_RATE:
                    active.append(turn)
                elif turn.start < end / SAMPLE_RATE and turn.end > start / SAMPLE_RATE:
                    active.append(
                        SpeakerTurn(
                            max(turn.start, start / SAMPLE_RATE),
                            min(turn.end, end / SAMPLE_RATE),
                            turn.speaker,
                        )
                    )
            yield DiarizedChunk(start, end, active)


class FakeTranscriber:
    model_name = "fake-whisper"

    def transcribe_words(self, audio):
        assert audio.dtype == np.float32
        assert 0 < len(audio) <= SAMPLE_RATE
        duration = len(audio) / SAMPLE_RATE
        return [
            TranscribedWord(0, duration / 2, "Transcript for the first half."),
            TranscribedWord(duration / 2, duration, " Transcript for the second half."),
        ]


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

    workflow.transcriber.transcribe_words = fail
    with pytest.raises(RuntimeError, match="model failed"):
        next(stream)
    with store.sessions() as session:
        assert session.get(Meeting, first.meeting_id).status == "incomplete"
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 1


def test_empty_window_is_reported_without_aborting(workflow, source, store, caplog):
    caplog.set_level(logging.INFO)
    workflow.transcriber.transcribe_words = lambda audio: []
    assert list(workflow.transcribe(source)) == []
    with store.sessions() as session:
        assert session.scalar(select(Meeting.status)) == "completed"
        assert session.scalar(select(Meeting.recording_duration_seconds)) >= 3
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0
    assert "FFmpeg extraction: completed in" in caplog.text
    assert "streaming diarization: completed in" in caplog.text
    assert "window 1 [0.000-1.000s]: Whisper" in caplog.text
    assert "Whisper returned no words; no segment stored" in caplog.text
    assert "status=completed" in caplog.text


def test_ffmpeg_errors_are_visible(workflow, tmp_path, store):
    source = tmp_path / "2026-11-10_budget.mp4"
    source.write_bytes(b"not an mp4")
    with pytest.raises(RuntimeError, match="FFmpeg audio extraction failed"):
        list(workflow.transcribe(source))
    with store.sessions() as session:
        assert session.scalar(select(Meeting.status)) == "incomplete"
        assert session.scalar(select(Meeting.recording_duration_seconds)) is None


@pytest.mark.parametrize(
    "seconds", [0, -1, float("nan"), float("inf"), 0.5 / SAMPLE_RATE]
)
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


def test_directory_ingestion_processes_all_recordings(workflow, source, store, engine):
    from pipelines.transcription.inputs import discover_recordings
    from pipelines.transcription.pipeline import IngestionPipeline

    second = source.parent / "2026-11-11_budget.mp4"
    shutil.copyfile(source, second)

    def factory(staging_store):
        workflow.store = staging_store
        return workflow

    pipeline = IngestionPipeline(engine, factory)
    for recording in discover_recordings(source.parent):
        assert pipeline.ingest(recording)
    for recording in discover_recordings(source.parent):
        assert not pipeline.ingest(recording)
    with store.sessions() as session:
        meetings = session.scalars(select(Meeting).order_by(Meeting.meeting_date)).all()
        assert [meeting.filename for meeting in meetings] == [source.name, second.name]
        assert all(meeting.status == "completed" for meeting in meetings)
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 8


class MemoryStore:
    def __init__(self):
        self.rows = []
        self.status = "processing"

    def start(self, *args, **kwargs):
        return uuid4()

    def set_recording_duration(self, meeting_id, duration):
        self.duration = duration

    def append(self, meeting_id, **kwargs):
        row = SimpleNamespace(meeting_id=meeting_id, **kwargs)
        self.rows.append(row)
        return row

    def finish(self, meeting_id, *, completed):
        self.status = "completed" if completed else "incomplete"


def test_tiny_turn_gets_recording_context_not_a_17ms_whisper_call(source):
    store = MemoryStore()
    calls = []
    voice_lengths = []
    diarizer = FakeDiarizer()
    diarizer.diarize = lambda path: [
        SpeakerTurn(1, 2.231, "A"),
        SpeakerTurn(2.231, 2.248, "B"),
        SpeakerTurn(2.248, 3, "A"),
    ]
    transcriber = FakeTranscriber()

    def transcribe(audio):
        calls.append(len(audio))
        return [
            TranscribedWord(1, 2.24, "Transcript for the budget."),
            TranscribedWord(2.24, 3, " More discussion."),
        ]

    transcriber.transcribe_words = transcribe
    voice_embedder = FakeVoiceEmbedder()

    def embed(audio):
        voice_lengths.append(len(audio))
        return [0.4, 0.5]

    voice_embedder.embed = embed
    workflow = TranscriptionWorkflow(
        store, diarizer, transcriber, FakeTextEmbedder(), voice_embedder
    )
    rows = list(workflow.transcribe(source))
    assert workflow.max_chunk_frames is None
    assert calls == [round(store.duration * SAMPLE_RATE)]
    assert len(rows) == 1
    assert rows[0].speaker == "A"
    assert rows[0].start == 1
    assert rows[0].end == 3
    assert rows[0].transcript == "Transcript for the budget. More discussion."
    assert voice_lengths == [2 * SAMPLE_RATE]
    assert store.status == "completed"


def test_word_alignment_uses_total_overlap_and_retains_unknown():
    words = [
        TranscribedWord(0, 0.6, "One"),
        TranscribedWord(0.6, 1, " two."),
        TranscribedWord(1, 1.5, " Three."),
        TranscribedWord(1.5, 1.75, " Four."),
    ]
    turns = [
        SpeakerTurn(30, 30.3, "A"),
        SpeakerTurn(30.3, 30.6, "A"),
        SpeakerTurn(30.4, 31, "B"),
        SpeakerTurn(31, 31.5, "C"),
    ]
    aligned = list(align_words(words, turns, 30 * SAMPLE_RATE, 2 * SAMPLE_RATE))
    assert aligned == [
        (SpeakerTurn(30, 30.6, "A"), "One"),
        (SpeakerTurn(30.6, 31, "B"), "two."),
        (SpeakerTurn(31, 31.5, "C"), "Three."),
        (SpeakerTurn(31.5, 31.75, "UNKNOWN"), "Four."),
    ]


@pytest.mark.parametrize(
    "word",
    [
        TranscribedWord(-0.1, 1, "bad"),
        TranscribedWord(0, float("nan"), "bad"),
        TranscribedWord(1, 0, "bad"),
        TranscribedWord(0, 2, "bad"),
        TranscribedWord(0, 1, " "),
        TranscribedWord(0, 1, None),
    ],
)
def test_invalid_whisper_words_raise(word):
    with pytest.raises(ValueError, match="Whisper returned"):
        list(align_words([word], [], 0, SAMPLE_RATE))


def test_zero_duration_word_at_window_end_is_retained():
    assert list(
        align_words(
            [TranscribedWord(1, 1, "Yes.")],
            [SpeakerTurn(0, 1, "A")],
            0,
            SAMPLE_RATE,
        )
    ) == [(SpeakerTurn(1 - 1 / SAMPLE_RATE, 1, "A"), "Yes.")]


def test_short_recognized_word_keeps_its_short_speaker_turn():
    words = [
        TranscribedWord(0, 1.231, "One."),
        TranscribedWord(1.231, 1.248, " Yes."),
        TranscribedWord(1.248, 2, " Two."),
    ]
    turns = [
        SpeakerTurn(0, 1.231, "A"),
        SpeakerTurn(1.231, 1.248, "B"),
        SpeakerTurn(1.248, 2, "A"),
    ]
    assert list(align_words(words, turns, 0, 2 * SAMPLE_RATE)) == [
        (turns[0], "One."),
        (turns[1], "Yes."),
        (turns[2], "Two."),
    ]


def test_out_of_order_whisper_words_raise():
    with pytest.raises(ValueError, match="invalid word timestamps"):
        list(
            align_words(
                [TranscribedWord(0.5, 0.75, "One."), TranscribedWord(0, 0.5, " Two.")],
                [],
                0,
                SAMPLE_RATE,
            )
        )


def test_empty_window_then_speech_continues_and_commits(source, caplog):
    store = MemoryStore()
    transcriber = FakeTranscriber()
    calls = []

    def transcribe(audio):
        calls.append(len(audio))
        if len(calls) == 1:
            return []
        return [TranscribedWord(0, 1, "Transcript from another window.")]

    transcriber.transcribe_words = transcribe
    workflow = TranscriptionWorkflow(
        store,
        FakeDiarizer(),
        transcriber,
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
        max_chunk_seconds=1,
    )
    rows = list(workflow.transcribe(source))
    assert calls == [SAMPLE_RATE] * 3
    assert [row.sequence for row in rows] == [0, 1]
    assert [(row.start, row.end) for row in rows] == [(1, 2), (2, 3)]
    assert store.status == "completed"
    assert "Whisper returned no words" in caplog.text


@pytest.mark.parametrize(
    "chunk_seconds, expected_spans",
    [
        (None, [(0, 60), (60, 65)]),
        (45, [(0, 45), (45, 65)]),
        (90, [(0, 65)]),
        (30, [(0, 30), (30, 60), (60, 65)]),
    ],
)
def test_long_monologue_has_no_workflow_30_second_limit(
    source, monkeypatch, chunk_seconds, expected_spans
):
    def extract(_source, destination):
        with wave.open(str(destination), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(SAMPLE_RATE)
            audio.writeframes(bytes(65 * SAMPLE_RATE * 2))

    monkeypatch.setattr("pipelines.transcription.workflow.extract_audio", extract)
    store = MemoryStore()
    diarizer = FakeDiarizer()
    diarizer.diarize = lambda path: [SpeakerTurn(0, 65, "A")]
    transcriber = FakeTranscriber()
    calls = []

    def transcribe(audio):
        calls.append(len(audio))
        return [TranscribedWord(0, len(audio) / SAMPLE_RATE, "Transcript.")]

    transcriber.transcribe_words = transcribe
    workflow = TranscriptionWorkflow(
        store,
        diarizer,
        transcriber,
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
        max_chunk_seconds=chunk_seconds,
    )
    rows = list(workflow.transcribe(source))
    assert calls == [(end - start) * SAMPLE_RATE for start, end in expected_spans]
    assert [(row.start, row.end) for row in rows] == expected_spans
    assert [row.sequence for row in rows] == list(range(len(expected_spans)))
    assert all(row.speaker == "A" for row in rows)
    assert store.status == "completed"


def test_invalid_words_preserve_prior_commits_and_mark_incomplete(source):
    store = MemoryStore()
    transcriber = FakeTranscriber()
    calls = []

    def transcribe(audio):
        calls.append(len(audio))
        if len(calls) == 1:
            return [TranscribedWord(0, 1, "Transcript.")]
        return [TranscribedWord(0, 2, "Invalid timestamp.")]

    transcriber.transcribe_words = transcribe
    workflow = TranscriptionWorkflow(
        store,
        FakeDiarizer(),
        transcriber,
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
        max_chunk_seconds=1,
    )
    with pytest.raises(ValueError, match="invalid word timestamps"):
        list(workflow.transcribe(source))
    assert len(store.rows) == 1
    assert store.rows[0].sequence == 0
    assert store.status == "incomplete"


def test_unmatched_words_are_persisted_with_warning(source, caplog):
    store = MemoryStore()
    diarizer = FakeDiarizer()
    diarizer.diarize = lambda path: [SpeakerTurn(0, 0.1, "A")]
    transcriber = FakeTranscriber()
    transcriber.transcribe_words = lambda audio: [
        TranscribedWord(1, 2, "Transcript not covered by diarization.")
    ]
    workflow = TranscriptionWorkflow(
        store, diarizer, transcriber, FakeTextEmbedder(), FakeVoiceEmbedder()
    )
    rows = list(workflow.transcribe(source))
    assert rows[0].speaker == "UNKNOWN"
    assert "assigning UNKNOWN" in caplog.text
    assert store.status == "completed"


def test_early_close_cleans_up_delegated_audio_processing(source):
    store = MemoryStore()
    diarizer = FakeDiarizer()
    paths = []

    def diarize(path):
        paths.append(Path(path))
        return [SpeakerTurn(0, 3, "A")]

    diarizer.diarize = diarize
    workflow = TranscriptionWorkflow(
        store,
        diarizer,
        FakeTranscriber(),
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
        max_chunk_seconds=1,
    )
    stream = workflow.transcribe(source)
    first = next(stream)
    assert store.rows == [first]
    assert store.status == "processing"
    assert paths[0].is_file()
    stream.close()
    assert store.rows == [first]
    assert store.status == "incomplete"
    assert not paths[0].parent.exists()


def test_stream_chunk_validation_preserves_sorted_turns():
    workflow = TranscriptionWorkflow(
        MemoryStore(),
        FakeDiarizer(),
        FakeTranscriber(),
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
        max_chunk_seconds=1,
    )
    turns = [SpeakerTurn(0.5, 1, "B"), SpeakerTurn(0, 1, "A")]
    chunk = DiarizedChunk(0, SAMPLE_RATE, turns)
    workflow._validate_chunk(chunk, 0, SAMPLE_RATE, SAMPLE_RATE)
    assert chunk.turns == [SpeakerTurn(0, 1, "A"), SpeakerTurn(0.5, 1, "B")]


@pytest.mark.parametrize(
    "chunk",
    [
        DiarizedChunk(1, SAMPLE_RATE, []),
        DiarizedChunk(0, SAMPLE_RATE + 1, []),
        DiarizedChunk(0, 0, []),
        DiarizedChunk(0, SAMPLE_RATE, [SpeakerTurn(-1, 1, "A")]),
        DiarizedChunk(0, SAMPLE_RATE, [SpeakerTurn(0, 1, " ")]),
    ],
)
def test_stream_chunk_validation_rejects_invalid_bounds_and_turns(chunk):
    workflow = TranscriptionWorkflow(
        MemoryStore(),
        FakeDiarizer(),
        FakeTranscriber(),
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
    )
    with pytest.raises(ValueError):
        workflow._validate_chunk(chunk, 0, SAMPLE_RATE, SAMPLE_RATE)


def test_vimeo_audio_mp4_ingests_with_resolved_metadata(engine, source, tmp_path):
    from pipelines.transcription.pipeline import IngestionPipeline

    recording = tmp_path / "vimeo-1200176629-audio-low.mp4"
    shutil.copyfile(source, recording)
    pipeline = IngestionPipeline(
        engine,
        lambda store: TranscriptionWorkflow(
            store,
            FakeDiarizer(),
            FakeTranscriber(),
            FakeTextEmbedder(),
            FakeVoiceEmbedder(),
            max_chunk_seconds=1,
        ),
    )
    assert pipeline.ingest(
        recording, metadata_filename="2026-06-11_economic_development.mp4"
    )
    with pipeline.store.sessions() as session:
        meeting = session.get(Meeting, pipeline.store.meeting_id)
        assert meeting.status == "completed"
        assert meeting.filename == recording.name
        assert meeting.meeting_type == "economic development"
        assert meeting.meeting_date.isoformat() == "2026-06-11"
        assert meeting.recording_duration_seconds >= 3
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) > 0
