import math
import re
from collections.abc import Sequence
from typing import Protocol

import numpy as np


class TextEmbedder(Protocol):
    model_name: str

    def embed(self, text: str) -> list[float]: ...


def validate_vector(values: Sequence[float]) -> list[float]:
    vector = [float(value) for value in values]
    if not vector or not all(math.isfinite(value) for value in vector):
        raise ValueError("Embeddings must be nonempty and contain only finite numbers")
    if not any(vector):
        raise ValueError("Embeddings must not be zero vectors")
    return vector


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
