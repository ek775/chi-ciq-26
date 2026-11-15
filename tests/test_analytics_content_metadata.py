import logging
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text

from databases.analytics.models import sources as analytics_sources
from databases.analytics.schema import migrate_database as migrate_analytics
from databases.documents.schema import (
    documents,
    ensure_directory,
)
from databases.documents.schema import (
    migrate_database as migrate_documents,
)
from databases.meetings.models import (
    Meeting,
    PublishedSource,
    Speaker,
    TranscriptSegment,
)
from pipelines.analytics import metadata, sources
from pipelines.analytics.metadata import UNKNOWN_BODY, extract_metadata
from pipelines.analytics.pipeline import AnalyticsConfig, AnalyticsPipeline


@pytest.mark.parametrize(
    "content",
    [
        "",
        "We need to finish this policy by November 4, 2026.",
        "The deadline is 2026-11-04. Committee on Finance should review it.",
        "The last meeting was on November 4, 2026.",
        "Our next meeting is on November 4, 2026.",
        "Committee on Finance\nPolicy deadline\nNovember 4, 2026",
    ],
)
def test_unidentified_content_stays_unknown(content):
    result = extract_metadata(content)
    assert result.meeting_date is None
    assert result.body == UNKNOWN_BODY
    assert result.date_status == result.body_status == "unavailable"


@pytest.mark.parametrize(
    "content",
    [
        (
            "This meeting of the Committee on Finance on November 4, 2026 "
            "will come to order."
        ),
        (
            "Today is Wednesday, November 4th, 2026.\n"
            "I call to order this meeting of the Committee on Finance."
        ),
        "COMMITTEE ON FINANCE\nMEETING AGENDA\nNovember 4, 2026",
        "Committee on Finance Meeting Agenda\n2026-11-04",
        ("Welcome to the Committee on Finance meeting. Today is November 4, 2026."),
    ],
)
def test_explicit_self_identification(content):
    result = extract_metadata(content)
    assert result.meeting_date == date(2026, 11, 4)
    assert result.body == "committee on finance"
    assert result.date_status == result.body_status == "inferred"
    assert result.evidence


def test_conflicts_are_independent_and_explicit():
    result = extract_metadata(
        "Meeting date: 2026-11-04\nMeeting date: 2026-11-05\n"
        "This meeting of the Committee on Finance will come to order.\n"
        "This meeting of the Committee on Housing will come to order."
    )
    assert result.meeting_date is None
    assert result.body == UNKNOWN_BODY
    assert result.date_status == result.body_status == "conflicting"
    assert len(result.evidence) >= 4


def test_repeated_agreeing_evidence_and_unrelated_deadline():
    result = extract_metadata(
        "Today is November 4, 2026. Today is November 4th, 2026.\n"
        "This meeting of the Chicago City Council will come to order.\n"
        "The project must finish by December 1, 2027."
    )
    assert result.meeting_date == date(2026, 11, 4)
    assert result.body == "city council"


def test_invalid_date_is_not_inferred():
    result = extract_metadata("Meeting date: February 30, 2026")
    assert result.meeting_date is None
    assert result.date_status == "unavailable"


def test_legacy_fields_win_independently_and_logging_has_provenance(caplog):
    with caplog.at_level(logging.INFO):
        inferred_date, body = sources._source_metadata(
            "meetings:opaque.mp4",
            (
                "Today is November 4, 2026. "
                "This meeting of the Committee on Finance will come to order."
            ),
            meeting_date=date(2025, 1, 1),
        )
    assert inferred_date == date(2025, 1, 1)
    assert body == "committee on finance"
    assert "(existing)" in caplog.text
    assert "(inferred)" in caplog.text
    assert "content_evidence=" in caplog.text
    assert sources._source_metadata(
        "irrelevant", "Today is November 4, 2026.", meeting_type="City_Council"
    ) == (date(2026, 11, 4), "city council")


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


def install_snapshot(monkeypatch, row, units):
    class Connection:
        def execute(self, query):
            return Result(units if "sequence" in str(query) else [row])

    @contextmanager
    def snapshot(engine):
        yield Connection()

    monkeypatch.setattr(sources, "snapshot", snapshot)


