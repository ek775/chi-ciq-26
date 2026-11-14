import hashlib
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from flyway_migrations import apply_migrations
from sqlalchemy import func, select, text

from databases.meetings.models import PublishedSource
from databases.meetings.schema import check_schema
from pipelines.transcription import Meeting, TranscriptSegment, parse_filename
from pipelines.transcription.pipeline import (
    IngestionPipeline,
    content_digest,
    source_lock,
)


class FakeWorkflow:
    def __init__(self, store, *, fail=False, during=None):
        self.store = store
        self.fail = fail
        self.during = during

    def transcribe(self, path):
        meeting_id = self.store.start(
            path,
            parse_filename(path),
            transcription_model="fake",
            diarization_model="fake",
            text_embedding_model="fake",
            voice_embedding_model="fake",
        )
        completed = False
        try:
            self.store.set_recording_duration(meeting_id, 10)
            yield self.store.append(
                meeting_id,
                sequence=0,
                speaker="SPEAKER_00",
                start=1,
                end=2,
                transcript=path.read_text(),
                text_embedding=[1, 2, 3],
                voice_embedding=[1, 2],
            )
            if self.during:
                self.during(path)
            if self.fail:
                raise RuntimeError("inference failed")
            completed = True
        finally:
            self.store.finish(meeting_id, completed=completed)


@pytest.fixture
def recording(tmp_path):
    path = tmp_path / "2026-11-10_budget.mp4"
    path.write_text("Budget 100% <script>not executable</script>")
    return path


def published(engine):
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        return session.scalars(select(PublishedSource)).all()


def test_ingestion_skips_unchanged_without_loading_models(engine, recording):
    builds = []

    def factory(store):
        builds.append(True)
        return FakeWorkflow(store)

    pipeline = IngestionPipeline(engine, factory)
    assert pipeline.ingest(recording)
    first = published(engine)[0]
    assert first.content_sha256 == content_digest(recording)
    assert first.key == f"recordings/{recording.name}"
    assert not pipeline.ingest(recording)
    # A fresh process should also skip without loading a model.
    other = IngestionPipeline(
        engine, lambda store: pytest.fail("unchanged input loaded models")
    )
    assert not other.ingest(recording)
    assert builds == [True]
    assert published(engine)[0].meeting_id == first.meeting_id
    with pipeline.store.sessions() as session:
        assert session.get(Meeting, first.meeting_id).source_path == str(
            recording.resolve()
        )


def test_vimeo_metadata_change_republishes_with_original_source_identity(
    engine, tmp_path
):
    recording = tmp_path / "vimeo-1200176629-audio-low.mp4"
    recording.write_text("audio")
    pipeline = IngestionPipeline(engine, FakeWorkflow)
    metadata = "2026-06-11_economic_development.mp4"
    assert pipeline.ingest(recording, metadata_filename=metadata)
    first = published(engine)[0].meeting_id
    assert not pipeline.ingest(recording, metadata_filename=metadata)
    assert pipeline.ingest(recording, metadata_filename="2026-06-12_budget.mp4")
    current = published(engine)[0]
    assert current.key == f"recordings/{recording.name}"
    assert current.meeting_id != first
    with pipeline.store.sessions() as session:
        meeting = session.get(Meeting, current.meeting_id)
        assert meeting.filename == recording.name
        assert meeting.meeting_date.isoformat() == "2026-06-12"
        assert meeting.meeting_type == "budget"
        assert meeting.source_path == str(recording.resolve())


def test_changed_input_is_atomically_published_and_old_rows_deleted(engine, recording):
    pipeline = IngestionPipeline(engine, FakeWorkflow)
    pipeline.ingest(recording)
    old = published(engine)[0].meeting_id
    recording.write_text("Updated budget")

    def during(path):
        assert published(engine)[0].meeting_id == old
        with pipeline.store.sessions() as session:
            assert session.get(Meeting, old).status == "completed"

    pipeline.workflow.during = during
    assert pipeline.ingest(recording)
    new = published(engine)[0].meeting_id
    assert new != old
    with pipeline.store.sessions() as session:
        assert session.get(Meeting, old) is None
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 1
        assert session.get(Meeting, new).status == "completed"


