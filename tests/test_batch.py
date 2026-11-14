import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL

from databases.meetings import archive
from examples import batch_transcribe as batch
from pipelines.transcription import Meeting, Speaker, TranscriptSegment, parse_filename


def test_discovery_is_sorted_case_insensitive_and_nonrecursive(tmp_path):
    for name in ("2026-11-11_budget.mp4", "2026-11-10_budget.MP4", "notes.txt"):
        (tmp_path / name).touch()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/invalid.mp4").touch()
    assert [p.name for p in batch.discover_recordings(tmp_path)] == [
        "2026-11-10_budget.MP4",
        "2026-11-11_budget.mp4",
    ]


def test_discovery_rejects_missing_empty_and_invalid_inputs(tmp_path):
    with pytest.raises(NotADirectoryError):
        batch.discover_recordings(tmp_path / "missing")
    with pytest.raises(ValueError, match="No MP4"):
        batch.discover_recordings(tmp_path)
    (tmp_path / "invalid.mp4").touch()
    with pytest.raises(ValueError, match="Filename"):
        batch.discover_recordings(tmp_path)


def test_export_is_private_atomic_and_password_not_in_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.shutil, "which", lambda name: "/usr/bin/pg_dump")
    calls = []

    def run(command, **kwargs):
        assert "postgresql://" in command[2]
        assert "postgresql+psycopg" not in command[2]
        assert all("secret" not in argument for argument in command)
        assert kwargs["env"]["PGPASSWORD"] == "secret"
        assert len(list(tmp_path.glob("*.dump"))) == len(calls)
        assert len(list(tmp_path.glob("*.partial"))) == 1
        calls.append(command)
        kwargs["stdout"].write(b"PGDMP")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(archive.subprocess, "run", run)
    url = "postgresql+psycopg://user:secret@localhost/transcription"
    first = batch.export_database(url, tmp_path)
    second = batch.export_database(url, tmp_path)
    assert first != second
    assert first.read_bytes() == second.read_bytes() == b"PGDMP"
    assert first.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.partial"))


def test_failed_export_cleans_partial_file_and_preserves_existing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(batch.shutil, "which", lambda name: "/usr/bin/pg_dump")
    existing = tmp_path / "existing.dump"
    existing.write_bytes(b"keep")

    def fail(command, **kwargs):
        kwargs["stdout"].write(b"incomplete")
        return SimpleNamespace(returncode=1, stderr="connection refused")

    monkeypatch.setattr(archive.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="connection refused"):
        batch.export_database("postgresql+psycopg://localhost/transcription", tmp_path)
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"keep"


def test_export_requires_pg_dump(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="pg_dump"):
        batch.export_database("postgresql://localhost/transcription", tmp_path)