@pytest.mark.parametrize("filename", ["opaque.mp4", "2020-01-01_City_Council.mp4"])
def test_meeting_reader_content_only_and_signatures_match(monkeypatch, filename):
    row = {
        "id": "meeting-id",
        "key": filename,
        "filename": filename,
        "meeting_date": None,
        "meeting_type": None,
        "recording_duration_seconds": 2.0,
        "text_embedding_model": "test",
        "voice_embedding_model": None,
    }
    original_text = (
        "Today is November 4, 2026. "
        "This meeting of the Committee on Finance will come to order."
    )
    segment = SimpleNamespace(
        label="SPEAKER_00",
        start_seconds=0.0,
        end_seconds=2.0,
        text=original_text,
        text_embedding=[1.0, 0.0],
        voice_embedding=None,
    )
    install_snapshot(monkeypatch, row, [segment])
    reader = sources.MeetingsReader(None)
    key = f"meetings:{filename}"
    loaded = reader.load(key)
    assert loaded.signature == reader.signatures()[key]
    assert loaded.signature != row["id"]
    assert loaded.meeting_date == date(2026, 11, 4)
    assert loaded.body == "committee on finance"
    assert loaded.units[0].text == original_text
    assert loaded.units[0].speaker == "SPEAKER_00"
    assert loaded.speakers[0].label == "SPEAKER_00"
    monkeypatch.setattr(sources, "EXTRACTION_VERSION", "next-version")
    assert reader.signatures()[key] != loaded.signature
    assert reader.load(key).signature == reader.signatures()[key]


@pytest.mark.parametrize(
    "content, expected_date, expected_body",
    [
        ("Policy deadline November 4, 2026", None, UNKNOWN_BODY),
        (
            "Committee on Finance\nAgenda\nNovember 4, 2026",
            date(2026, 11, 4),
            "committee on finance",
        ),
    ],
)
def test_document_reader_uses_full_text_not_chunks_or_path(
    monkeypatch, content, expected_date, expected_body
):
    row = {
        "key": "opaque",
        "filename": "2020-01-01 City Council.txt",
        "relative_path": "2020-01-01 City Council/2020-01-01 City Council.txt",
        "content_sha256": "a" * 64,
        "processing_signature": "test",
        "text_embedding_model": "test",
        "table_name": "doc_test_" + "a" * 40,
        "text": content,
    }
    chunk_text = "An unchanged quote without any meeting metadata."
    install_snapshot(
        monkeypatch,
        row,
        [SimpleNamespace(sequence=0, text=chunk_text, text_embedding=[1.0, 0.0])],
    )
    reader = sources.DocumentsReader(None)
    loaded = reader.load("documents:opaque")
    assert loaded.meeting_date == expected_date
    assert loaded.body == expected_body
    assert loaded.units[0].text == chunk_text
    assert loaded.units[0].speaker is None
    assert loaded.signature == reader.signatures()["documents:opaque"]
    assert metadata.EXTRACTION_VERSION in loaded.signature
    monkeypatch.setattr(sources, "EXTRACTION_VERSION", "next-version")
    assert reader.load("documents:opaque").signature != loaded.signature
    assert (
        reader.load("documents:opaque").signature
        == reader.signatures()["documents:opaque"]
    )


def test_unavailable_and_conflicting_metadata_are_logged(caplog):
    with caplog.at_level(logging.INFO):
        sources._source_metadata("unknown", "A policy statement.")
        sources._source_metadata(
            "conflict", "Meeting date: 2026-11-04\nMeeting date: 2026-11-05"
        )
    assert "(unavailable)" in caplog.text
    assert "(conflicting)" in caplog.text


def test_legacy_metadata_change_invalidates_signature():
    row = {
        "id": "id",
        "meeting_date": None,
        "meeting_type": None,
        "recording_duration_seconds": None,
    }
    old = sources._meeting_signature(row)
    assert sources._meeting_signature({**row, "meeting_type": "Finance"}) != old
    assert sources._meeting_signature({**row, "meeting_date": date(2026, 11, 4)}) != old
    assert (
        sources._meeting_signature({**row, "recording_duration_seconds": 7200}) != old
    )


