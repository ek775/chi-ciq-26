import logging
import os
import sys
from contextlib import closing

from dotenv import load_dotenv

from chi_ciq_transcription import (
    PyannoteDiarizer,
    PyannoteVoiceEmbedder,
    SentenceTextEmbedder,
    TranscriptionWorkflow,
    TranscriptStore,
    WhisperTranscriber,
    create_database,
)
from chi_ciq_transcription.progress import timed_operation


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_workflow(store: TranscriptStore, token: str) -> TranscriptionWorkflow:
    if not token.strip() or token == "your-hugging-face-token":
        raise ValueError("HF_TOKEN must be set for gated pyannote models")
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Inference device: {device}", flush=True)
    with timed_operation("Loading diarization model"):
        diarizer = PyannoteDiarizer(token, device=device)
    with timed_operation("Loading Whisper model"):
        transcriber = WhisperTranscriber(language="en", device=device)
    with timed_operation("Loading text embedding model"):
        text_embedder = SentenceTextEmbedder(device=device)
    with timed_operation("Loading voice embedding model"):
        voice_embedder = PyannoteVoiceEmbedder(token, device=device)
    return TranscriptionWorkflow(
        store, diarizer, transcriber, text_embedder, voice_embedder
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python examples/transcribe.py path/to/meeting.mp4")
    load_dotenv()
    configure_logging()
    engine = create_database(os.environ["DATABASE_URL"])
    try:
        workflow = build_workflow(TranscriptStore(engine), os.environ["HF_TOKEN"])
        with (
            timed_operation(f"Video {sys.argv[1]}"),
            closing(workflow.transcribe(sys.argv[1])) as segments,
        ):
            for segment in segments:
                print(
                    f"{segment.meeting_id} / {segment.speaker_id} "
                    f"[{segment.start_seconds:.2f}-{segment.end_seconds:.2f}] "
                    f"{segment.text}",
                    flush=True,
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
