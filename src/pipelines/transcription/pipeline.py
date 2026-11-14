import argparse
import hashlib
import logging
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import Engine, delete, select, text

from databases.meetings.metadata import MeetingMetadata, parse_filename
from databases.meetings.models import Meeting, PublishedSource
from databases.meetings.schema import check_schema, connect_database
from databases.meetings.store import TranscriptStore
from shared.logging import configure_logging

from .inputs import (
    content_digest,
    discover_recordings,
    load_metadata_file,
    meeting_filename,
    validate_meeting_filename,
)
from .runtime import build_workflow
from .workflow import TranscriptionWorkflow

logger = logging.getLogger(__name__)


@contextmanager
def source_lock(engine: Engine, key: str) -> Iterator[None]:
    lock_id = int.from_bytes(
        hashlib.sha256(key.encode()).digest()[:8], "big", signed=True
    )
    # A dedicated connection retains the session lock across per-segment commits.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_id})
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id}
            )


class StagingStore(TranscriptStore):
    def __init__(self, engine: Engine):
        super().__init__(engine)
        self.source: Path | None = None
        self.meeting_id: UUID | None = None

    def start(
        self,
        source: Path,
        metadata: MeetingMetadata,
        *,
        transcription_model: str,
        diarization_model: str,
        text_embedding_model: str,
        voice_embedding_model: str,
    ) -> UUID:
        if self.source is None:
            raise RuntimeError("Staging source must be set before transcription")
        if self.meeting_id is not None:
            raise RuntimeError("Only one run can be staged per input")
        self.meeting_id = super().start(
            self.source,
            replace(metadata, filename=self.source.name),
            transcription_model=transcription_model,
            diarization_model=diarization_model,
            text_embedding_model=text_embedding_model,
            voice_embedding_model=voice_embedding_model,
        )
        return self.meeting_id

    def is_unchanged(self, key: str, digest: str, metadata: MeetingMetadata) -> bool:
        with self.sessions() as session:
            source = session.get(PublishedSource, key)
            if source is None or source.content_sha256 != digest:
                return False
            meeting = session.get(Meeting, source.meeting_id)
            return (
                meeting is not None
                and meeting.meeting_date == metadata.meeting_date
                and meeting.meeting_type == metadata.meeting_type
            )

    def publish(self, key: str, digest: str) -> None:
        if self.meeting_id is None:
            raise RuntimeError("Transcription did not create a staged meeting")
        with self.sessions.begin() as session:
            meeting = session.get(Meeting, self.meeting_id)
            if meeting is None or meeting.status != "completed":
                raise RuntimeError("Only a completed transcription may be published")
            source = session.scalar(
                select(PublishedSource)
                .where(PublishedSource.key == key)
                .with_for_update()
            )
            previous = None
            if source is None:
                session.add(
                    PublishedSource(
                        key=key, content_sha256=digest, meeting_id=self.meeting_id
                    )
                )
            else:
                previous = source.meeting_id
                source.content_sha256 = digest
                source.meeting_id = self.meeting_id
            session.flush()
            if previous is not None and previous != self.meeting_id:
                session.execute(delete(Meeting).where(Meeting.id == previous))


