"""Representative roster, manual speaker labels, and text mention matching.

The analytics ``roster`` stage seeds current officials from the City of
Chicago Data Portal (see ``officials.py``); CSV rosters loaded with
``load-roster`` override and extend them, so corrections always win.
"""

import csv
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from databases.analytics.models import ROLES

ROSTER_COLUMNS = ("id", "display_name", "role", "ward", "aliases")
LABEL_COLUMNS = ("source_key", "speaker_label", "representative_id")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


@dataclass(frozen=True)
class Representative:
    id: str
    display_name: str
    role: str
    ward: int | None
    aliases: tuple[str, ...]
    active_from: date | None = None
    active_to: date | None = None
    source_ref: str | None = None


@dataclass(frozen=True)
class SpeakerLabel:
    source_key: str
    speaker_label: str
    representative_id: str
    note: str | None = None


def normalize_alias(value: str) -> str:
    return " ".join(value.lower().split())


def _optional_date(value: str | None, field: str, line: int) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError(f"Line {line}: {field} must be YYYY-MM-DD") from error


def _require_columns(reader: csv.DictReader, required: Iterable[str]) -> None:
    missing = set(required) - set(reader.fieldnames or ())
    if missing:
        raise ValueError(f"CSV is missing columns: {sorted(missing)}")


def read_roster(path: Path) -> list[Representative]:
    """Read ``id,display_name,role,ward,aliases[,active_from,active_to]``.

    ``aliases`` is semicolon-separated; the display name is always an alias.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, ROSTER_COLUMNS)
        representatives: dict[str, Representative] = {}
        for line, row in enumerate(reader, start=2):
            identifier = (row["id"] or "").strip()
            name = " ".join((row["display_name"] or "").split())
            role = (row["role"] or "").strip().lower()
            if not SLUG.fullmatch(identifier):
                raise ValueError(f"Line {line}: id must be a lowercase slug")
            if identifier in representatives:
                raise ValueError(f"Line {line}: duplicate id {identifier}")
            if not name:
                raise ValueError(f"Line {line}: display_name is required")
            if role not in ROLES:
                raise ValueError(f"Line {line}: role must be one of {ROLES}")
            ward_text = (row["ward"] or "").strip()
            ward = int(ward_text) if ward_text else None
            if ward is not None and not 1 <= ward <= 50:
                raise ValueError(f"Line {line}: ward must be 1-50")
            aliases = {normalize_alias(name)}
            aliases.update(
                normalize_alias(alias)
                for alias in (row["aliases"] or "").split(";")
                if alias.strip()
            )
            short = sorted(alias for alias in aliases if len(alias) < 3)
            if short:
                raise ValueError(f"Line {line}: aliases too short: {short}")
            active_from = _optional_date(row.get("active_from"), "active_from", line)
            active_to = _optional_date(row.get("active_to"), "active_to", line)
            if active_from and active_to and active_to < active_from:
                raise ValueError(f"Line {line}: active_to precedes active_from")
            representatives[identifier] = Representative(
                id=identifier,
                display_name=name,
                role=role,
                ward=ward,
                aliases=tuple(sorted(aliases)),
                active_from=active_from,
                active_to=active_to,
            )
    return list(representatives.values())


def read_speaker_labels(path: Path) -> list[SpeakerLabel]:
    """Read ``source_key,speaker_label,representative_id[,note]`` rows."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, LABEL_COLUMNS)
        labels: dict[tuple[str, str], SpeakerLabel] = {}
        for line, row in enumerate(reader, start=2):
            values = [(row[column] or "").strip() for column in LABEL_COLUMNS]
            if not all(values):
                raise ValueError(f"Line {line}: {LABEL_COLUMNS} are required")
            source_key, speaker_label, representative_id = values
            if (source_key, speaker_label) in labels:
                raise ValueError(f"Line {line}: duplicate speaker label")
            note = (row.get("note") or "").strip() or None
            labels[(source_key, speaker_label)] = SpeakerLabel(
                source_key, speaker_label, representative_id, note
            )
    return list(labels.values())


def roster_digest(aliases: Iterable[tuple[str, str]]) -> str:
    """Stable hash of (representative_id, alias) pairs for rescan detection."""
    payload = "\n".join(f"{rep}\t{alias}" for rep, alias in sorted(set(aliases)))
    return hashlib.sha256(payload.encode()).hexdigest()


class MentionMatcher:
    """Count whole-phrase, case-insensitive alias matches per representative.

    All aliases share one alternation tried longest first, so overlapping
    names ("rodriguez sanchez" vs "rodriguez") count only the longest match.
    """

    def __init__(self, aliases: Iterable[tuple[str, str]]):
        owners: dict[str, set[str]] = {}
        for representative_id, alias in aliases:
            owners.setdefault(normalize_alias(alias), set()).add(representative_id)
        self.owners = owners
        self.pattern = (
            re.compile(
                r"(?<!\w)(?:"
                + "|".join(
                    r"\s+".join(map(re.escape, alias.split()))
                    for alias in sorted(owners, key=lambda a: (-len(a), a))
                )
                + r")(?!\w)",
                re.IGNORECASE,
            )
            if owners
            else None
        )

    def count(self, text: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        if self.pattern is None:
            return counts
        for match in self.pattern.finditer(text):
            for representative_id in self.owners[normalize_alias(match.group(0))]:
                counts[representative_id] = counts.get(representative_id, 0) + 1
        return counts
