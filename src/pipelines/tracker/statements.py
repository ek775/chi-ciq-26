"""Build tracker statements from published transcripts and quote candidates."""

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from pipelines.analytics.evidence import EvidenceIndex


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def read_segments(database_url: str, source_keys: list[str]) -> dict:
    from sqlalchemy import select

    from databases.meetings.models import Meeting, PublishedSource, TranscriptSegment
    from databases.meetings.schema import connect_database

    query = (
        select(
            PublishedSource.key,
            PublishedSource.content_sha256,
            Meeting.id.label("meeting_id"),
            Meeting.meeting_date,
            Meeting.meeting_type,
            Meeting.transcription_model,
            TranscriptSegment.sequence,
            TranscriptSegment.text,
            TranscriptSegment.start_seconds,
            TranscriptSegment.end_seconds,
        )
        .join(Meeting, Meeting.id == PublishedSource.meeting_id)
        .join(TranscriptSegment, TranscriptSegment.meeting_id == Meeting.id)
        .where(PublishedSource.key.in_(source_keys), Meeting.status == "completed")
    )
    engine = connect_database(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return {
                (row.key, row.sequence): dict(row)
                for row in connection.execute(query).mappings()
            }
    finally:
        engine.dispose()


def build_statements(manifest: dict, segments: dict, evidence: EvidenceIndex):
    statements = {}
    rejected = {}
    for key, candidate in manifest["candidates"].items():
        source_key = candidate["published_source_key"]
        row = segments.get((source_key, candidate["segment_sequence"]))
        if row is None:
            rejected[key] = "published segment missing"
            continue
        snapshot = candidate.get("content_sha256")
        if snapshot and snapshot != row["content_sha256"]:
            rejected[key] = "recording content changed"
            continue
        quote = normalize(candidate["quote"])
        # Token boundaries keep a fragment such as "tax" from matching "taxi".
        if not quote or f" {quote} " not in f" {normalize(row['text'])} ":
            rejected[key] = "quote does not match segment"
            continue
        recording_id = candidate["recording_id"]
        offset = candidate.get("recording_offset_s", 0)
        start = round(row["start_seconds"] + offset, 6)
        end = round(row["end_seconds"] + offset, 6)
        fields = evidence.resolve(recording_id, start, end)
        meeting_id = str(UUID(str(row["meeting_id"])))
        identity = hashlib.sha256(
            json.dumps([source_key, candidate["segment_sequence"], quote]).encode()
        ).hexdigest()[:24]
        hours, remainder = divmod(int(start), 3600)
        minutes, seconds = divmod(remainder, 60)
        statement = {
            "statement_id": f"{meeting_id}#{identity}",
            "meeting_id": meeting_id,
            "date": str(row["meeting_date"]),
            "body": row["meeting_type"],
            "recording_id": recording_id,
            "published_source_key": source_key,
            "segment_sequence": row["sequence"],
            "content_sha256": row["content_sha256"],
            "quote": candidate["quote"],
            "start_s": start,
            "end_s": end,
            "timestamp_precision": "segment",
            "video_link": (
                f"{candidate['recording_url'].split('#', 1)[0]}"
                f"#t={hours}h{minutes}m{seconds}s"
            ),
            "verified": True,
            "type": candidate["type"],
            "topic": candidate.get("topic"),
            "department": candidate.get("department"),
            "source": row["transcription_model"],
            "model": manifest.get("model", "reviewed-candidates"),
            **fields,
        }
        if statement["type"] == "commitment":
            statement["commitment"] = {
                **candidate["commitment"],
                "who": statement["speaker"],
            }
        statements[key] = statement
    return statements, rejected


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def write_statements(directory: Path, segments: dict, statements: dict) -> list:
    # Also write an empty partition when a supplied meeting has no valid quotes.
    by_meeting = defaultdict(dict)
    for row in segments.values():
        by_meeting[str(UUID(str(row["meeting_id"])))]
    for statement in statements.values():
        by_meeting[statement["meeting_id"]][statement["statement_id"]] = statement
    for meeting_id, records in by_meeting.items():
        atomic_write(
            directory / "statements" / f"{meeting_id}.jsonl",
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records.values()),
        )
    return sorted(by_meeting)
