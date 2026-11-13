import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

Audio = NDArray[np.float32]
SAMPLE_RATE = 16000
WHISPER_WINDOW_SECONDS = 30


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class TranscribedWord:
    """Whisper word text and offsets relative to the supplied audio window."""

    start: float
    end: float
    text: str


class Diarizer(Protocol):
    model_name: str

    def diarize(self, audio_path: str) -> Iterable[SpeakerTurn]: ...


class Transcriber(Protocol):
    model_name: str

    def transcribe_words(self, audio: Audio) -> list[TranscribedWord]: ...


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

    def transcribe_words(self, audio: Audio) -> list[TranscribedWord]:
        result = self.model.transcribe(
            audio,
            language=self.language,
            fp16=self.model.device.type == "cuda",
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        if not isinstance(result["text"], str):
            raise TypeError("Whisper returned a non-string transcript")
        words = [
            TranscribedWord(word["start"], word["end"], word["word"])
            for segment in result["segments"]
            for word in segment["words"]
        ]
        if result["text"].strip() and not words:
            raise ValueError("Whisper returned text without word timestamps")
        return words


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
        tokens = self.model.tokenizer(text, truncation=False)["input_ids"]
        if len(tokens) <= self.model.max_seq_length:
            return self.model.encode(text, normalize_embeddings=True).tolist()
        chunks: list[str] = []
        weights: list[int] = []
        current = ""
        special_tokens = self.model.tokenizer.num_special_tokens_to_add(pair=False)
        for match in re.finditer(r"\S+\s*", text):
            piece = match.group()
            candidate = current + piece
            size = len(self.model.tokenizer(candidate, truncation=False)["input_ids"])
            if size > self.model.max_seq_length:
                if not current:
                    raise ValueError("A word exceeds the embedding model context")
                chunks.append(current)
                weights.append(
                    len(self.model.tokenizer(current, truncation=False)["input_ids"])
                    - special_tokens
                )
                current = piece
                if (
                    len(self.model.tokenizer(current, truncation=False)["input_ids"])
                    > self.model.max_seq_length
                ):
                    raise ValueError("A word exceeds the embedding model context")
            else:
                current = candidate
        if current:
            chunks.append(current)
            weights.append(
                len(self.model.tokenizer(current, truncation=False)["input_ids"])
                - special_tokens
            )
        vectors = self.model.encode(chunks, normalize_embeddings=True)
        vector = np.average(vectors, axis=0, weights=weights)
        norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm == 0:
            raise ValueError("Long-text embedding must be finite and nonzero")
        return (vector / norm).tolist()


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
