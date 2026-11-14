"""Pure transformations from source rows into analysis units.

Meetings: consecutive diarized segments by the same speaker become one turn,
split at long pauses or a character budget so model inputs stay bounded.
Documents: each stored chunk is one unit; transcript timestamps are parsed
into offsets and stripped from the text sent to models.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

import numpy as np
from numpy.typing import NDArray

TIMESTAMP = re.compile(r"\[(\d{1,2}):([0-5]\d):([0-5]\d(?:\.\d+)?)\]")
DOCUMENT_TITLE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[ _]+(?P<body>.+?)(?:\s*\[[0-9A-Za-z]+\])?"
)


@dataclass(frozen=True)
class Segment:
    speaker: str
    start: float
    end: float
    text: str
    text_embedding: Sequence[float]
    voice_embedding: Sequence[float] | None = None


@dataclass(frozen=True)
class Unit:
    sequence: int
    text: str
    embedding: list[float]
    speaker: str | None = None
    start: float | None = None
    end: float | None = None

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class SpeakerSummary:
    label: str
    segment_count: int
    speaking_seconds: float
    centroid: list[float] | None


def normalized(vector: Sequence[float]) -> NDArray[np.float64]:
    array = np.asarray(vector, dtype=np.float64)
    norm = np.linalg.norm(array)
    if array.ndim != 1 or not np.isfinite(norm) or norm == 0:
        raise ValueError("Vectors must be one-dimensional, finite, and nonzero")
    return array / norm


def weighted_centroid(
    vectors: Sequence[Sequence[float]], weights: Sequence[float]
) -> list[float]:
    """Mean of unit-normalized vectors, renormalized, so scale cannot dominate."""
    if not vectors:
        raise ValueError("At least one vector is required")
    stacked = np.stack([normalized(vector) for vector in vectors])
    total = np.asarray(weights, dtype=np.float64)
    if total.shape != (len(vectors),) or (total < 0).any() or total.sum() <= 0:
        total = np.ones(len(vectors))
    return normalized(np.average(stacked, axis=0, weights=total)).tolist()


def build_turns(
    segments: Sequence[Segment],
    *,
    max_characters: int = 1500,
    max_gap_seconds: float = 30.0,
) -> list[Unit]:
    if max_characters <= 0 or max_gap_seconds < 0:
        raise ValueError("Turn limits must be positive")
    units: list[Unit] = []
    group: list[Segment] = []

    def flush() -> None:
        if not group:
            return
        units.append(
            Unit(
                sequence=len(units),
                speaker=group[0].speaker,
                start=group[0].start,
                end=max(segment.end for segment in group),
                text=" ".join(segment.text.strip() for segment in group),
                embedding=weighted_centroid(
                    [segment.text_embedding for segment in group],
                    [len(segment.text) for segment in group],
                ),
            )
        )
        group.clear()

    for segment in segments:
        if not segment.text.strip():
            continue
        if group:
            previous = group[-1]
            length = sum(len(item.text) + 1 for item in group) + len(segment.text)
            if (
                segment.speaker != previous.speaker
                or segment.start - previous.end > max_gap_seconds
                or length > max_characters
            ):
                flush()
        group.append(segment)
    flush()
    return units


def summarize_speakers(segments: Sequence[Segment]) -> list[SpeakerSummary]:
    grouped: dict[str, list[Segment]] = {}
    for segment in segments:
        grouped.setdefault(segment.speaker, []).append(segment)
    summaries = []
    for label, items in sorted(grouped.items()):
        voiced = [item for item in items if item.voice_embedding is not None]
        centroid = None
        if voiced:
            centroid = weighted_centroid(
                [item.voice_embedding for item in voiced],
                [item.end - item.start for item in voiced],
            )
        summaries.append(
            SpeakerSummary(
                label=label,
                segment_count=len(items),
                speaking_seconds=float(sum(item.end - item.start for item in items)),
                centroid=centroid,
            )
        )
    return summaries


def _seconds(match: re.Match[str]) -> float:
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def clean_document_chunk(text: str) -> tuple[str, float | None, float | None]:
    """Strip ``[HH:MM:SS.mmm]`` cues; return text and first/last cue offsets."""
    offsets = [_seconds(match) for match in TIMESTAMP.finditer(text)]
    cleaned = " ".join(TIMESTAMP.sub(" ", text).split())
    if not offsets:
        return cleaned, None, None
    return cleaned, offsets[0], max(offsets[0], offsets[-1])


def normalize_body(value: str) -> str:
    return " ".join(re.sub(r"[_]+", " ", value).split()).lower()


def parse_document_title(relative_path: str) -> tuple[date | None, str]:
    """Parse ``YYYY-MM-DD Committee name [ID].txt`` into a date and body."""
    path = PurePosixPath(relative_path)
    match = DOCUMENT_TITLE.fullmatch(path.stem)
    if match:
        try:
            return date.fromisoformat(match["date"]), normalize_body(match["body"])
        except ValueError:
            pass
    parent = path.parent.name if path.parent.name not in {"", "."} else "documents"
    return None, normalize_body(parent)
