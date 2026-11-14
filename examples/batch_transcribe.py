import argparse
import logging
import os
import shutil
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv

from databases.meetings.archive import export_database
from pipelines.transcription import (
    TranscriptionWorkflow,
    TranscriptStore,
    create_database,
)
from pipelines.transcription.pipeline import discover_recordings
from pipelines.transcription.progress import timed_operation

from .transcribe import build_workflow, configure_logging

logger = logging.getLogger(__name__)


def transcribe_recordings(
    workflow: TranscriptionWorkflow, recordings: Sequence[Path]
) -> None:
    for index, recording in enumerate(recordings, start=1):
        logger.info("Video %d/%d: %s", index, len(recordings), recording.name)
        count = 0
        with (
            timed_operation(f"Video {recording.name}"),
            closing(workflow.transcribe(recording)) as segments,
        ):
            for segment in segments:
                count += 1
                print(
                    f"  {segment.meeting_id} / {segment.speaker_id} "
                    f"[{segment.start_seconds:.2f}-{segment.end_seconds:.2f}]",
                    flush=True,
                )
        print(f"Completed {recording.name}: {count} segments", flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Transcribe a directory of MP4 files and export the PostgreSQL database."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(os.environ.get("TRANSCRIPTION_INPUT_DIR", "data/mp4")),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("TRANSCRIPTION_OUTPUT_DIR", "data/database")),
    )
    args = parser.parse_args(argv)
    configure_logging()
    recordings = discover_recordings(args.input_dir)
    logger.info("Found %d MP4 files in %s", len(recordings), args.input_dir)
    if shutil.which("pg_dump") is None:
        raise FileNotFoundError(
            "pg_dump is required; install the PostgreSQL client tools"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Check permissions before starting costly model inference.
    with NamedTemporaryFile(dir=args.output_dir):
        pass
    database_url = os.environ["DATABASE_URL"]
    engine = create_database(database_url)
    try:
        with timed_operation("Batch (model loading, videos, and database export)"):
            workflow = build_workflow(TranscriptStore(engine), os.environ["HF_TOKEN"])
            transcribe_recordings(workflow, recordings)
            with timed_operation("Database export"):
                archive = export_database(database_url, args.output_dir)
            print(f"Database archive: {archive}", flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
