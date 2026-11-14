import hashlib
import json
import re
from pathlib import Path

from databases.meetings.metadata import parse_filename, vimeo_audio_id


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


def validate_meeting_filename(filename: str) -> str:
    if Path(filename).name != filename or "\\" in filename:
        raise ValueError("Meeting filename must not contain directory components")
    parse_filename(filename)
    return filename


def meeting_filename(source: Path, metadata: dict[str, str]) -> str:
    video_id = vimeo_audio_id(source)
    if video_id is not None:
        if video_id not in metadata:
            raise ValueError(
                f"Missing meeting metadata for Vimeo ID {video_id}; "
                "provide --metadata-file mapping IDs to dated MP4 filenames"
            )
        return validate_meeting_filename(metadata[video_id])
    return validate_meeting_filename(source.name)


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
