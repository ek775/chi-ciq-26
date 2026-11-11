import argparse
import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

from chi_ciq_transcription import (
    TranscriptionWorkflow,
    TranscriptStore,
    create_database,
    parse_filename,
)
from chi_ciq_transcription.progress import timed_operation

from .transcribe import build_workflow, configure_logging

logger = logging.getLogger(__name__)


def discover_recordings(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise NotADirectoryError(directory)
    recordings = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp4"
    )
    if not recordings:
        raise ValueError(f"No MP4 files found in {directory}")
    # Validate the whole batch before committing any meeting rows.
    for recording in recordings:
        parse_filename(recording)
    return recordings


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


def export_database(database_url: str, output_directory: Path) -> Path:
    """Write a custom-format pg_dump archive, publishing it only after success."""
    executable = shutil.which("pg_dump")
    if executable is None:
        raise FileNotFoundError(
            "pg_dump is required; install the PostgreSQL client tools"
        )
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Database export requires a PostgreSQL URL")
    environment = os.environ.copy()
    if url.password is not None:
        environment["PGPASSWORD"] = url.password
    connection_url = URL.create(
        "postgresql",
        username=url.username,
        host=url.host,
        port=url.port,
        database=url.database,
        query=url.query,
    ).render_as_string(hide_password=False)
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = output_directory / f"transcription-{timestamp}-{uuid4().hex}.dump"
    with NamedTemporaryFile(
        dir=output_directory, prefix=".transcription-", suffix=".partial", delete=False
    ) as archive:
        temporary = Path(archive.name)
        try:
            result = subprocess.run(
                [
                    executable,
                    "--dbname",
                    connection_url,
                    "--no-password",
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                ],
                stdout=archive,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"pg_dump failed: {result.stderr.strip()}")
            archive.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


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
