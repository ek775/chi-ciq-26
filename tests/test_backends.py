import sys
from types import SimpleNamespace

import numpy as np
import pytest

from pipelines.transcription import (
    PyannoteDiarizer,
    PyannoteVoiceEmbedder,
    SentenceTextEmbedder,
    TranscribedWord,
    WhisperTranscriber,
)


def test_whisper_receives_audio_and_cpu_options(monkeypatch):
    observed = {}

    def transcribe(audio, **kwargs):
        observed.update(kwargs)
        assert audio.shape == (16000,)
        return {"text": " Hello. "}

    model = SimpleNamespace(device=SimpleNamespace(type="cpu"), transcribe=transcribe)
    monkeypatch.setitem(
        sys.modules, "whisper", SimpleNamespace(load_model=lambda *a, **k: model)
    )
    transcriber = WhisperTranscriber(language="en")
    assert transcriber.transcribe(np.zeros(16000, dtype=np.float32)) == "Hello."
    assert observed == {
        "language": "en",
        "fp16": False,
        "condition_on_previous_text": False,
    }


@pytest.mark.parametrize("device", ["cpu", "cuda"])
@pytest.mark.parametrize("seconds", [1, 65])
def test_whisper_returns_word_timestamps(monkeypatch, device, seconds):
    audio = np.zeros(seconds * 16000, dtype=np.float32)
    start = seconds - 0.8
    end = seconds - 0.2

    def transcribe(audio, **kwargs):
        assert audio.shape == (seconds * 16000,)
        assert kwargs == {
            "language": "en",
            "fp16": device == "cuda",
            "condition_on_previous_text": False,
            "word_timestamps": True,
        }
        return {
            "text": " Hello.",
            "segments": [{"words": [{"start": start, "end": end, "word": " Hello."}]}],
        }

    model = SimpleNamespace(device=SimpleNamespace(type=device), transcribe=transcribe)
    monkeypatch.setitem(
        sys.modules, "whisper", SimpleNamespace(load_model=lambda *a, **k: model)
    )
    assert WhisperTranscriber(language="en").transcribe_words(audio) == [
        TranscribedWord(start, end, " Hello.")
    ]


def test_whisper_text_without_timestamps_is_an_error(monkeypatch):
    model = SimpleNamespace(
        device=SimpleNamespace(type="cpu"),
        transcribe=lambda *a, **k: {"text": "Hello", "segments": []},
    )
    monkeypatch.setitem(
        sys.modules, "whisper", SimpleNamespace(load_model=lambda *a, **k: model)
    )
    with pytest.raises(ValueError, match="without word timestamps"):
        WhisperTranscriber().transcribe_words(np.zeros(16000, dtype=np.float32))


def test_whisper_no_speech_returns_no_words(monkeypatch):
    model = SimpleNamespace(
        device=SimpleNamespace(type="cpu"),
        transcribe=lambda *a, **k: {"text": "", "segments": []},
    )
    monkeypatch.setitem(
        sys.modules, "whisper", SimpleNamespace(load_model=lambda *a, **k: model)
    )
    assert (
        WhisperTranscriber().transcribe_words(np.zeros(16000, dtype=np.float32)) == []
    )


def test_diarizer_uses_pyannote_four_output_and_orders_turns(monkeypatch):
    class Annotation:
        def itertracks(self, *, yield_label):
            assert yield_label
            yield SimpleNamespace(start=2, end=3), 0, "SPEAKER_01"
            yield SimpleNamespace(start=0, end=1), 0, "SPEAKER_00"

    class Pipeline:
        @classmethod
        def from_pretrained(cls, name, *, token):
            assert token == "test-token"
            return cls()

        def to(self, device):
            assert device == "cpu"

        def __call__(self, path):
            return SimpleNamespace(speaker_diarization=Annotation())

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(device=lambda d: d))
    monkeypatch.setitem(
        sys.modules, "pyannote.audio", SimpleNamespace(Pipeline=Pipeline)
    )
    turns = list(PyannoteDiarizer("test-token").diarize("audio.wav"))
    assert [turn.start for turn in turns] == [0, 2]
    assert turns[0].speaker == "SPEAKER_00"


def test_voice_embedding_uses_whole_window_and_pads_short_turns(monkeypatch):
    class Tensor:
        def __init__(self, audio):
            self.audio = audio

        def unsqueeze(self, dimension):
            assert dimension == 0
            return self.audio[np.newaxis, :]

    class Inference:
        def __init__(self, model, *, window):
            assert window == "whole"

        def to(self, device):
            assert device == "cpu"

        def __call__(self, audio):
            assert audio["sample_rate"] == 16000
            assert audio["waveform"].shape == (1, 16000)
            return np.array([[0.1, 0.2]], dtype=np.float32)

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(device=lambda d: d, from_numpy=Tensor),
    )
    monkeypatch.setitem(
        sys.modules,
        "pyannote.audio",
        SimpleNamespace(
            Model=SimpleNamespace(from_pretrained=lambda *a, **k: object()),
            Inference=Inference,
        ),
    )
    embedder = PyannoteVoiceEmbedder("test-token")
    np.testing.assert_allclose(
        embedder.embed(np.zeros(100, dtype=np.float32)), [0.1, 0.2]
    )


@pytest.mark.parametrize("too_long", [False, True])
def test_text_embedding_never_silently_truncates(monkeypatch, too_long):
    class Tokenizer:
        def __call__(self, text, *, truncation):
            assert not truncation
            return {"input_ids": [1] * (len(text.split()) + 2)}

        def num_special_tokens_to_add(self, *, pair):
            assert not pair
            return 2

    def encode(text, *, normalize_embeddings):
        assert normalize_embeddings
        if isinstance(text, list):
            assert text == ["One two ", "three four ", "five."]
            assert all(len(part.split()) <= 2 for part in text)
            return np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])
        return np.array([0.1, 0.2])

    model = SimpleNamespace(
        tokenizer=Tokenizer(),
        encode=encode,
        max_seq_length=4,
    )
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=lambda *a, **k: model),
    )
    embedder = SentenceTextEmbedder()
    if too_long:
        vector = embedder.embed("One two three four five.")
        np.testing.assert_allclose(vector, np.array([3, 2]) / np.sqrt(13))
        assert np.linalg.norm(vector) == pytest.approx(1)
    else:
        assert embedder.embed("Hello") == [0.1, 0.2]


@pytest.mark.parametrize("invalid_vector", [np.zeros((2, 2)), np.full((2, 2), np.nan)])
def test_long_embedding_rejects_invalid_pooled_vector(monkeypatch, invalid_vector):
    class Tokenizer:
        def __call__(self, text, *, truncation):
            return {"input_ids": [1] * (len(text.split()) + 2)}

        def num_special_tokens_to_add(self, *, pair):
            return 2

    model = SimpleNamespace(
        tokenizer=Tokenizer(),
        max_seq_length=4,
        encode=lambda *a, **k: invalid_vector,
    )
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=lambda *a, **k: model),
    )
    with pytest.raises(ValueError, match="finite and nonzero"):
        SentenceTextEmbedder().embed("One two three four")


def test_embedding_reports_a_single_word_exceeding_context(monkeypatch):
    class Tokenizer:
        def __call__(self, text, *, truncation):
            return {"input_ids": [1] * 10}

        def num_special_tokens_to_add(self, *, pair):
            return 2

    model = SimpleNamespace(tokenizer=Tokenizer(), max_seq_length=4)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=lambda *a, **k: model),
    )
    with pytest.raises(ValueError, match="word exceeds"):
        SentenceTextEmbedder().embed("Supercalifragilisticexpialidocious")
