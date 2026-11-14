import sys
import wave
from contextlib import closing
from pathlib import Path
from threading import Event, current_thread, get_ident
from types import SimpleNamespace

import numpy as np
import pytest
from test_workflow import (
    FakeTextEmbedder,
    FakeTranscriber,
    FakeVoiceEmbedder,
    MemoryStore,
)

from pipelines.transcription import (
    DiarizedChunk,
    SpeakerTurn,
    StreamingPyannoteDiarizer,
    TranscribedWord,
    TranscriptionWorkflow,
)
from pipelines.transcription.backends import SAMPLE_RATE
from pipelines.transcription.streaming import OnlineSpeakers


def test_online_speakers_track_permuted_local_slots_without_merging():
    speakers = OnlineSpeakers(0.35)
    assert speakers.assign({0: [1, 0, 0], 1: [0, 1, 0]}) == {
        0: "SPEAKER_00",
        1: "SPEAKER_01",
    }
    assert speakers.assign({0: [0, 1, 0], 1: [1, 0, 0]}) == {
        0: "SPEAKER_01",
        1: "SPEAKER_00",
    }
    assert speakers.assign({0: [0, 0, 1]}) == {0: "SPEAKER_02"}
    assert len(speakers.centroids) == 3
    assert speakers.assign({}) == {}
    assert speakers.assign({0: [-1, 0, 0]}) == {0: "SPEAKER_03"}


def test_online_speakers_exceed_previous_capacity_and_reuse_existing_labels():
    speakers = OnlineSpeakers(0.35)
    vectors = np.eye(128)
    for index, vector in enumerate(vectors):
        assert speakers.assign({0: vector.tolist()}) == {0: f"SPEAKER_{index:02d}"}
    assert len(speakers.centroids) == 128
    assert len(speakers.counts) == 128
    for index in (0, 31, 32, 100, 127):
        assert speakers.assign({0: vectors[index].tolist()}) == {
            0: f"SPEAKER_{index:02d}"
        }
        assert speakers.counts[index] == 2
    assert len(speakers.centroids) == 128


@pytest.mark.parametrize("vector", [[0, 0], [float("nan"), 1], [float("inf"), 1]])
def test_invalid_online_embeddings_raise(vector):
    with pytest.raises(ValueError, match="finite and nonzero"):
        OnlineSpeakers(0.35).assign({0: vector})


def test_workflow_rejects_offline_diarizer_before_starting_inference():
    with pytest.raises(TypeError, match="diarize_chunks"):
        TranscriptionWorkflow(
            MemoryStore(),
            SimpleNamespace(model_name="offline", diarize=lambda path: []),
            FakeTranscriber(),
            FakeTextEmbedder(),
            FakeVoiceEmbedder(),
        )


def test_simultaneous_speakers_cannot_claim_the_same_centroid():
    speakers = OnlineSpeakers(0.35)
    speakers.assign({0: [1, 0]})
    labels = speakers.assign({0: [1, 0.1], 1: [1, 0.2]})
    assert len(set(labels.values())) == 2


