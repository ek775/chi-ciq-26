from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

Audio = NDArray[np.float32]
SAMPLE_RATE = 16000


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


class Diarizer(Protocol):
    model_name: str

    def diarize(self, audio_path: str) -> Iterable[SpeakerTurn]: ...


class Transcriber(Protocol):
    model_name: str

    def transcribe(self, audio: Audio) -> str: ...


class TextEmbedder(Protocol):
    model_name: str

    def embed(self, text: str) -> list[float]: ...


class VoiceEmbedder(Protocol):
    model_name: str

    def embed(self, audio: Audio) -> list[float]: ...


class PyannoteDiarizer:
    def __init__(
        self,
        token: str,
        model_name: str = "pyannote/speaker-diarization-community-1",
        device: str = "cpu",
    ):
        import torch
        from pyannote.audio import Pipeline

        self.model_name = model_name
        self.pipeline = Pipeline.from_pretrained(model_name, token=token)
        if self.pipeline is None:
            raise RuntimeError(f"Unable to load diarization model: {model_name}")
        self.pipeline.to(torch.device(device))

    def diarize(self, audio_path: str) -> Iterable[SpeakerTurn]:
        output = self.pipeline(audio_path)
        turns = (
            SpeakerTurn(turn.start, turn.end, str(speaker))
            for turn, _, speaker in output.speaker_diarization.itertracks(
                yield_label=True
            )
        )
        return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))


class WhisperTranscriber:
    def __init__(
        self,
        model_name: str = "small",
        device: str = "cpu",
        language: str | None = None,
    ):
        import whisper

        self.model_name = model_name
        self.model = whisper.load_model(model_name, device=device)
        self.language = language

    def transcribe(self, audio: Audio) -> str:
        result = self.model.transcribe(
            audio,
            language=self.language,
            fp16=self.model.device.type == "cuda",
            condition_on_previous_text=False,
        )
        text = result["text"]
        if not isinstance(text, str):
            raise TypeError("Whisper returned a non-string transcript")
        return text.strip()


class SentenceTextEmbedder:
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        device: str = "cpu",
    ):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device=device)

    def embed(self, text: str) -> list[float]:
        # Fail rather than silently truncate the text represented by this vector.
        tokens = self.model.tokenizer(text, truncation=False)["input_ids"]
        if len(tokens) > self.model.max_seq_length:
            raise ValueError(
                "Text exceeds embedding model context; reduce max_chunk_seconds"
            )
        return self.model.encode(text, normalize_embeddings=True).tolist()


class PyannoteVoiceEmbedder:
    def __init__(
        self,
        token: str,
        model_name: str = "pyannote/wespeaker-voxceleb-resnet34-LM",
        device: str = "cpu",
    ):
        import torch
        from pyannote.audio import Inference, Model

        self.model_name = model_name
        model = Model.from_pretrained(model_name, token=token)
        if model is None:
            raise RuntimeError(f"Unable to load voice embedding model: {model_name}")
        self.inference = Inference(model, window="whole")
        self.inference.to(torch.device(device))

    def embed(self, audio: Audio) -> list[float]:
        import torch

        # Short turns need enough frames for the speaker model's convolution layers.
        padded = np.pad(audio, (0, max(0, SAMPLE_RATE - len(audio))))
        embedding = self.inference(
            {
                "waveform": torch.from_numpy(padded).unsqueeze(0),
                "sample_rate": SAMPLE_RATE,
            }
        )
        return np.asarray(embedding).reshape(-1).tolist()
