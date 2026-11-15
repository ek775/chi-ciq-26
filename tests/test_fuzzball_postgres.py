from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_postgres_workflow() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "fuzzball.yaml").read_text())


def test_data_directory_is_on_a_persistent_volume() -> None:
    workflow = load_postgres_workflow()
    service = workflow["services"]["postgres"]
    environment = dict(entry.split("=", 1) for entry in service["env"])
    data_directory = Path(environment["PGDATA"])
    mounted_volumes = [
        mount["volume"]
        for mount_path, mount in service["mounts"].items()
        if Path(mount_path) in data_directory.parents
    ]
    assert len(mounted_volumes) == 1
    # In a v4 Fuzzfile, `name` is what makes a volume persistent.
    assert workflow["volumes"][mounted_volumes[0]].get("name")


def test_service_outlives_its_dependents_and_keeps_its_password_secret() -> None:
    service = load_postgres_workflow()["services"]["postgres"]
    environment = dict(entry.split("=", 1) for entry in service["env"])
    assert service["persist"] is True
    assert environment["POSTGRES_PASSWORD"].startswith("secret://")


def test_tracker_restore_uses_an_ephemeral_data_directory() -> None:
    service = load_postgres_workflow()["services"]["tracker-snapshot-db"]
    environment = dict(entry.split("=", 1) for entry in service["env"])
    assert service["persist"] is False
    assert environment["PGDATA"] == "/tmp/tracker-pgdata"
    assert not service.get("mounts")
