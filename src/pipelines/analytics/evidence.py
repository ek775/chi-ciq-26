"""Resolve reviewed evidence against spans on the original recording timeline."""

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Evidence:
    id: str
    recording_id: str
    start_s: float
    end_s: float
    kind: str
    source: str
    speaker: str | None = None
    role: str | None = None
    person_id: str | None = None
    matter_id: str | None = None
    agenda_item: str | None = None

    def __post_init__(self):
        _check_span(self.start_s, self.end_s)
        if not all(value.strip() for value in (self.id, self.recording_id, self.source)):
            raise ValueError("Evidence requires an ID, recording ID, and source")
        if self.kind not in {
            "caption_label", "self_introduction", "recognition",
            "document_attribution", "reviewed_recording", "role", "agenda",
        }:
            raise ValueError(f"Unknown evidence kind: {self.kind}")
        if self.kind in {"role", "agenda"} and (self.speaker or self.person_id):
            raise ValueError("Role and agenda evidence cannot establish a name")
        if self.person_id and not self.speaker:
            raise ValueError("A person ID requires a speaker name")


def _check_span(start_s: float, end_s: float) -> None:
    if not (math.isfinite(start_s) and math.isfinite(end_s) and 0 <= start_s < end_s):
        raise ValueError("Expected a finite, positive-duration recording span")


class EvidenceIndex:
    def __init__(self, records: list[Evidence]):
        self.by_recording: dict[str, list[Evidence]] = {}
        ids = set()
        for record in records:
            if record.id in ids:
                raise ValueError(f"Duplicate evidence ID: {record.id}")
            ids.add(record.id)
            self.by_recording.setdefault(record.recording_id, []).append(record)

    @classmethod
    def load(cls, directory: Path) -> "EvidenceIndex":
        paths = sorted(directory.glob("*.jsonl"))
        if not paths:
            raise ValueError(f"No evidence files in {directory}")
        records = []
        for path in paths:
            with path.open(encoding="utf-8") as source:
                for number, line in enumerate(source, 1):
                    if line.strip():
                        try:
                            records.append(Evidence(**json.loads(line)))
                        except (TypeError, ValueError) as exc:
                            raise ValueError(f"{path}:{number}: {exc}") from exc
        return cls(records)

    def resolve(self, recording_id: str, start_s: float, end_s: float) -> dict:
        """Only use evidence covering the span. Conflicting overlaps veto a value."""
        _check_span(start_s, end_s)
        overlapping = [
            row for row in self.by_recording.get(recording_id, ())
            if row.start_s < end_s and row.end_s > start_s
        ]
        covering = [
            row for row in overlapping
            if row.start_s <= start_s and row.end_s >= end_s
        ]
        used = set()

        def resolve_fields(*fields):
            def key(row):
                return tuple(getattr(row, field) for field in fields)

            candidates = [row for row in covering if key(row)[0] is not None]
            values = {key(row) for row in overlapping if key(row)[0] is not None}
            if candidates and len(values) == 1:
                used.update(row.id for row in candidates)
                return key(candidates[0])
            return (None,) * len(fields)

        speaker, person_id = resolve_fields("speaker", "person_id")
        role, = resolve_fields("role")
        matter_id, = resolve_fields("matter_id")
        agenda_item, = resolve_fields("agenda_item")
        return {
            "speaker": speaker,
            "speaker_confidence": "named" if speaker else "unconfirmed",
            "person_id": person_id,
            "role": role,
            "matter_id": matter_id,
            "agenda_item": agenda_item,
            "evidence_ids": sorted(used),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("recording_id", help="For example, vimeo:1154082574")
    parser.add_argument("start_s", type=float)
    parser.add_argument("end_s", type=float)
    args = parser.parse_args()
    index = EvidenceIndex.load(args.directory)
    print(json.dumps(index.resolve(args.recording_id, args.start_s, args.end_s), indent=2))


if __name__ == "__main__":
    main()
