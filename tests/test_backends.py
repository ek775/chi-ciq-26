import sys
from types import SimpleNamespace

import numpy as np
import pytest

from chi_ciq_transcription import (
    PyannoteDiarizer,
    PyannoteVoiceEmbedder,
    SentenceTextEmbedder,
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
    def tokenize(text, *, truncation):
        assert not truncation
        return {"input_ids": [1] * (5 if too_long else 3)}

    def encode(text, *, normalize_embeddings):
        assert normalize_embeddings
        return np.array([0.1, 0.2])

    model = SimpleNamespace(
        tokenizer=tokenize,
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
        with pytest.raises(ValueError, match="context"):
            embedder.embed("Hello")
    else:
        assert embedder.embed("Hello") == [0.1, 0.2]
