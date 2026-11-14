"""Generate the representative roster from official data and source text.

Alderpersons come from the City of Chicago Data Portal "Ward Offices" dataset
(one current alderperson per ward). Citywide officials, which that dataset does
not cover, come from the bundled snapshot. The snapshot also mirrors the portal
so runs without network access still produce a roster.

Transcripts are speech recognition output, so names are often misspelled
("Alderman Riley" for Reilly). ``discover_candidates`` finds title-prefixed
names in source text and maps unknown spellings to roster surnames by string
and phonetic similarity; only unambiguous, repeated matches become aliases.
"""

import difflib
import http.client
import json
import logging
import re
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .roster import Representative, normalize_alias

logger = logging.getLogger(__name__)

PORTAL_URL = (
    "https://data.cityofchicago.org/resource/htai-wnw4.json"
    "?$select=ward,alderman&$limit=100"
)
SNAPSHOT_PATH = Path(__file__).with_name("chicago_officials.json")
ROSTER_SOURCES = ("portal", "snapshot", "off")

# Forms used to address or introduce an alderperson, including committee chairs.
ALDER_TITLES = (
    "alderman",
    "alderwoman",
    "alderperson",
    "alder person",
    "alder",
    "ald.",
    "chairman",
    "chairwoman",
    "chair",
    "vice chair",
)
CITYWIDE_TITLES = {
    "mayor": ("mayor",),
    "clerk": ("clerk", "city clerk"),
    "treasurer": ("treasurer", "city treasurer"),
}
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
TITLE_NAME = re.compile(
    r"(?<![\w.])(?i:vice\s+chair|chairwoman|chairman|chair|alderwoman|alderman"
    r"|alderperson|alder\s+person|alder|ald\.?)[.,]?\s+([A-Z][A-Za-z'\u2019-]+)"
)
POSSESSIVE = re.compile(r"'s$")
CONTRACTION = re.compile(r"'(?:ll|re|ve|m|d|t)$")
# Capitalized words that follow a title in speech but are not surnames.
_NOT_NAMES = """
    a all and are as at be been but by can did do does for from go good had has
    have he her here him his how i if in is it its just let madam me members mr
    mrs ms my no not now of off ok okay on or our person please present pro
    recognize recognized recognizes she so tem thank thanks that the their them
    then there these they this to um uh we well what when who will with yes you
    your yeah again any first could hello hi alder alderman alderwoman
    alderperson chair chairman chairwoman vice woman commissioner
"""
NOT_NAMES = frozenset(_NOT_NAMES.split())


@dataclass(frozen=True)
class Candidate:
    name: str
    example: str
    occurrences: int
    sources: int
    status: str
    representative_id: str | None
    similarity: float | None


def slugify(value: str) -> str:
    value = value.lower().replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def surname_variants(surname: str) -> set[str]:
    """Spellings speech recognition and typing commonly produce."""
    base = normalize_alias(surname).replace("\u2019", "'")
    spaced = base.replace("-", " ")
    variants = {base, spaced, spaced.replace(" ", "")}
    variants |= {variant.replace("'", "") for variant in variants}
    return {" ".join(variant.split()) for variant in variants if variant.strip()}