@pytest.fixture
def source(tmp_path, monkeypatch):
    source = tmp_path / "2026-11-10_budget.mp4"
    source.touch()

    def extract(_source, destination):
        with wave.open(str(destination), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(SAMPLE_RATE)
            audio.writeframes(bytes(10 * SAMPLE_RATE * 2))

    monkeypatch.setattr("pipelines.transcription.workflow.extract_audio", extract)
    return source


def test_continuous_overlap_and_backpressure_beyond_first_two_windows(source):
    owner = get_ident()
    entered = Event()
    release = Event()
    at_capacity = Event()
    produced = []
    workers = []
    closed = Event()
    paths = []

    class Diarizer:
        model_name = "streaming-fake"

        def diarize_chunks(self, path, chunk_frames):
            workers.append(current_thread())
            paths.append(Path(path))
            assert get_ident() != owner
            try:
                for index in range(10):
                    produced.append(index)
                    if index == 3:
                        at_capacity.set()
                    if index == 5:
                        assert entered.wait(5), (
                            "Transcription never overlapped later work"
                        )
                        release.set()
                    yield DiarizedChunk(
                        index * SAMPLE_RATE,
                        (index + 1) * SAMPLE_RATE,
                        [SpeakerTurn(index, index + 1, "A")],
                    )
            finally:
                closed.set()

    calls = []
    transcriber = FakeTranscriber()

    def transcribe(audio):
        assert get_ident() == owner
        calls.append(len(audio))
        if len(calls) == 1:
            assert at_capacity.wait(5)
            # One consumed + two queued + one waiting to enqueue. The producer
            # cannot ask its iterator for a fifth chunk until we consume again.
            assert produced == [0, 1, 2, 3]
        if len(calls) == 4:
            entered.set()
            assert release.wait(5), "Diarization did not progress during Whisper"
        return [TranscribedWord(0, 1, "Transcript.")]

    transcriber.transcribe_words = transcribe

    class OwnedStore(MemoryStore):
        def append(self, *args, **kwargs):
            assert get_ident() == owner
            return super().append(*args, **kwargs)

        def finish(self, *args, **kwargs):
            assert get_ident() == owner
            return super().finish(*args, **kwargs)

    store = OwnedStore()
    workflow = TranscriptionWorkflow(
        store,
        Diarizer(),
        transcriber,
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
        max_chunk_seconds=1,
    )
    rows = list(workflow.transcribe(source))
    assert calls == [SAMPLE_RATE] * 10
    assert [(row.start, row.end, row.sequence) for row in rows] == [
        (index, index + 1, index) for index in range(10)
    ]
    assert closed.is_set()
    assert not workers[0].is_alive()
    assert not paths[0].exists()
    assert store.status == "completed"


@pytest.mark.parametrize("failure", ["close", "whisper", "diarizer", "truncated"])
def test_failure_and_early_close_cancel_full_queue_and_join(source, failure):
    waiting = Event()
    closed = Event()
    workers = []

    class Diarizer:
        model_name = "failing-stream"

        def diarize_chunks(self, path, chunk_frames):
            workers.append(current_thread())
            try:
                for index in range(10):
                    if index == 3:
                        waiting.set()
                    if index == 4 and failure == "diarizer":
                        raise RuntimeError("diarizer failed")
                    if index == 4 and failure == "truncated":
                        return
                    yield DiarizedChunk(
                        index * SAMPLE_RATE,
                        (index + 1) * SAMPLE_RATE,
                        [SpeakerTurn(index, index + 1, "A")],
                    )
            finally:
                closed.set()

    store = MemoryStore()
    transcriber = FakeTranscriber()

    def transcribe(audio):
        assert waiting.wait(5)
        if failure == "whisper":
            raise RuntimeError("Whisper failed")
        return [TranscribedWord(0, 1, "Transcript.")]

    transcriber.transcribe_words = transcribe
    workflow = TranscriptionWorkflow(
        store,
        Diarizer(),
        transcriber,
        FakeTextEmbedder(),
        FakeVoiceEmbedder(),
        max_chunk_seconds=1,
    )
    with closing(workflow.transcribe(source)) as stream:
        if failure == "close":
            assert next(stream).sequence == 0
        else:
            error = ValueError if failure == "truncated" else RuntimeError
            with pytest.raises(error, match="failed|before audio EOF"):
                list(stream)
    assert closed.is_set()
    assert not workers[0].is_alive()
    assert store.status == "incomplete"
    if failure in {"diarizer", "truncated"}:
        assert [row.sequence for row in store.rows] == [0, 1, 2, 3]


@pytest.mark.parametrize("frame_count", [0, SAMPLE_RATE // 2, 13 * SAMPLE_RATE])
def test_incremental_inference_flushes_tail_and_resets_speaker_state(
    tmp_path, frame_count
):
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(bytes(frame_count * 2))
    # Real inference API is exercised separately; this isolates stream ownership,
    # overlapping context, finalization and per-recording state.
    diarizer = StreamingPyannoteDiarizer.__new__(StreamingPyannoteDiarizer)
    diarizer.window_frames = 10 * SAMPLE_RATE
    diarizer.hop_frames = 5 * SAMPLE_RATE
    diarizer.left_context = round(2.5 * SAMPLE_RATE)
    diarizer.distance_threshold = 0.35
    states = []

    def infer(audio, window_start, start, end, speakers, *, valid_end):
        assert len(audio) == 10 * SAMPLE_RATE
        assert window_start == start - round(2.5 * SAMPLE_RATE)
        assert 0 < valid_end <= len(audio)
        states.append(speakers)
        return [SpeakerTurn(start / SAMPLE_RATE, end / SAMPLE_RATE, "A")]

    diarizer._infer = infer
    for _ in range(2):
        chunks = list(diarizer.diarize_chunks(str(path), 6 * SAMPLE_RATE))
        assert sum(chunk.end - chunk.start for chunk in chunks) == frame_count
        assert [chunk.start for chunk in chunks] == list(
            range(0, frame_count, 6 * SAMPLE_RATE)
        )
        for chunk in chunks:
            assert chunk.end - chunk.start <= 6 * SAMPLE_RATE
            assert all(
                chunk.start / SAMPLE_RATE
                <= turn.start
                < turn.end
                <= chunk.end / SAMPLE_RATE
                for turn in chunk.turns
            )
    if frame_count:
        assert states[0] is not states[-1]


def test_segmentation_frames_are_clipped_and_local_slots_are_tracked(
    monkeypatch, caplog
):
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(from_numpy=lambda audio: audio)
    )
    diarizer = StreamingPyannoteDiarizer.__new__(StreamingPyannoteDiarizer)
    scores = np.zeros((1, 20, 2))
    scores[0, :10, 0] = 1
    scores[0, 10:, 1] = 1
    diarizer.segmentation = SimpleNamespace(
        infer=lambda tensor: scores,
        model=SimpleNamespace(
            receptive_field=SimpleNamespace(start=0, duration=0.1, step=0.1)
        ),
    )
    diarizer.embedding = SimpleNamespace(
        embed=lambda audio: [1, 0] if audio[0] < 0.15 else [0, 1]
    )
    audio = np.repeat(np.array([0.1, 0.2], dtype=np.float32), SAMPLE_RATE)
    speakers = OnlineSpeakers(0.35)
    expected = [
        SpeakerTurn(0, 1, "SPEAKER_00"),
        SpeakerTurn(1, 2, "SPEAKER_01"),
    ]
    assert (
        diarizer._infer(audio, 0, 0, 2 * SAMPLE_RATE, speakers, valid_end=len(audio))
        == expected
    )
    scores = scores[:, :, ::-1]
    assert (
        diarizer._infer(audio, 0, 0, 2 * SAMPLE_RATE, speakers, valid_end=len(audio))
        == expected
    )
    # Overlap without sufficient clean audio must not fabricate an identity.
    scores = np.ones((1, 20, 2))
    turns = diarizer._infer(
        audio,
        0,
        SAMPLE_RATE // 2,
        SAMPLE_RATE,
        speakers,
        valid_end=len(audio),
    )
    assert turns == [
        SpeakerTurn(0.5, 1, "UNKNOWN"),
        SpeakerTurn(0.5, 1, "UNKNOWN"),
    ]
    assert "insufficient clean speech" in caplog.text


@pytest.mark.parametrize(
    "scores",
    [
        np.empty((1, 0, 2)),
        np.zeros((2, 20, 2)),
        np.full((1, 20, 2), float("nan")),
        np.full((1, 20, 2), 2.0),
    ],
)
def test_invalid_segmentation_output_is_an_error(monkeypatch, scores):
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(from_numpy=lambda audio: audio)
    )
    diarizer = StreamingPyannoteDiarizer.__new__(StreamingPyannoteDiarizer)
    diarizer.segmentation = SimpleNamespace(infer=lambda tensor: scores)
    with pytest.raises(ValueError, match="segmentation"):
        diarizer._infer(
            np.zeros(2 * SAMPLE_RATE, dtype=np.float32),
            0,
            0,
            2 * SAMPLE_RATE,
            OnlineSpeakers(0.35),
            valid_end=2 * SAMPLE_RATE,
        )
