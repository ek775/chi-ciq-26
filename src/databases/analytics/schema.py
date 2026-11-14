import argparse
import hashlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Connection, Engine

from databases.meetings.schema import connect_database
from shared.logging import configure_logging

from .models import ALIAS_ORIGINS, REPRESENTATIVE_ORIGINS, metadata, schema_state
from .views import (
    DASHBOARD_READER_ROLE,
    DASHBOARD_VIEWS_VERSION,
    MATERIALIZED_VIEWS,
    PLAIN_VIEWS,
    create_dashboard_views,
    drop_dashboard_views,
    grant_dashboard_access,
)

__all__ = ["check_schema", "connect_database", "lock_key", "migrate_database"]


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


ADDED_COLUMNS = (
    (
        "ALTER TABLE representatives "
        "ADD COLUMN IF NOT EXISTS origin VARCHAR NOT NULL DEFAULT 'manual' "
        "CONSTRAINT representatives_origin "
        f"CHECK (origin IN ({_values(REPRESENTATIVE_ORIGINS)}))"
    ),
    "ALTER TABLE representatives ADD COLUMN IF NOT EXISTS source_ref VARCHAR",
    (
        "ALTER TABLE representative_aliases "
        "ADD COLUMN IF NOT EXISTS origin VARCHAR NOT NULL DEFAULT 'manual' "
        f"CONSTRAINT aliases_origin CHECK (origin IN ({_values(ALIAS_ORIGINS)}))"
    ),
)


def lock_key(connection: Connection, key: str) -> None:
    lock_id = int.from_bytes(
        hashlib.sha256(key.encode()).digest()[:8], "big", signed=True
    )
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})


def _views_version(connection: Connection) -> str | None:
    return connection.execute(
        select(schema_state.c.value).where(
            schema_state.c.key == "dashboard_views_version"
        )
    ).scalar_one_or_none()


def migrate_database(engine: Engine) -> None:
    """Create tables additively and rebuild derived views on a version change."""
    with engine.begin() as connection:
        lock_key(connection, "analytics-schema")
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        metadata.create_all(connection)
        # create_all never alters existing tables; add later columns in place.
        for statement in ADDED_COLUMNS:
            connection.execute(text(statement))
        if _views_version(connection) != DASHBOARD_VIEWS_VERSION:
            drop_dashboard_views(connection)
            create_dashboard_views(connection)
            connection.execute(
                text(
                    "INSERT INTO schema_state (key, value) "
                    "VALUES ('dashboard_views_version', :version) "
                    "ON CONFLICT (key) DO UPDATE "
                    "SET value = excluded.value, updated_at = now()"
                ),
                {"version": DASHBOARD_VIEWS_VERSION},
            )
        # Rebuilt views lose their grants; restore them for the dashboard role.
        grant_dashboard_access(connection, DASHBOARD_READER_ROLE)


def check_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table in metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"Missing table {table.name}; run analytics migrate")
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            missing = set(table.columns.keys()) - actual
            if missing:
                raise RuntimeError(
                    f"Missing columns in {table.name}: {sorted(missing)}; "
                    "run analytics migrate"
                )
        version = _views_version(connection)
        if version != DASHBOARD_VIEWS_VERSION:
            raise RuntimeError(
                f"Dashboard views version {version!r} != "
                f"{DASHBOARD_VIEWS_VERSION!r}; run analytics migrate"
            )
        views = set(inspector.get_view_names())
        materialized = set(inspector.get_materialized_view_names())
        missing_views = (set(PLAIN_VIEWS) - views) | (
            set(MATERIALIZED_VIEWS) - materialized
        )
        if missing_views:
            raise RuntimeError(
                f"Missing dashboard views {sorted(missing_views)}; "
                "run analytics migrate"
            )


def main(argv: list[str] | None = None) -> None:
    from databases.meetings.archive import export_database

    load_dotenv()
    configure_logging("analytics-database")
    parser = argparse.ArgumentParser(description="Manage the analytics database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("check")
    backup = commands.add_parser("backup")
    backup.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    url = os.environ["ANALYTICS_DATABASE_URL"]
    logger = logging.getLogger(__name__)
    logger.info("Analytics database %s started", args.command)
    if args.command == "backup":
        archive = export_database(url, args.output_dir, prefix="analytics")
        logger.info("Analytics database backup completed: %s", archive)
        print(archive, flush=True)
        return
    engine = connect_database(url)
    try:
        if args.command == "migrate":
            migrate_database(engine)
        check_schema(engine)
        logger.info("Analytics database %s completed", args.command)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
