import argparse
import hashlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from databases.meetings.schema import connect_database
from shared.logging import configure_logging

from .models import metadata
from .views import MATERIALIZED_VIEWS, PLAIN_VIEWS

__all__ = ["check_schema", "connect_database", "lock_key"]


def lock_key(connection: Connection, key: str) -> None:
    lock_id = int.from_bytes(
        hashlib.sha256(key.encode()).digest()[:8], "big", signed=True
    )
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})


def check_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table in metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(
                    f"Missing table {table.name}; apply the Flyway migrations"
                )
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            missing = set(table.columns.keys()) - actual
            if missing:
                raise RuntimeError(
                    f"Missing columns in {table.name}: {sorted(missing)}; "
                    "apply the Flyway migrations"
                )
        views = set(inspector.get_view_names())
        materialized = set(inspector.get_materialized_view_names())
        missing_views = (set(PLAIN_VIEWS) - views) | (
            set(MATERIALIZED_VIEWS) - materialized
        )
        if missing_views:
            raise RuntimeError(
                f"Missing dashboard views {sorted(missing_views)}; "
                "apply the Flyway migrations"
            )


def main(argv: list[str] | None = None) -> None:
    from databases.meetings.archive import export_database

    load_dotenv()
    configure_logging("analytics-database")
    parser = argparse.ArgumentParser(description="Manage the analytics database")
    commands = parser.add_subparsers(dest="command", required=True)
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
        check_schema(engine)
        logger.info("Analytics database %s completed", args.command)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