class IngestionPipeline:
    def __init__(
        self,
        engine: Engine,
        factory: Callable[[TranscriptStore], TranscriptionWorkflow],
    ):
        self.engine = engine
        self.store = StagingStore(engine)
        self.factory = factory
        self.workflow: TranscriptionWorkflow | None = None

    def ingest(
        self,
        source: Path,
        *,
        namespace: str = "recordings",
        force: bool = False,
        expected_sha256: str | None = None,
        metadata_filename: str | None = None,
    ) -> bool:
        if not namespace.strip():
            raise ValueError("Source namespace must not be empty")
        if source.suffix.lower() != ".mp4":
            raise ValueError(f"Expected an MP4 file: {source.name}")
        filename = validate_meeting_filename(
            source.name if metadata_filename is None else metadata_filename
        )
        metadata = parse_filename(filename)
        key = f"{namespace}/{source.name}"
        with source_lock(self.engine, key):
            digest = content_digest(source)
            if expected_sha256 is not None and digest != expected_sha256:
                raise ValueError(f"Input content changed since dispatch: {source.name}")
            if not force and self.store.is_unchanged(key, digest, metadata):
                logger.info("Unchanged, skipping %s", key)
                return False
            with TemporaryDirectory(prefix="chi-ciq-ingest-") as directory:
                snapshot = Path(directory) / filename
                shutil.copyfile(source, snapshot)
                digest = content_digest(snapshot)
                if expected_sha256 is not None and digest != expected_sha256:
                    raise ValueError(
                        f"Input content changed while staging: {source.name}"
                    )
                if not force and self.store.is_unchanged(key, digest, metadata):
                    logger.info("Unchanged snapshot, skipping %s", key)
                    return False
                self.store.source = source
                self.store.meeting_id = None
                if self.workflow is None:
                    self.workflow = self.factory(self.store)
                if self.workflow.store is not self.store:
                    raise RuntimeError("Ingestion workflow must use its staging store")
                with closing(self.workflow.transcribe(snapshot)) as segments:
                    for _ in segments:
                        pass
                self.store.publish(key, digest)
                logger.info("Published %s as meeting %s", key, self.store.meeting_id)
                return True


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Ingest or atomically update MP4 inputs"
    )
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument(
        "--input-dir",
        type=Path,
    )
    inputs.add_argument(
        "--input-file", type=Path, help="Process only this Fuzzball-assigned recording"
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        help="JSON mapping Vimeo IDs to dated MP4 filenames",
    )
    parser.add_argument(
        "--meeting-filename",
        help="Dated meeting filename for a single --input-file",
    )
    parser.add_argument(
        "--expected-sha256", help="Reject a recording changed since job creation"
    )
    parser.add_argument("--namespace", default="recordings")
    parser.add_argument(
        "--force", action="store_true", help="Reprocess unchanged inputs"
    )
    args = parser.parse_args(argv)
    if args.meeting_filename is not None and args.input_file is None:
        parser.error("--meeting-filename requires --input-file")
    if args.meeting_filename is not None and args.metadata_file is not None:
        parser.error("--meeting-filename cannot be combined with --metadata-file")
    if args.expected_sha256 is not None:
        if args.input_file is None:
            parser.error("--expected-sha256 requires --input-file")
        if len(args.expected_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in args.expected_sha256
        ):
            parser.error("--expected-sha256 must be 64 lowercase hexadecimal digits")
    configure_logging("transcription")
    metadata = load_metadata_file(args.metadata_file)
    if args.input_file is not None:
        if args.input_file.suffix.lower() != ".mp4":
            parser.error("--input-file must be an MP4 file")
        filename = (
            validate_meeting_filename(args.meeting_filename)
            if args.meeting_filename is not None
            else meeting_filename(args.input_file, metadata)
        )
        if not args.input_file.is_file():
            raise FileNotFoundError(args.input_file)
        recordings = [args.input_file]
    else:
        recordings = discover_recordings(
            args.input_dir
            or Path(os.environ.get("TRANSCRIPTION_INPUT_DIR", "data/mp4")),
            metadata,
        )
    engine = connect_database(os.environ["DATABASE_URL"])
    try:
        check_schema(engine)
        pipeline = IngestionPipeline(
            engine, lambda store: build_workflow(store, os.environ["HF_TOKEN"])
        )
        for recording in recordings:
            pipeline.ingest(
                recording,
                namespace=args.namespace,
                force=args.force,
                expected_sha256=args.expected_sha256,
                metadata_filename=(
                    filename
                    if args.input_file is not None
                    else meeting_filename(recording, metadata)
                ),
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
