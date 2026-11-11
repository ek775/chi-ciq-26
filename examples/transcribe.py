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


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python examples/transcribe.py path/to/meeting.mp4")
    load_dotenv()
    engine = create_database(os.environ["DATABASE_URL"])
    try:
        token = os.environ["HF_TOKEN"]
        if not token.strip():
            raise ValueError("HF_TOKEN must be set for gated pyannote models")
        workflow = TranscriptionWorkflow(
            store=TranscriptStore(engine),
            diarizer=PyannoteDiarizer(token),
            transcriber=WhisperTranscriber(language="en"),
            text_embedder=SentenceTextEmbedder(),
            voice_embedder=PyannoteVoiceEmbedder(token),
        )
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
