"""Conservative, content-only meeting metadata with auditable evidence.

Only explicit opening statements, labeled meeting dates, and document headers
qualify. Policy dates, filenames, and recording/upload dates are not evidence.
"""

import re
from dataclasses import dataclass
from datetime import date

from .units import normalize_body

EXTRACTION_VERSION = "content-metadata-v1"
UNKNOWN_BODY = "unknown"

_MONTHS = [
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
]
_DATE = (
    r"(?:\d{4}-\d{2}-\d{2}|"
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4})"
)
_WEEKDAY = r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
_BODY = (
    r"(?:Committee\s+on\s+[A-Za-z][A-Za-z &'’-]*?"
    r"|(?:Chicago\s+)?City\s+Council(?:\s+of\s+(?:the\s+)?City\s+of\s+Chicago)?)"
)
_FLAGS = re.IGNORECASE | re.MULTILINE
_DATE_PATTERNS = [
    re.compile(rf"^\s*Meeting\s+date\s*:\s*{_WEEKDAY}(?P<value>{_DATE})\b", _FLAGS),
    re.compile(rf"\btoday\s+is\s+{_WEEKDAY}(?P<value>{_DATE})\b", _FLAGS),
    re.compile(
        rf"\b(?:this|today's)\s+(?:regular\s+|special\s+)?meeting"
        rf"(?:\s+of\s+(?:the\s+)?{_BODY})?"
        rf"\s+(?:is\s+(?:being\s+)?held\s+on|on|for)\s+"
        rf"{_WEEKDAY}(?P<value>{_DATE})\b",
        _FLAGS,
    ),
    re.compile(
        rf"\bwelcome\s+to\s+(?:the\s+)?"
        rf"{_WEEKDAY}(?P<value>{_DATE})\s+meeting\b",
        _FLAGS,
    ),
]
_BODY_PATTERNS = [
    re.compile(
        rf"\b(?:(?:this|today's)\s+(?:regular\s+|special\s+)?meeting\s+of|"
        rf"welcome\s+to\s+(?:this\s+meeting\s+of|the)|"
        rf"call\s+to\s+order\s+(?:this|the)\s+meeting\s+of)"
        rf"\s+(?:the\s+)?(?P<value>{_BODY})"
        rf"(?=\s*(?:[.,:;!?\n]|$|"
        rf"\b(?:meeting|will|is|to\s+order|on\s+{_DATE})\b))",
        _FLAGS,
    ),
    re.compile(
        rf"^\s*(?P<value>{_BODY})\s*(?:meeting|meeting\s+agenda|agenda|minutes)?"
        rf"\s*$",
        re.IGNORECASE,
    ),
]


@dataclass(frozen=True)
class ContentMetadata:
    meeting_date: date | None
    body: str
    date_status: str
    body_status: str
    evidence: tuple[str, ...]


def _parse_date(value: str) -> date | None:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        month, day, year = (
            re.sub(r"(\d)(?:st|nd|rd|th)", r"\1", value.lower())
            .replace(",", "")
            .split()
        )
        return date(int(year), _MONTHS.index(month) + 1, int(day))
    except ValueError:
        return None


def extract_metadata(content: str) -> ContentMetadata:
    """Resolve each field independently; disagreement never chooses a winner."""
    dates: set[date] = set()
    bodies: set[str] = set()
    evidence: list[str] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(content):
            parsed = _parse_date(match["value"])
            if parsed is not None:
                dates.add(parsed)
                evidence.append(f"date@{match.start()}:{match.group(0)}")

    # A standalone body/date is a header only near the start, not an agenda item
    # or a later reference to another committee's business.
    header_lines = content.splitlines()[:20]
    meeting_header = any(
        re.search(r"\b(?:meeting|agenda|minutes)\b", line, re.IGNORECASE)
        for line in header_lines
    )
    header_body = False
    for match in _BODY_PATTERNS[0].finditer(content):
        body = normalize_body(match["value"])
        if not body.startswith("committee on "):
            body = "city council"
        bodies.add(body)
        evidence.append(f"body@{match.start()}:{match.group(0)}")
    offset = 0
    for line in header_lines:
        match = _BODY_PATTERNS[1].fullmatch(line)
        if match and meeting_header:
            body = normalize_body(match["value"])
            if not body.startswith("committee on "):
                body = "city council"
            bodies.add(body)
            header_body = True
            evidence.append(f"body@{offset}:{line}")
        offset += len(line) + 1
    if header_body:
        for line in header_lines:
            match = re.fullmatch(
                rf"\s*{_WEEKDAY}(?P<value>{_DATE})(?:\s+at\s+\d.+)?\s*",
                line,
                re.IGNORECASE,
            )
            if match and (parsed := _parse_date(match["value"])) is not None:
                dates.add(parsed)
                evidence.append(f"header-date:{line}")

    def status(values: set) -> str:
        return (
            "inferred"
            if len(values) == 1
            else "conflicting"
            if values
            else "unavailable"
        )

    return ContentMetadata(
        meeting_date=next(iter(dates)) if len(dates) == 1 else None,
        body=next(iter(bodies)) if len(bodies) == 1 else UNKNOWN_BODY,
        date_status=status(dates),
        body_status=status(bodies),
        evidence=tuple(evidence),
    )