def parse_portal_name(value: str) -> tuple[str, str, str | None]:
    """Split ``"Cardona, Jr., Felix"`` into given name, surname, and suffix."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) < 2:
        raise ValueError(f"Unexpected alderperson name {value!r}")
    suffixes = [part for part in parts[1:-1] if part.lower() in SUFFIXES]
    return parts[-1], parts[0], suffixes[0] if suffixes else None


def official(
    role: str,
    given: str,
    surname: str,
    *,
    ward: int | None = None,
    suffix: str | None = None,
    source_ref: str | None = None,
) -> Representative:
    first = given.split()[0]
    display = " ".join(part for part in (given, surname, suffix) if part)
    titles = ALDER_TITLES if role == "alderperson" else CITYWIDE_TITLES.get(role, ())
    aliases = {normalize_alias(display), normalize_alias(f"{given} {surname}")}
    for variant in surname_variants(surname):
        aliases.add(f"{normalize_alias(first)} {variant}")
        aliases.update(f"{title} {variant}" for title in titles)
    return Representative(
        id=slugify(f"{first} {surname}"),
        display_name=display,
        role=role,
        ward=ward,
        aliases=tuple(sorted(alias for alias in aliases if len(alias) >= 3)),
        source_ref=source_ref,
    )


def drop_shared_aliases(roster: Sequence[Representative]) -> list[Representative]:
    """Remove aliases claimed by more than one person; they identify no one."""
    owners: dict[str, set[str]] = {}
    for item in roster:
        for alias in item.aliases:
            owners.setdefault(alias, set()).add(item.id)
    shared = {alias for alias, ids in owners.items() if len(ids) > 1}
    if shared:
        logger.info("Dropped ambiguous roster aliases: %s", sorted(shared))
    return [
        Representative(
            **{
                **item.__dict__,
                "aliases": tuple(a for a in item.aliases if a not in shared),
            }
        )
        for item in roster
    ]


def _alderpersons(records: Iterable[Mapping], source_ref: str) -> list[Representative]:
    members = []
    for record in records:
        ward = int(str(record["ward"]).strip())
        if not 1 <= ward <= 50:
            raise ValueError(f"Invalid ward {ward}")
        given, surname, suffix = parse_portal_name(str(record["alderman"]))
        members.append(
            official(
                "alderperson",
                given,
                surname,
                ward=ward,
                suffix=suffix,
                source_ref=source_ref,
            )
        )
    if len({member.ward for member in members}) != len(members):
        raise ValueError("Duplicate wards in alderperson roster")
    return members


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_ward_offices(url: str = PORTAL_URL, timeout: float = 30.0) -> list[dict]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        records = json.loads(response.read())
    if not isinstance(records, list) or not records:
        raise ValueError("Ward offices response is empty")
    return records


def official_roster(
    source: str = "portal",
    *,
    url: str = PORTAL_URL,
    timeout: float = 30.0,
    snapshot_path: Path = SNAPSHOT_PATH,
) -> list[Representative]:
    """Current officials; ``portal`` falls back to the snapshot when offline."""
    if source not in ROSTER_SOURCES:
        raise ValueError(f"Roster source must be one of {ROSTER_SOURCES}")
    if source == "off":
        return []
    snapshot = load_snapshot(snapshot_path)
    records, ref = snapshot["alderpersons"], snapshot["alderpersons_source"]
    if source == "portal":
        try:
            records, ref = fetch_ward_offices(url, timeout), url.split("?")[0]
        except (OSError, ValueError, http.client.HTTPException) as error:
            logger.warning(
                "Ward offices fetch failed (%s); using snapshot from %s",
                error,
                snapshot["as_of"],
            )
    roster = _alderpersons(records, ref)
    roster.extend(
        official(
            item["role"],
            item["given"],
            item["surname"],
            suffix=item.get("suffix"),
            source_ref=item["source"],
        )
        for item in snapshot["citywide"]
    )
    return drop_shared_aliases(roster)


def soundex(word: str) -> str:
    word = _key(word)
    if not word:
        return ""
    codes = {
        **dict.fromkeys("bfpv", "1"),
        **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"),
        "l": "4",
        **dict.fromkeys("mn", "5"),
        "r": "6",
    }
    result, previous = word[0].upper(), codes.get(word[0], "")
    for letter in word[1:]:
        code = codes.get(letter, "")
        if code and code != previous:
            result += code
        if letter not in "hw":
            previous = code
    return (result + "000")[:4]


def surname_index(roster: Iterable[Representative]) -> dict[str, set[str]]:
    """Map compact surname keys to representative ids.

    Surnames come from title aliases ("alderman la spata"), or from the
    display name when there are none. Parts of compound surnames ("spata",
    "hoppenworth") are added only when they are not someone's full surname.
    """
    full: dict[str, set[str]] = {}
    parts: dict[str, set[str]] = {}
    for item in roster:
        surnames = set()
        for alias in item.aliases:
            for title in sorted(ALDER_TITLES, key=len, reverse=True):
                if alias.startswith(title + " "):
                    surnames.add(alias[len(title) + 1 :])
                    break
        if not surnames:
            words = [
                word
                for word in item.display_name.split()
                if word.lower().strip(".") not in {s.strip(".") for s in SUFFIXES}
            ]
            surnames = {words[-1]} if len(words) > 1 else set()
        for surname in surnames:
            full.setdefault(_key(surname), set()).add(item.id)
            for part in re.split(r"[\s-]+", surname):
                parts.setdefault(_key(part), set()).add(item.id)
    for key, owners in parts.items():
        if key not in full:
            full[key] = owners
    return {key: owners for key, owners in full.items() if len(key) >= 3}


def _onset(key: str) -> str:
    return "aeiouy" if key[0] in "aeiouy" else key[0]


def match_surname(
    token: str, surnames: Mapping[str, set[str]]
) -> tuple[str, str | None, float | None]:
    """Return (status, representative id, similarity) for one name token."""
    key = _key(token)
    if key in surnames:
        owners = surnames[key]
        if len(owners) == 1:
            return "matched", next(iter(owners)), 1.0
        return "ambiguous", None, 1.0
    if len(key) < 4:
        return "unmatched", None, None
    scored: list[tuple[float, float, str]] = []
    for name, owners in surnames.items():
        if _onset(key) != _onset(name):
            continue
        ratio = difflib.SequenceMatcher(None, key, name).ratio()
        sounds_alike = soundex(key) == soundex(name)
        if ratio >= 0.8 or (sounds_alike and ratio >= 0.6):
            scored.extend(
                (ratio + (0.1 if sounds_alike else 0), ratio, owner) for owner in owners
            )
    if not scored:
        return "unmatched", None, None
    scored.sort(reverse=True)
    best_score, best_ratio, best = scored[0]
    rivals = [score for score, _, owner in scored[1:] if owner != best]
    if rivals and best_score - rivals[0] < 0.05:
        return "ambiguous", None, round(best_ratio, 3)
    return "matched", best, round(best_ratio, 3)


def discover_candidates(
    texts: Iterable[tuple[str, str]],
    roster: Sequence[Representative],
    *,
    min_occurrences: int = 3,
) -> list[Candidate]:
    """Find title-prefixed names in ``(source_key, text)`` pairs.

    Names seen at least ``min_occurrences`` times are matched to one
    alderperson's surname exactly, by spelling similarity, or by sound; only
    unambiguous matches become aliases.
    """
    surnames = surname_index(item for item in roster if item.role == "alderperson")
    occurrences: dict[str, int] = {}
    seen_in: dict[str, set[str]] = {}
    examples: dict[str, str] = {}
    for source_key, text in texts:
        for match in TITLE_NAME.finditer(text):
            name = match.group(1).lower().replace("\u2019", "'").strip("'-")
            name = POSSESSIVE.sub("", name)
            if len(name) < 3 or name in NOT_NAMES or CONTRACTION.search(name):
                continue
            occurrences[name] = occurrences.get(name, 0) + 1
            seen_in.setdefault(name, set()).add(source_key)
            examples.setdefault(name, " ".join(match.group(0).split()))
    candidates = []
    for name, count in sorted(occurrences.items()):
        if count < min_occurrences:
            continue
        status, representative_id, similarity = match_surname(name, surnames)
        candidates.append(
            Candidate(
                name=name,
                example=examples[name],
                occurrences=count,
                sources=len(seen_in[name]),
                status=status,
                representative_id=representative_id,
                similarity=similarity,
            )
        )
    return candidates


def discovered_aliases(candidates: Iterable[Candidate]) -> set[tuple[str, str]]:
    return {
        (candidate.representative_id, f"{title} {candidate.name}")
        for candidate in candidates
        if candidate.status == "matched" and candidate.representative_id
        for title in ALDER_TITLES
    }
