import os
import sys
from contextlib import closing

from dotenv import load_dotenv

from pipelines.transcription import TranscriptStore, create_database
from pipelines.transcription.progress import timed_operation
from pipelines.transcription.runtime import build_workflow, configure_logging


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
