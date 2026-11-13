import logging
import math
import wave
from collections.abc import Iterator

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import linear_sum_assignment

from .backends import (
    SAMPLE_RATE,
    Audio,
    DiarizedChunk,
    PyannoteVoiceEmbedder,
    SpeakerTurn,
)
from .workflow import read_audio

logger = logging.getLogger(__name__)


class OnlineSpeakers:
    """Bounded centroid state; published labels are never renumbered or revised."""

    def __init__(self, max_speakers: int, distance_threshold: float):
        self.max_speakers = max_speakers
        self.distance_threshold = distance_threshold
        self.centroids: list[NDArray[np.float64]] = []
        self.counts: list[int] = []

    def assign(self, embeddings: dict[int, list[float]]) -> dict[int, str]:
        if not embeddings:
            return {}
        slots = list(embeddings)
        vectors = np.asarray([embeddings[slot] for slot in slots], dtype=np.float64)
        norms = np.linalg.norm(vectors, axis=1)
        if (
            not np.isfinite(vectors).all()
            or not np.isfinite(norms).all()
            or np.any(norms == 0)
        ):
            raise ValueError("Streaming speaker embeddings must be finite and nonzero")
        vectors /= norms[:, None]
        assigned: dict[int, int] = {}
        if self.centroids:
            distances = 1 - vectors @ np.asarray(self.centroids).T
            # Dummy columns let any local speaker start a new cluster. One-to-one
            # assignment prevents simultaneous local speakers sharing an identity.
            costs = np.column_stack(
                [
                    distances,
                    np.full((len(slots), len(slots)), self.distance_threshold + 1e-6),
                ]
            )
            rows, columns = linear_sum_assignment(costs)
            for row, column in zip(rows, columns):
                if (
                    column < len(self.centroids)
                    and distances[row, column] <= self.distance_threshold
                ):
                    assigned[int(row)] = int(column)
        labels: dict[int, str] = {}
        for row, slot in enumerate(slots):
            vector = vectors[row]
            if row in assigned:
                identity = assigned[row]
                count = self.counts[identity]
                updated = self.centroids[identity] * count + vector
                norm = np.linalg.norm(updated)
                if norm == 0 or not math.isfinite(norm):
                    raise ValueError("Invalid streaming speaker centroid")
                self.centroids[identity] = updated / norm
                self.counts[identity] = count + 1
            else:
                if len(self.centroids) >= self.max_speakers:
                    raise RuntimeError(
                        "Streaming speaker capacity exhausted; increase max_speakers "
                        "or use separate Fuzzball-managed inference containers"
                    )
                identity = len(self.centroids)
                self.centroids.append(vector.copy())
                self.counts.append(1)
            labels[slot] = f"SPEAKER_{identity:02d}"
        return labels


