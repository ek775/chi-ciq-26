"""Container-only preparation and fail-closed checks for the isolated ROCm run."""

import argparse
import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

WORK = Path("/work")
logger = logging.getLogger(__name__)


def positive_setting(name: str, default: int, maximum: int) -> int:
    value = int(os.environ.get(name, default))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def selected_ids(path: Path, count: int) -> list[str]:
    from pipelines.downloads.vimeo import vimeo_id

    ids = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            video = vimeo_id(entry)
            if video not in ids:
                ids.append(video)
            if len(ids) == count:
                return ids
    raise ValueError(f"Established Vimeo list contains fewer than {count} unique IDs")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            output.write(json.dumps(value, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def prepare(work: Path = WORK, archive: Path = Path("/archive")) -> None:
    from pipelines.downloads import vimeo

    (work / "report.json").unlink(missing_ok=True)
    count = positive_setting("ROCM_AUDIO_COUNT", 10, 20)
    workers = positive_setting("ROCM_DOWNLOAD_WORKERS", 2, 4)
    document_count = positive_setting("ROCM_DOCUMENT_COUNT", 3, 10)
    ids = selected_ids(vimeo.DEFAULT_IDS, count)
    settings = {
        "ids": ids,
        "audio_mode": "full-recordings",
        "document_count": document_count,
    }
    manifest_path = work / "manifest.json"
    previous = None
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous["settings"] != settings:
            raise ValueError(
                "Sample settings changed: use a fresh COMPOSE_PROJECT_NAME/work volume"
            )
    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    # This is the existing downloader, including its retries, receipts and locks.
    status = vimeo.main(
        [*ids, "--audio-only", "--workers", str(workers), "--output-dir", str(raw)]
    )
    if status:
        raise RuntimeError("Public audio acquisition failed; see recording IDs above")
    sources = {}
    for video in ids:
        receipt = json.loads((raw / ".vimeo" / f"{video}.json").read_text())
        original = raw / vimeo.validate_filename(receipt["filename"])
        if (
            receipt["video_id"] != video
            or receipt["audio_only"] is not True
            or vimeo.digest(original) != receipt["sha256"]
        ):
            raise ValueError(f"{video}: invalid original audio receipt")
        key = f"rocm-smoke/{original.name}"
        if previous is not None:
            saved = previous["sources"][key]
            if saved["content_sha256"] != receipt["sha256"]:
                raise ValueError(f"{video}: smoke input changed; use a fresh project")
        sources[key] = {
            "video_id": video,
            "recording_url": f"https://vimeo.com/{video}",
            "recording_offset_s": 0,
            "original_filename": receipt["filename"],
            "content_sha256": receipt["sha256"],
        }
    if {path.name for path in raw.glob("*.mp4")} != {
        source["original_filename"] for source in sources.values()
    }:
        raise ValueError("Unexpected recordings in work volume; use a fresh project")
    documents = work / "documents"
    documents.mkdir(exist_ok=True)
    selected = sorted(archive.glob("*.txt"))[:document_count]
    if len(selected) != document_count:
        raise ValueError(f"Need {document_count} reference TXT documents in {archive}")
    expected_documents = {path.name for path in selected}
    if {path.name for path in documents.glob("*")} - expected_documents:
        raise ValueError("Unexpected document samples; use a fresh project")
    for path in selected:
        # Bound CPU embedding work without modifying the reference archive.
        excerpt = path.read_text(encoding="utf-8")[:12000]
        if not excerpt.strip():
            raise ValueError(f"Empty reference document: {path}")
        target = documents / path.name
        if previous is not None and (
            not target.is_file() or target.read_text(encoding="utf-8") != excerpt
        ):
            raise ValueError("Document sample changed; use a fresh project")
        target.write_text(excerpt, encoding="utf-8")
    write_json(manifest_path, {"settings": settings, "sources": sources})
    logger.info(
        "Prepared %d complete audio recordings and %d documents",
        count,
        document_count,
    )


def gpu() -> None:
    import torch

    if not torch.version.hip or not torch.cuda.is_available():
        raise RuntimeError("ROCm PyTorch/GPU unavailable; refusing CPU transcription")
    device = torch.device("cuda")
    tensor = torch.ones((32, 32), device=device)
    result = (tensor @ tensor).sum().item()
    if result != 32768:
        raise RuntimeError(f"ROCm GPU computation returned {result}, expected 32768")
    logger.info(
        "ROCm %s: %s; GPU computation passed",
        torch.version.hip,
        torch.cuda.get_device_name(0),
    )


def tracker(work: Path = WORK) -> None:
    from pipelines.analytics.evidence import EvidenceIndex
    from pipelines.tracker import database
    from pipelines.tracker.__main__ import make_dataset
    from pipelines.tracker.statements import read_segments

    manifest = json.loads((work / "manifest.json").read_text())
    segments = read_segments(
        os.environ["MEETINGS_DATABASE_URL"], list(manifest["sources"])
    )
    candidates = {}
    evidence = []
    for key, source in manifest["sources"].items():
        rows = sorted(
            (
                row
                for (source_key, _), row in segments.items()
                if source_key == key and row["text"].strip()
            ),
            key=lambda row: row["sequence"],
        )
        if not rows:
            raise ValueError(f"No nonempty published segments for {key}")
        row = rows[0]
        candidates[key] = {
            "published_source_key": key,
            "segment_sequence": row["sequence"],
            "content_sha256": source["content_sha256"],
            "quote": row["text"],
            "recording_id": f"vimeo:{source['video_id']}",
            "recording_url": source["recording_url"],
            "recording_offset_s": source["recording_offset_s"],
            "type": "statement",
            "speaker": None,
            "speaker_confidence": "unconfirmed",
        }
        evidence.append(
            {
                "id": key,
                "recording_id": f"vimeo:{source['video_id']}",
                "start_s": row["start_seconds"],
                "end_s": row["end_seconds"],
                "kind": "agenda",
                "source": "local smoke source mapping; no reviewed identity or agenda",
            }
        )
    inputs = {
        "coverage": "ROCm integration sample: first published segment per complete recording",
        "model": "local-smoke-exact-segment-copy",
        "candidates": candidates,
    }
    directory = work / "tracker-inputs"
    write_json(directory / "candidates.json", inputs)
    evidence_directory = directory / "evidence"
    evidence_directory.mkdir(exist_ok=True)
    (evidence_directory / "unattributed.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in evidence), encoding="utf-8"
    )
    dataset, _ = make_dataset(
        inputs, segments, EvidenceIndex.load(evidence_directory), []
    )
    check_tracker(dataset, candidates, segments)
    output = work / "tracker"
    subprocess.run(
        [
            "chi-ciq-alderman-tracker",
            "build",
            "--candidates",
            str(directory / "candidates.json"),
            "--evidence-dir",
            str(evidence_directory),
            "--output-dir",
            str(output),
        ],
        check=True,
    )
    subprocess.run(["chi-ciq-alderman-tracker", "migrate"], check=True)
    subprocess.run(
        [
            "chi-ciq-alderman-tracker",
            "publish",
            str(output / "tracker.json"),
            "--batch-id",
            "rocm-smoke",
        ],
        check=True,
    )
    subprocess.run(
        [
            "chi-ciq-alderman-tracker",
            "query",
            "--batch-id",
            "rocm-smoke",
            "--person-id",
            "unconfirmed",
        ],
        check=True,
    )
    published = database.load_batch(
        os.environ["TRACKER_ANALYTICS_DATABASE_URL"], "rocm-smoke"
    )
    check_tracker(published, candidates, segments)


def check_tracker(dataset: dict, candidates: dict, segments: dict) -> None:
    if dataset["rejected_candidates"] or len(dataset["statements"]) != len(candidates):
        raise ValueError("Tracker did not accept exactly one quote per recording")
    for row in dataset["statements"]:
        segment = segments[(row["published_source_key"], row["segment_sequence"])]
        if (
            row["quote"] != segment["text"]
            or row["verified"] is not True
            or row["speaker"] is not None
            or row["person_id"] is not None
            or row["speaker_confidence"] != "unconfirmed"
        ):
            raise ValueError("Tracker smoke quote/identity contract violated")


def read_rows(url: str, sql: str) -> list[dict]:
    from sqlalchemy import text

    from databases.meetings.schema import connect_database

    engine = connect_database(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return [dict(row) for row in connection.execute(text(sql)).mappings()]
    finally:
        engine.dispose()


def get_json(path: str):
    with urlopen(f"http://web:8080{path}", timeout=30) as response:
        return json.load(response)


def export(directory: Path = Path("/exports")) -> None:
    from databases.meetings.archive import export_database

    archive = export_database(
        os.environ["ANALYTICS_DATABASE_URL"], directory, prefix="analytics"
    )
    with archive.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError(f"Invalid PostgreSQL custom-format dump: {archive}")
    subprocess.run(
        ["pg_restore", "--list", str(archive)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    logger.info("Persisted and validated complete analytics database: %s", archive)
    print(archive, flush=True)


def verify(work: Path = WORK) -> None:
    from pipelines.tracker import database
    from pipelines.tracker.statements import read_segments

    manifest = json.loads((work / "manifest.json").read_text())
    expected = manifest["settings"]
    meetings = get_json("/api/meetings?limit=200")
    if len(meetings) != len(expected["ids"]):
        raise ValueError("Web did not publish the requested number of recordings")
    for meeting in meetings:
        segments = get_json(f"/api/meetings/{meeting['id']}/segments?limit=200")
        if not segments or not any(row["text"].strip() for row in segments):
            raise ValueError(f"Web has no transcript for {meeting['id']}")
    if get_json("/ready") != {"status": "ready"}:
        raise ValueError("Web schema readiness failed")
    summary = get_json("/api/analytics/summary")
    if (
        summary["meetings"] != len(expected["ids"])
        or summary["documents"] != expected["document_count"]
        or not summary["refreshed_at"]
        or summary["last_run"]["status"] != "completed"
    ):
        raise ValueError(f"Unexpected dashboard publication: {summary}")
    if not get_json("/api/analytics/topics") or not get_json(
        "/api/analytics/representatives"
    ):
        raise ValueError("Missing dashboard taxonomy or roster")
    get_json("/api/analytics/statements")
    for path in ("/", "/dashboard"):
        with urlopen(f"http://web:8080{path}", timeout=30) as response:
            if b"<html" not in response.read().lower():
                raise ValueError(f"Missing HTML page: {path}")
    sources = read_rows(
        os.environ["DATABASE_URL"],
        "SELECT p.key, p.content_sha256, count(t.id) AS segments, "
        "min(vector_dims(t.text_embedding)) AS text_dimensions, "
        "min(vector_dims(t.voice_embedding)) AS voice_dimensions "
        "FROM published_sources p JOIN transcript_segments t ON t.meeting_id = p.meeting_id "
        "GROUP BY p.key, p.content_sha256",
    )
    if {row["key"] for row in sources} != set(manifest["sources"]):
        raise ValueError("Published source coverage does not match the audio batch")
    for row in sources:
        if (
            row["content_sha256"] != manifest["sources"][row["key"]]["content_sha256"]
            or row["segments"] < 1
            or row["text_dimensions"] < 1
            or row["voice_dimensions"] < 1
        ):
            raise ValueError(f"Incomplete transcription/embedding result: {row['key']}")
    counts = read_rows(
        os.environ["ANALYTICS_DATABASE_URL"],
        "SELECT (SELECT count(*) FROM analysis_units) AS units, "
        "(SELECT count(*) FROM sentiment_results) AS sentiments, "
        "(SELECT count(*) FROM emotion_results) AS emotions, "
        "(SELECT count(*) FROM topic_relevance) AS relevance, "
        "(SELECT count(*) FROM stance_results) AS stances, "
        "(SELECT count(*) FROM speaker_attributions) AS attributions",
    )[0]
    if (
        counts["units"] < 1
        or counts["sentiments"] != counts["units"]
        or counts["emotions"] != counts["units"]
        or counts["relevance"] < counts["units"]
        or counts["stances"] < 1
        or counts["attributions"] != 0
    ):
        raise ValueError(
            f"Missing analytics stage output or unreviewed identity: {counts}"
        )
    quotes = read_rows(
        os.environ["ANALYTICS_DATABASE_URL"],
        "SELECT s.evidence, s.evidence_verified, u.text FROM stance_results s "
        "JOIN analysis_units u ON u.id = s.unit_id WHERE s.evidence IS NOT NULL",
    )
    for quote in quotes:
        if quote["evidence"] and (
            quote["evidence_verified"] is not True
            or quote["evidence"] not in quote["text"]
        ):
            raise ValueError(
                "Stance evidence is not a verbatim verified transcript quote"
            )
    dataset = database.load_batch(
        os.environ["TRACKER_ANALYTICS_DATABASE_URL"], "rocm-smoke"
    )
    candidates = json.loads((work / "tracker-inputs" / "candidates.json").read_text())[
        "candidates"
    ]
    segments = read_segments(
        os.environ["MEETINGS_DATABASE_URL"], list(manifest["sources"])
    )
    check_tracker(dataset, candidates, segments)
    for name in ("meetings", "documents", "analytics"):
        for phase in ("before", "after"):
            dumps = list((work / "backups" / f"{name}-{phase}").glob("*.dump"))
            if not dumps or any(path.stat().st_size == 0 for path in dumps):
                raise ValueError(f"Missing or empty {name} {phase} backup")
    report = {
        "status": "passed",
        "verified_at": datetime.now(UTC).isoformat(),
        "recordings": len(meetings),
        "dashboard": summary,
        "analytics": counts,
    }
    write_json(work / "report.json", report)
    print(json.dumps(report, indent=2))


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "gpu", "tracker", "export", "verify")
    )
    command = parser.parse_args().command
    {
        "prepare": prepare,
        "gpu": gpu,
        "tracker": tracker,
        "export": export,
        "verify": verify,
    }[command]()


if __name__ == "__main__":
    main()
