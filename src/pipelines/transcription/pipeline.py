import argparse
import hashlib
import logging
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import Engine, delete, select, text

from databases.meetings.metadata import MeetingMetadata, parse_filename
from databases.meetings.models import Meeting, PublishedSource
from databases.meetings.schema import check_schema, connect_database
from databases.meetings.store import TranscriptStore

from .runtime import build_workflow, configure_logging
from .workflow import TranscriptionWorkflow

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
    for recording in recordings:
        parse_filename(recording)
    return recordings


def content_digest(source: Path) -> str:
    with source.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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
            metadata,
            transcription_model=transcription_model,
            diarization_model=diarization_model,
            text_embedding_model=text_embedding_model,
            voice_embedding_model=voice_embedding_model,
        )
        return self.meeting_id

    def is_unchanged(self, key: str, digest: str) -> bool:
        with self.sessions() as session:
            source = session.get(PublishedSource, key)
            return source is not None and source.content_sha256 == digest

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
        self, source: Path, *, namespace: str = "recordings", force=False
    ) -> bool:
        if not namespace.strip():
            raise ValueError("Source namespace must not be empty")
        parse_filename(source)
        key = f"{namespace}/{source.name}"
        with source_lock(self.engine, key):
            digest = content_digest(source)
            if not force and self.store.is_unchanged(key, digest):
                logger.info("Unchanged, skipping %s", key)
                return False
            with TemporaryDirectory(prefix="chi-ciq-ingest-") as directory:
                snapshot = Path(directory) / source.name
                shutil.copyfile(source, snapshot)
                digest = content_digest(snapshot)
                if not force and self.store.is_unchanged(key, digest):
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


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Ingest or atomically update MP4 inputs"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(os.environ.get("TRANSCRIPTION_INPUT_DIR", "data/mp4")),
    )
    parser.add_argument("--namespace", default="recordings")
    parser.add_argument(
        "--force", action="store_true", help="Reprocess unchanged inputs"
    )
    args = parser.parse_args()
    configure_logging()
    recordings = discover_recordings(args.input_dir)
    engine = connect_database(os.environ["DATABASE_URL"])
    try:
        check_schema(engine)
        pipeline = IngestionPipeline(
            engine, lambda store: build_workflow(store, os.environ["HF_TOKEN"])
        )
        for recording in recordings:
            pipeline.ingest(recording, namespace=args.namespace, force=args.force)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
