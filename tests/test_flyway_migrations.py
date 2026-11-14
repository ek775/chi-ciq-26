import re
from pathlib import Path

import pytest
from flyway_migrations import MIGRATION_ROOT, migration_files, migration_version

from databases.analytics.views import (
    DASHBOARD_OBJECTS,
    DASHBOARD_READER_ROLE,
    MATERIALIZED_VIEWS,
    PLAIN_VIEWS,
)

DASHBOARD_VIEWS = MIGRATION_ROOT / "analytics/migrations/R__dashboard_views.sql"


@pytest.mark.parametrize("database_name", ["meetings", "documents", "analytics"])
def test_each_database_has_a_baseline_with_unique_versions(database_name: str) -> None:
    versions = [
        migration_version(path)
        for path in migration_files(database_name)
        if path.name.startswith("V")
    ]
    assert versions[0] == (1,)
    assert len(versions) == len(set(versions))


def test_versions_sort_by_number_and_not_by_text() -> None:
    names = ["V10__later.sql", "V2__second.sql", "V1_1__patch.sql", "V1__baseline.sql"]
    ordered = sorted((Path(name) for name in names), key=migration_version)
    assert [path.name for path in ordered] == [
        "V1__baseline.sql",
        "V1_1__patch.sql",
        "V2__second.sql",
        "V10__later.sql",
    ]


def test_dashboard_view_file_defines_the_views_that_the_code_names() -> None:
    sql = DASHBOARD_VIEWS.read_text()
    assert tuple(re.findall(r"^CREATE VIEW (\w+) AS$", sql, re.M)) == PLAIN_VIEWS
    assert (
        tuple(re.findall(r"^CREATE MATERIALIZED VIEW (\w+) AS$", sql, re.M))
        == MATERIALIZED_VIEWS
    )
    # REFRESH ... CONCURRENTLY needs a unique index on each materialized view.
    assert tuple(re.findall(r"^CREATE UNIQUE INDEX \w+ ON (\w+) ", sql, re.M)) == (
        MATERIALIZED_VIEWS
    )
    dropped = set(
        re.findall(r"^DROP (?:MATERIALIZED )?VIEW IF EXISTS (\w+)", sql, re.M)
    )
    assert dropped == set(PLAIN_VIEWS) | set(MATERIALIZED_VIEWS)


def test_dashboard_view_file_grants_only_the_dashboard_objects() -> None:
    sql = DASHBOARD_VIEWS.read_text()
    granted = sql.split("GRANT SELECT ON", 1)[1].split(f"TO {DASHBOARD_READER_ROLE}")[0]
    assert {name.strip() for name in granted.split(",")} == set(DASHBOARD_OBJECTS)
