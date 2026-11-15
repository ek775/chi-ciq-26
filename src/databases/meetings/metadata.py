import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


@dataclass(frozen=True)
class MeetingMetadata:
    filename: str
    meeting_date: date | None
    meeting_type: str | None


def vimeo_audio_id(source: str | Path) -> str | None:
    match = re.fullmatch(
        r"vimeo-([0-9]+)-audio-low\.mp4", Path(source).name, re.IGNORECASE
    )
    return match.group(1) if match else None


def parse_filename(source: str | Path) -> MeetingMetadata:
    path = Path(source)
    if path.suffix.lower() != ".mp4":
        raise ValueError(f"Expected an MP4 file: {path.name}")
    stem = re.sub(r" \(\d+p\)$", "", path.stem)
    match = re.fullmatch(
        r"(?P<year>\d{4})_(?P<month>[a-z]+)_(?P<day>\d{1,2})_-_(?P<kind>.+)",
        stem,
        flags=re.IGNORECASE,
    )
    if match:
        month_name = match["month"].lower()
        if month_name not in MONTHS:
            raise ValueError(f"Unknown month in filename: {month_name}")
        month = MONTHS[month_name]
    else:
        match = re.fullmatch(
            r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_(?P<kind>.+)",
            stem,
        )
        if not match:
            raise ValueError(
                f"Filename must include a date and meeting type: {path.name}"
            )
        month = int(match["month"])
    kind = match["kind"].replace("_", " ").strip()
    if not kind:
        raise ValueError("Meeting type must not be empty")
    return MeetingMetadata(
        filename=path.name,
        meeting_date=date(int(match["year"]), month, int(match["day"])),
        meeting_type=kind,
    )