def test_undated_meeting_does_not_read_metadata_from_filename(monkeypatch):
    row = {
        "id": "id",
        "key": "opaque",
        "filename": "2026-11-04_City_Council.mp4",
        "meeting_date": None,
        "meeting_type": None,
        "recording_duration_seconds": None,
        "text_embedding_model": "test",
        "voice_embedding_model": None,
    }
    install_snapshot(monkeypatch, row, [])
    loaded = sources.MeetingsReader(None).load("meetings:opaque")
    assert loaded.meeting_date is None
    assert loaded.body == UNKNOWN_BODY


@pytest.mark.parametrize(
    "content, expected_date, expected_body",
    [
        (
            (
                "Today is November 4, 2026. "
                "This meeting of the Committee on Finance will come to order."
            ),
            date(2026, 11, 4),
            "committee on finance",
        ),
        ("The policy deadline is November 4, 2026.", None, UNKNOWN_BODY),
        (
            (
                "Meeting date: 2026-11-04\nMeeting date: 2026-11-05\n"
                "This meeting of the Committee on Finance will come to order.\n"
                "This meeting of the Committee on Housing will come to order."
            ),
            None,
            UNKNOWN_BODY,
        ),
    ],
)
def test_postgres_sync_persists_content_metadata(
    engine, content, expected_date, expected_body
):
    """Use actual stored source rows and the production analytics sync stage."""
    migrate_documents(engine)
    meeting_id, speaker_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            Meeting.__table__.insert().values(
                id=meeting_id,
                source_path="/recordings/opaque.mp4",
                filename="2020-01-01_City_Council.mp4",
                meeting_date=None,
                meeting_type=None,
                status="completed",
                transcription_model="test",
                diarization_model="test",
                text_embedding_model="test",
                voice_embedding_model="test",
            )
        )
        connection.execute(
            Speaker.__table__.insert().values(
                id=speaker_id, meeting_id=meeting_id, label="SPEAKER_00"
            )
        )
        connection.execute(
            TranscriptSegment.__table__.insert().values(
                meeting_id=meeting_id,
                speaker_id=speaker_id,
                sequence=0,
                start_seconds=0.0,
                end_seconds=2.0,
                text=content,
                text_embedding=[1.0, 0.0],
                voice_embedding=[1.0, 0.0],
            )
        )
        connection.execute(
            PublishedSource.__table__.insert().values(
                key="opaque", meeting_id=meeting_id, content_sha256="a" * 64
            )
        )
        chunks = ensure_directory(connection, "test", ".")
        connection.execute(
            documents.insert().values(
                key="test/opaque.txt",
                directory_key="test/.",
                relative_path="2020-01-01_City_Council.txt",
                filename="2020-01-01_City_Council.txt",
                format="txt",
                content_sha256="b" * 64,
                processing_signature="test",
                text=content,
                text_embedding_model="test",
                text_embedding_dimensions=2,
                chunk_count=1,
            )
        )
        connection.execute(
            chunks.insert().values(
                document_key="test/opaque.txt",
                sequence=0,
                start_character=0,
                end_character=10,
                text="Exact unmodified quote.",
                text_embedding=[1.0, 0.0],
            )
        )
        schema = f"analytics_content_{uuid4().hex}"
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    destination = create_engine(
        engine.url.update_query_dict({"options": f"-csearch_path={schema},public"})
    )
    try:
        migrate_analytics(destination)
        readers = [sources.MeetingsReader(engine), sources.DocumentsReader(engine)]
        pipeline = AnalyticsPipeline(
            destination, readers, config=AnalyticsConfig(stages=("sync",))
        )
        assert pipeline.run()["sources_synced"] == 2
        with destination.connect() as connection:
            rows = connection.execute(select(analytics_sources)).mappings().all()
        assert len(rows) == 2
        for row in rows:
            assert row["meeting_date"] == expected_date
            assert row["body"] == expected_body
        for reader in readers:
            for key, signature in reader.signatures().items():
                assert reader.load(key).signature == signature
        assert pipeline.run()["sources_unchanged"] == 2
    finally:
        destination.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
