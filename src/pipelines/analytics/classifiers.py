"""Sentiment and emotion classification with Hugging Face text classifiers.

``transformers`` is imported lazily so database and web code never load it.
Outputs are mapped onto fixed label sets so dashboard columns stay stable.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from databases.analytics.models import EMOTIONS, SENTIMENTS

DEFAULT_SENTIMENT_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
DEFAULT_EMOTION_MODEL = "j-hartmann/emotion-english-distilroberta-base"


class TextClassifier(Protocol):
    model_name: str

    def classify(self, texts: Sequence[str]) -> list[dict[str, float]]:
        """Return one label -> probability mapping per input text."""
        ...


@dataclass(frozen=True)
class Sentiment:
    label: str
    score: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class Emotion:
    label: str
    probabilities: dict[str, float]


def _distribution(scores: dict[str, float], labels: Sequence[str]) -> dict[str, float]:
    lowered = {key.lower(): float(value) for key, value in scores.items()}
    missing = set(labels) - lowered.keys()
    if missing:
        raise ValueError(
            f"Classifier output lacks labels {sorted(missing)}; got {sorted(lowered)}"
        )
    values = {label: min(max(lowered[label], 0.0), 1.0) for label in labels}
    total = sum(values.values())
    if total <= 0:
        raise ValueError("Classifier probabilities must not all be zero")
    return {label: value / total for label, value in values.items()}


def to_sentiment(scores: dict[str, float]) -> Sentiment:
    probabilities = _distribution(scores, SENTIMENTS)
    label = max(SENTIMENTS, key=lambda name: (probabilities[name], name))
    score = probabilities["positive"] - probabilities["negative"]
    return Sentiment(label, max(-1.0, min(1.0, score)), probabilities)


def to_emotion(scores: dict[str, float]) -> Emotion:
    probabilities = _distribution(scores, EMOTIONS)
    label = max(EMOTIONS, key=lambda name: (probabilities[name], name))
    return Emotion(label, probabilities)


class TransformersClassifier:
    def __init__(self, model_name: str, *, device: str = "cpu", batch_size: int = 32):
        from transformers import pipeline

        self.model_name = model_name
        self.batch_size = batch_size
        self.pipeline = pipeline(
            "text-classification", model=model_name, top_k=None, device=device
        )

    def classify(self, texts: Sequence[str]) -> list[dict[str, float]]:
        outputs = self.pipeline(
            list(texts),
            batch_size=self.batch_size,
            truncation=True,
            max_length=512,
        )
        return [{item["label"]: item["score"] for item in output} for output in outputs]
