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


def build_workflow(store: TranscriptStore, token: str) -> TranscriptionWorkflow:
    if not token.strip() or token == "your-hugging-face-token":
        raise ValueError("HF_TOKEN must be set for gated pyannote models")
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Inference device: {device}", flush=True)
    return TranscriptionWorkflow(
        store=store,
        diarizer=PyannoteDiarizer(token, device=device),
        transcriber=WhisperTranscriber(language="en", device=device),
        text_embedder=SentenceTextEmbedder(device=device),
        voice_embedder=PyannoteVoiceEmbedder(token, device=device),
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python examples/transcribe.py path/to/meeting.mp4")
    load_dotenv()
    engine = create_database(os.environ["DATABASE_URL"])
    try:
        workflow = build_workflow(TranscriptStore(engine), os.environ["HF_TOKEN"])
        with closing(workflow.transcribe(sys.argv[1])) as segments:
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
