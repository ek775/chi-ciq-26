import logging

from . import (
    PyannoteVoiceEmbedder,
    SentenceTextEmbedder,
    StreamingPyannoteDiarizer,
    TranscriptionWorkflow,
    TranscriptStore,
    WhisperTranscriber,
)
from .progress import timed_operation


def build_workflow(store: TranscriptStore, token: str) -> TranscriptionWorkflow:
    if not token.strip() or token == "your-hugging-face-token":
        raise ValueError("HF_TOKEN must be set for gated pyannote models")
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logging.getLogger(__name__).info("Inference device: %s", device)
    with timed_operation("Loading diarization model"):
        diarizer = StreamingPyannoteDiarizer(token, device=device)
    with timed_operation("Loading Whisper model"):
        transcriber = WhisperTranscriber(language="en", device=device)
    with timed_operation("Loading text embedding model"):
        text_embedder = SentenceTextEmbedder(device=device)
    with timed_operation("Loading voice embedding model"):
        voice_embedder = PyannoteVoiceEmbedder(token, device=device)
    return TranscriptionWorkflow(
        store, diarizer, transcriber, text_embedder, voice_embedder
    )
