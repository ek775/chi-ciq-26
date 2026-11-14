"""Propagate manual speaker labels to other meetings by voice similarity.

A representative's voiceprint is the speaking-time-weighted centroid of the
speaker clusters a person has labeled. Unlabeled clusters inherit a label only
when the best match clears an absolute threshold and beats the runner-up by a
margin. These are inferred, unverified attributions and are stored as such.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import numpy as np

from .units import normalized, weighted_centroid


@dataclass(frozen=True)
class SpeakerVoice:
    speaker_id: UUID
    model: str
    centroid: Sequence[float]
    speaking_seconds: float


@dataclass(frozen=True)
class Voiceprint:
    representative_id: str
    model: str
    embedding: list[float]
    speaker_count: int
    speaking_seconds: float


@dataclass(frozen=True)
class VoiceMatch:
    speaker_id: UUID
    representative_id: str
    similarity: float
    margin: float


def build_voiceprints(
    voices: Sequence[SpeakerVoice], manual: dict[UUID, str]
) -> list[Voiceprint]:
    grouped: dict[tuple[str, str], list[SpeakerVoice]] = {}
    for voice in voices:
        representative_id = manual.get(voice.speaker_id)
        if representative_id is not None:
            grouped.setdefault((representative_id, voice.model), []).append(voice)
    prints = []
    for (representative_id, model), items in sorted(grouped.items()):
        dimensions = {len(item.centroid) for item in items}
        if len(dimensions) != 1:
            raise ValueError(f"Inconsistent voice dimensions for {representative_id}")
        prints.append(
            Voiceprint(
                representative_id=representative_id,
                model=model,
                embedding=weighted_centroid(
                    [item.centroid for item in items],
                    [max(item.speaking_seconds, 1e-6) for item in items],
                ),
                speaker_count=len(items),
                speaking_seconds=float(sum(item.speaking_seconds for item in items)),
            )
        )
    return prints


def match_voices(
    voices: Sequence[SpeakerVoice],
    voiceprints: Sequence[Voiceprint],
    *,
    exclude: set[UUID],
    threshold: float,
    margin: float,
) -> list[VoiceMatch]:
    if not -1 <= threshold <= 1 or margin < 0:
        raise ValueError("Threshold must be in [-1, 1] and margin nonnegative")
    by_model: dict[str, list[Voiceprint]] = {}
    for voiceprint in voiceprints:
        by_model.setdefault(voiceprint.model, []).append(voiceprint)
    matches = []
    for voice in voices:
        candidates = [
            item
            for item in by_model.get(voice.model, [])
            if len(item.embedding) == len(voice.centroid)
        ]
        if voice.speaker_id in exclude or not candidates:
            continue
        query = normalized(voice.centroid)
        similarities = np.array(
            [float(query @ normalized(item.embedding)) for item in candidates]
        )
        order = np.argsort(-similarities)
        best = similarities[order[0]]
        runner_up = similarities[order[1]] if len(order) > 1 else -1.0
        if best >= threshold and best - runner_up >= margin:
            matches.append(
                VoiceMatch(
                    speaker_id=voice.speaker_id,
                    representative_id=candidates[order[0]].representative_id,
                    similarity=float(best),
                    margin=float(best - runner_up),
                )
            )
    return matches