def test_failed_update_preserves_published_run_and_hash(engine, recording):
    pipeline = IngestionPipeline(engine, FakeWorkflow)
    pipeline.ingest(recording)
    old = published(engine)[0]
    recording.write_text("Failed replacement")
    pipeline.workflow.fail = True
    with pytest.raises(RuntimeError, match="inference failed"):
        pipeline.ingest(recording)
    current = published(engine)[0]
    assert current.meeting_id == old.meeting_id
    assert current.content_sha256 == old.content_sha256
    with pipeline.store.sessions() as session:
        assert session.get(Meeting, pipeline.store.meeting_id).status == "incomplete"
        assert session.get(Meeting, old.meeting_id).status == "completed"
    pipeline.workflow.fail = False
    assert pipeline.ingest(recording)
    assert published(engine)[0].meeting_id != old.meeting_id


def test_failure_on_first_ingestion_does_not_publish(engine, recording):
    pipeline = IngestionPipeline(engine, lambda store: FakeWorkflow(store, fail=True))
    with pytest.raises(RuntimeError, match="inference failed"):
        pipeline.ingest(recording)
    assert published(engine) == []


def test_force_replaces_unchanged_and_namespaces_are_independent(engine, recording):
    pipeline = IngestionPipeline(engine, FakeWorkflow)
    pipeline.ingest(recording)
    old = published(engine)[0].meeting_id
    assert pipeline.ingest(recording, force=True)
    assert published(engine)[0].meeting_id != old
    assert pipeline.ingest(recording, namespace="other-dataset")
    assert len(published(engine)) == 2
    with pytest.raises(ValueError, match="namespace"):
        pipeline.ingest(recording, namespace=" ")


def test_input_snapshot_matches_published_hash_when_original_changes(engine, recording):
    original = recording.read_bytes()
    pipeline = IngestionPipeline(
        engine,
        lambda store: FakeWorkflow(
            store, during=lambda path: recording.write_text("Changed during inference")
        ),
    )
    assert pipeline.ingest(recording)
    assert published(engine)[0].content_sha256 == hashlib.sha256(original).hexdigest()
    assert published(engine)[0].content_sha256 != content_digest(recording)


def test_source_lock_is_exclusive_and_released_after_error(engine):
    key = "dataset/file"
    lock_id = int.from_bytes(
        hashlib.sha256(key.encode()).digest()[:8], "big", signed=True
    )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as other:
        with pytest.raises(RuntimeError), source_lock(engine, key):
            assert not other.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id}
            )
            raise RuntimeError("failed")
        assert other.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id})
        other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})


def test_publication_failure_rolls_back_pointer_and_deletion(engine, recording):
    pipeline = IngestionPipeline(engine, FakeWorkflow)
    pipeline.ingest(recording)
    old = published(engine)[0]
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION reject_delete() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'cannot delete'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER protect_old BEFORE DELETE ON meetings "
                "FOR EACH ROW EXECUTE FUNCTION reject_delete()"
            )
        )
    recording.write_text("Replacement")
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="cannot delete"):
        pipeline.ingest(recording)
    assert published(engine)[0].meeting_id == old.meeting_id
    assert published(engine)[0].content_sha256 == old.content_sha256


def test_migration_is_additive_and_idempotent(engine, recording):
    pipeline = IngestionPipeline(engine, FakeWorkflow)
    pipeline.ingest(recording)
    old = published(engine)[0].meeting_id
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE meetings DROP COLUMN recording_duration_seconds")
        )
    with pytest.raises(RuntimeError, match="Missing columns"):
        check_schema(engine)
    apply_migrations(engine, "meetings")
    apply_migrations(engine, "meetings")
    check_schema(engine)
    with pipeline.store.sessions() as session:
        meeting = session.get(Meeting, old)
        assert meeting.recording_duration_seconds is None
        assert meeting.status == "completed"


def test_staging_rejects_workflow_using_another_store(engine, recording):
    pipeline = IngestionPipeline(
        engine,
        lambda store: SimpleNamespace(store=object(), transcribe=lambda path: ()),
    )
    with pytest.raises(RuntimeError, match="staging store"):
        pipeline.ingest(recording)


def test_cli_skips_published_inputs_without_hugging_face_token(engine, recording):
    IngestionPipeline(engine, FakeWorkflow).ingest(recording)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = engine.url.render_as_string(hide_password=False)
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    environment.pop("HF_TOKEN", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipelines.transcription.pipeline",
            "--input-dir",
            str(recording.parent),
        ],
        cwd=recording.parent,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Unchanged, skipping" in result.stderr
    assert len(published(engine)) == 1