def test_export_never_overwrites_a_completed_archive(tmp_path, monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now(timezone):
            return datetime(2026, 11, 14, tzinfo=UTC)

    monkeypatch.setattr(archive, "datetime", FixedDatetime)
    monkeypatch.setattr(archive, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    monkeypatch.setattr(archive.shutil, "which", lambda name: "/usr/bin/pg_dump")

    def run(command, **kwargs):
        kwargs["stdout"].write(b"original")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(archive.subprocess, "run", run)
    first = archive.export_database("postgresql://localhost/transcription", tmp_path)
    with pytest.raises(FileExistsError):
        archive.export_database("postgresql://localhost/transcription", tmp_path)
    assert first.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [first]


def test_batch_closes_iterator_on_processing_error(caplog):
    caplog.set_level(logging.INFO)
    closed = []

    def transcribe(path):
        try:
            yield SimpleNamespace(
                meeting_id="meeting",
                speaker_id="speaker",
                start_seconds=0,
                end_seconds=1,
            )
            raise RuntimeError("model failed")
        finally:
            closed.append(path)

    workflow = SimpleNamespace(transcribe=transcribe)
    with pytest.raises(RuntimeError, match="model failed"):
        batch.transcribe_recordings(workflow, [Path("first.mp4"), Path("second.mp4")])
    assert closed == [Path("first.mp4")]
    assert "Video 1/2: first.mp4" in caplog.text
    assert "Video first.mp4: failed or interrupted in" in caplog.text
    assert "Video 2/2" not in caplog.text


def test_cli_env_defaults_and_overrides(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "2026-11-10_budget.mp4").touch()
    output = tmp_path / "output"
    monkeypatch.setattr(batch, "load_dotenv", lambda: None)
    monkeypatch.setenv("TRANSCRIPTION_INPUT_DIR", str(inputs))
    monkeypatch.setenv("TRANSCRIPTION_OUTPUT_DIR", str(output))
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/transcription")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setattr(batch.shutil, "which", lambda name: "/usr/bin/pg_dump")
    disposed = []
    monkeypatch.setattr(
        batch,
        "create_database",
        lambda url: SimpleNamespace(dispose=lambda: disposed.append(True)),
    )
    monkeypatch.setattr(batch, "TranscriptStore", lambda engine: engine)
    builds = []
    monkeypatch.setattr(
        batch, "build_workflow", lambda store, token: builds.append(token) or object()
    )
    processed = []
    monkeypatch.setattr(
        batch, "transcribe_recordings", lambda workflow, files: processed.append(files)
    )
    exports = []
    monkeypatch.setattr(
        batch,
        "export_database",
        lambda url, directory: exports.append(directory) or directory / "test.dump",
    )
    batch.main([])
    override = tmp_path / "override"
    batch.main(["--input-dir", str(inputs), "--output-dir", str(override)])
    assert builds == ["test-token", "test-token"]
    assert processed == [[inputs / "2026-11-10_budget.mp4"]] * 2
    assert exports == [output, override]
    assert disposed == [True, True]

    def fail(workflow, files):
        raise RuntimeError("model failed")

    monkeypatch.setattr(batch, "transcribe_recordings", fail)
    with pytest.raises(RuntimeError, match="model failed"):
        batch.main([])
    assert exports == [output, override]
    assert disposed == [True, True, True]


def test_dump_restores_to_new_database(engine, store, tmp_path):
    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        pytest.skip("PostgreSQL client tools required")
    schema = engine.url.query["options"].split("=", 1)[1].split(",", 1)[0]
    source = Path("2026-11-10_budget.mp4")
    meeting_id = store.start(
        source,
        parse_filename(source),
        transcription_model="whisper",
        diarization_model="pyannote",
        text_embedding_model="text",
        voice_embedding_model="voice",
    )
    store.set_recording_duration(meeting_id, 10)
    segment = store.append(
        meeting_id,
        sequence=0,
        speaker="SPEAKER_00",
        start=1,
        end=2,
        transcript="Budget discussion",
        text_embedding=[1, 2, 3],
        voice_embedding=[4, 5],
    )
    store.finish(meeting_id, completed=True)
    archive = batch.export_database(
        engine.url.render_as_string(hide_password=False), tmp_path
    )
    database_name = f"restore_test_{uuid4().hex}"
    admin = create_engine(engine.url, isolation_level="AUTOCOMMIT")
    restored = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        restore_url = engine.url.set(database=database_name)
        libpq_url = URL.create(
            "postgresql",
            username=restore_url.username,
            host=restore_url.host,
            port=restore_url.port,
            database=database_name,
        ).render_as_string(hide_password=False)
        environment = os.environ.copy()
        if restore_url.password is not None:
            environment["PGPASSWORD"] = restore_url.password
        subprocess.run(
            [
                "pg_restore",
                "--dbname",
                libpq_url,
                "--no-password",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                str(archive),
            ],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        restored = create_engine(restore_url)
        with restored.connect() as connection:
            assert connection.scalar(select(Meeting.recording_duration_seconds)) == 10
            row = connection.execute(
                select(
                    TranscriptSegment.text, Speaker.label, TranscriptSegment.end_seconds
                )
                .join(Speaker, TranscriptSegment.speaker_id == Speaker.id)
                .where(TranscriptSegment.id == segment.id)
            ).one()
            assert row == ("Budget discussion", "SPEAKER_00", 2)
            assert (
                connection.scalar(
                    text(
                        f'SELECT vector_dims(text_embedding) FROM "{schema}".transcript_segments'
                    )
                )
                == 3
            )
            assert (
                connection.scalar(
                    text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                )
                == "vector"
            )
    finally:
        if restored is not None:
            restored.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin.dispose()
