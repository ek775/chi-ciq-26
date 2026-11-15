import hashlib
import json
import re
from datetime import date
from pathlib import Path

from databases.meetings.metadata import MeetingMetadata, parse_filename, vimeo_audio_id


def load_metadata_file(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(
            "Metadata file must be a JSON object mapping Vimeo IDs to filenames"
        )
    for video_id, filename in data.items():
        if not isinstance(video_id, str) or not re.fullmatch(r"[0-9]+", video_id):
            raise ValueError(f"Invalid Vimeo ID in metadata file: {video_id!r}")
        if not isinstance(filename, str):
            raise TypeError(f"Invalid metadata filename for Vimeo ID {video_id}")
        validate_meeting_filename(filename)
    return data


def load_audio_meeting_map(path: Path | None) -> dict[str, MeetingMetadata]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format_version") != 2:
        raise ValueError("Audio meeting map must use format_version 2")
    expected_fields = {
        "meeting_date": "audio_files[].database_metadata.meeting_date",
        "meeting_type": "audio_files[].database_metadata.meeting_type",
    }
    schema_mapping = data.get("schema_mapping")
    if (
        not isinstance(schema_mapping, dict)
        or schema_mapping.get("target") != "databases.meetings.models.Meeting"
        or schema_mapping.get("fields") != expected_fields
        or schema_mapping.get("unknown_value") is not None
    ):
        raise ValueError("Audio meeting map does not match the meetings schema")
    rows = data.get("audio_files")
    if not isinstance(rows, list):
        raise TypeError("Audio meeting map audio_files must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("Audio meeting map entries must be objects")
        filename = row.get("audio_filename")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or "\\" in filename
            or Path(filename).suffix.lower() != ".mp4"
        ):
            raise ValueError("Audio meeting map contains an invalid audio_filename")
        if filename in result:
            raise ValueError(f"Duplicate audio filename in meeting map: {filename}")
        values = row.get("database_metadata")
        if not isinstance(values, dict) or set(values) != {
            "meeting_date",
            "meeting_type",
        }:
            raise ValueError(f"Invalid database metadata for {filename}")
        raw_date = values["meeting_date"]
        if raw_date is not None:
            if not isinstance(raw_date, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", raw_date
            ):
                raise ValueError(f"Invalid meeting_date for {filename}")
            try:
                meeting_date = date.fromisoformat(raw_date)
            except ValueError:
                raise ValueError(f"Invalid meeting_date for {filename}") from None
        else:
            meeting_date = None
        meeting_type = values["meeting_type"]
        if meeting_type is not None and (
            not isinstance(meeting_type, str) or not meeting_type.strip()
        ):
            raise ValueError(f"Invalid meeting_type for {filename}")
        result[filename] = MeetingMetadata(filename, meeting_date, meeting_type)
    return result


def validate_meeting_filename(filename: str) -> str:
    if Path(filename).name != filename or "\\" in filename:
        raise ValueError("Meeting filename must not contain directory components")
    parse_filename(filename)
    return filename


def meeting_filename(source: Path, metadata: dict[str, str]) -> str:
    video_id = vimeo_audio_id(source)
    if video_id is not None and video_id in metadata:
        return validate_meeting_filename(metadata[video_id])
    return source.name


def discover_recordings(
    directory: Path, metadata: dict[str, str] | None = None
) -> list[Path]:
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
        meeting_filename(recording, metadata or {})
    return recordings


def content_digest(source: Path) -> str:
    with source.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
