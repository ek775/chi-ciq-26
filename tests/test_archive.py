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
from databases.meetings.metadata import parse_filename
from databases.meetings.models import Meeting, Speaker, TranscriptSegment


def test_export_is_private_atomic_and_password_not_in_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(archive.shutil, "which", lambda name: "/usr/bin/pg_dump")
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
    first = archive.export_database(url, tmp_path)
    second = archive.export_database(url, tmp_path)
    assert first != second
    assert first.read_bytes() == second.read_bytes() == b"PGDMP"
    assert first.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.partial"))


def test_failed_export_cleans_partial_file_and_preserves_existing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(archive.shutil, "which", lambda name: "/usr/bin/pg_dump")
    existing = tmp_path / "existing.dump"
    existing.write_bytes(b"keep")

    def fail(command, **kwargs):
        kwargs["stdout"].write(b"incomplete")
        return SimpleNamespace(returncode=1, stderr="connection refused")

    monkeypatch.setattr(archive.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="connection refused"):
        archive.export_database(
            "postgresql+psycopg://localhost/transcription", tmp_path
        )
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"keep"


def test_export_requires_pg_dump(tmp_path, monkeypatch):
    monkeypatch.setattr(archive.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="pg_dump"):
        archive.export_database("postgresql://localhost/transcription", tmp_path)


def test_documents_archive_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(archive.shutil, "which", lambda name: "/usr/bin/pg_dump")

    def run(command, **kwargs):
        kwargs["stdout"].write(b"PGDMP")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(archive.subprocess, "run", run)
    output = archive.export_database(
        "postgresql://localhost/documents", tmp_path, prefix="documents"
    )
    assert output.name.startswith("documents-")
    assert output.read_bytes() == b"PGDMP"


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
    dump_path = archive.export_database(
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
                str(dump_path),
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