class StreamingPyannoteDiarizer:
    """Local community-1 checkpoints with fixed-memory, online speaker clustering."""

    def __init__(
        self,
        token: str,
        model_name: str = "pyannote/speaker-diarization-community-1",
        device: str = "cpu",
        *,
        max_speakers: int = 32,
        distance_threshold: float = 0.35,
    ):
        if not isinstance(max_speakers, int) or max_speakers < 1:
            raise ValueError("max_speakers must be a positive integer")
        if not math.isfinite(distance_threshold) or not 0 < distance_threshold < 1:
            raise ValueError("distance_threshold must be finite and between 0 and 1")
        import torch
        from pyannote.audio import Inference, Model

        model = Model.from_pretrained(model_name, subfolder="segmentation", token=token)
        if model is None:
            raise RuntimeError(f"Unable to load segmentation model: {model_name}")
        self.segmentation = Inference(model, device=torch.device(device), batch_size=1)
        self.embedding = PyannoteVoiceEmbedder(
            token, model_name=model_name, device=device, subfolder="embedding"
        )
        self.model_name = f"{model_name}:online-centroid-v1"
        self.window_frames = round(self.segmentation.duration * SAMPLE_RATE)
        if self.window_frames < 4:
            raise ValueError(
                "Segmentation model context must contain at least 4 samples"
            )
        self.hop_frames = self.window_frames // 2
        self.left_context = (self.window_frames - self.hop_frames) // 2
        self.max_speakers = max_speakers
        self.distance_threshold = distance_threshold

    def diarize_chunks(
        self, audio_path: str, chunk_frames: int
    ) -> Iterator[DiarizedChunk]:
        if not isinstance(chunk_frames, int) or chunk_frames < 1:
            raise ValueError("chunk_frames must be a positive integer")
        speakers = OnlineSpeakers(self.max_speakers, self.distance_threshold)
        with wave.open(audio_path, "rb") as audio_file:
            if (
                audio_file.getframerate() != SAMPLE_RATE
                or audio_file.getnchannels() != 1
                or audio_file.getsampwidth() != 2
            ):
                raise ValueError("Expected mono, 16 kHz, signed 16-bit PCM audio")
            frame_count = audio_file.getnframes()
            next_start = 0
            pending: list[SpeakerTurn] = []
            for start in range(0, frame_count, self.hop_frames):
                end = min(start + self.hop_frames, frame_count)
                window_start = start - self.left_context
                read_start = max(0, window_start)
                read_end = min(frame_count, window_start + self.window_frames)
                audio = read_audio(audio_file, read_start, read_end)
                audio = np.pad(
                    audio,
                    (
                        read_start - window_start,
                        window_start + self.window_frames - read_end,
                    ),
                )
                turns = self._infer(
                    audio,
                    window_start,
                    start,
                    end,
                    speakers,
                    valid_end=read_end - window_start,
                )
                pending.extend(turns)
                while min(next_start + chunk_frames, frame_count) <= end:
                    chunk_end = min(next_start + chunk_frames, frame_count)
                    committed = [
                        SpeakerTurn(
                            max(turn.start, next_start / SAMPLE_RATE),
                            min(turn.end, chunk_end / SAMPLE_RATE),
                            turn.speaker,
                        )
                        for turn in pending
                        if turn.start < chunk_end / SAMPLE_RATE
                        and turn.end > next_start / SAMPLE_RATE
                    ]
                    yield DiarizedChunk(next_start, chunk_end, committed)
                    pending = [
                        turn for turn in pending if turn.end > chunk_end / SAMPLE_RATE
                    ]
                    next_start = chunk_end
                    if next_start == frame_count:
                        break

    def _infer(
        self,
        audio: Audio,
        window_start: int,
        start: int,
        end: int,
        speakers: OnlineSpeakers,
        *,
        valid_end: int,
    ) -> list[SpeakerTurn]:
        import torch

        output = self.segmentation.infer(torch.from_numpy(audio)[None, None, :])
        if (
            not isinstance(output, np.ndarray)
            or output.ndim != 3
            or output.shape[0] != 1
            or output.shape[1] == 0
            or output.shape[2] == 0
        ):
            raise ValueError("Expected one frame-level segmentation output")
        scores = output[0]
        if not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
            raise ValueError("Invalid segmentation probabilities")
        frames = self.segmentation.model.receptive_field
        centers = (
            frames.start + frames.duration / 2 + np.arange(len(scores)) * frames.step
        )
        offsets = np.clip(
            np.rint(centers * SAMPLE_RATE).astype(np.int64), 0, len(audio)
        )
        # Midpoints partition the audio without overlaps or gaps between frames.
        edges = np.r_[0, (offsets[:-1] + offsets[1:]) // 2, len(audio)]
        active = scores >= 0.5
        clean = active & (active.sum(axis=1) == 1)[:, None]
        embeddings: dict[int, list[float]] = {}
        for slot in range(scores.shape[1]):
            pieces = [
                audio[edges[index] : edges[index + 1]]
                for index in np.flatnonzero(clean[:, slot])
                if edges[index + 1] > edges[index]
                and edges[index] + window_start >= 0
                and edges[index + 1] <= valid_end
            ]
            if pieces:
                speech = np.concatenate(pieces)
                if len(speech) >= SAMPLE_RATE:
                    embeddings[slot] = self.embedding.embed(speech)
        labels = speakers.assign(embeddings)
        turns = []
        for slot in range(scores.shape[1]):
            label = labels.get(slot, "UNKNOWN")
            mask = active[:, slot]
            boundaries = np.flatnonzero(np.diff(np.r_[False, mask, False]))
            for first, last in zip(boundaries[::2], boundaries[1::2]):
                turn_start = max(start, window_start + int(edges[first]))
                turn_end = min(end, window_start + int(edges[last]))
                if turn_end > turn_start:
                    if label == "UNKNOWN":
                        logger.warning(
                            "Streaming diarization: insufficient clean speech to "
                            "identify speaker at %.3f-%.3fs",
                            turn_start / SAMPLE_RATE,
                            turn_end / SAMPLE_RATE,
                        )
                    turns.append(
                        SpeakerTurn(
                            turn_start / SAMPLE_RATE, turn_end / SAMPLE_RATE, label
                        )
                    )
        return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
