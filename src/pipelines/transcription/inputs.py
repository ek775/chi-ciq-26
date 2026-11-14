import hashlib
from pathlib import Path

from databases.meetings.metadata import parse_filename


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
