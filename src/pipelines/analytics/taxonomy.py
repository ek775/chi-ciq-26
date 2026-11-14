import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

KEY = re.compile(r"[a-z0-9][a-z0-9-]*")


@dataclass(frozen=True)
class Topic:
    key: str
    area: str
    label: str
    proposition: str
    description: str

    @property
    def definition_sha256(self) -> str:
        payload = json.dumps(
            [self.key, self.area, self.label, self.proposition, self.description]
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def embedding_text(self) -> str:
        return f"{self.label}. {self.proposition} {self.description}"


def load_taxonomy(path: Path | None = None) -> list[Topic]:
    """Load the packaged taxonomy or a JSON file with a ``topics`` list."""
    raw = (
        path.read_text(encoding="utf-8")
        if path is not None
        else files(__package__).joinpath("taxonomy.json").read_text(encoding="utf-8")
    )
    entries = json.loads(raw).get("topics")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Taxonomy must contain a nonempty 'topics' list")
    topics: dict[str, Topic] = {}
    for entry in entries:
        try:
            topic = Topic(
                **{
                    name: " ".join(str(entry[name]).split())
                    for name in ("key", "area", "label", "proposition", "description")
                }
            )
        except (KeyError, TypeError) as error:
            raise ValueError(f"Invalid taxonomy entry: {entry!r}") from error
        if not KEY.fullmatch(topic.key):
            raise ValueError(f"Topic key must be a lowercase slug: {topic.key!r}")
        if topic.key in topics:
            raise ValueError(f"Duplicate topic key: {topic.key}")
        if not all((topic.area, topic.label, topic.proposition)):
            raise ValueError(f"Topic {topic.key} needs area, label and proposition")
        topics[topic.key] = topic
    return list(topics.values())
