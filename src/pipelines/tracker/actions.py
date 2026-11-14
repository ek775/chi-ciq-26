"""Import person-level actions from official archive legislation records."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def _chicago_date(value: str) -> str:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(ZoneInfo("America/Chicago"))
    return timestamp.date().isoformat()


def read_actions(paths: list[Path]) -> list[dict]:
    records = {}
    for path in paths:
        matter = json.loads(path.read_text(encoding="utf-8"))
        number = matter["recordNumber"]
        common = {"matter_id": number, "title": matter["title"]}
        if matter.get("filingSponsorId") and matter.get("introductionDate"):
            person_id = matter["filingSponsorId"].lower()
            key = f"{number}:sponsor:{person_id}"
            records[key] = {
                **common,
                "action_id": key,
                "type": "filing_sponsor",
                "person_id": person_id,
                "person": matter["filingSponsor"],
                "date": _chicago_date(matter["introductionDate"]),
                "source": f"{path}#filingSponsorId",
            }
        for action in matter.get("actions") or []:
            for vote in action.get("votes") or []:
                if not vote.get("personId") or not vote.get("vote"):
                    continue
                person_id = vote["personId"].lower()
                history_id = action["historyId"]
                key = f"{number}:{history_id}:vote:{person_id}"
                record = {
                    **common,
                    "action_id": key,
                    "type": "recorded_vote",
                    "person_id": person_id,
                    "person": vote["voterName"],
                    "vote": vote["vote"],
                    "date": _chicago_date(action["actionDate"]),
                    "official_meeting_id": action.get("meetingId") or None,
                    "source": f"{path}#actions/{history_id}/votes/{person_id}",
                }
                previous = records.get(key)
                if previous and previous["vote"] != record["vote"]:
                    raise ValueError(f"Conflicting official votes for {key}")
                records[key] = record
    return [records[key] for key in sorted(records)]
