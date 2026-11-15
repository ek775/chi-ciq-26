import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, inspect, text

from shared.logging import configure_logging

from .models import Base


def connect_database(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("This library requires PostgreSQL with pgvector")
    return engine


def migrate_database(engine: Engine) -> None:
    """Preserve data while updating schema and nullable meeting metadata."""
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(738491025)"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(connection)
        connection.execute(
            text("ALTER TABLE meetings ALTER COLUMN meeting_date DROP NOT NULL")
        )
        connection.execute(
            text("ALTER TABLE meetings ALTER COLUMN meeting_type DROP NOT NULL")
        )
        connection.execute(
            text(
                "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS "
                "recording_duration_seconds double precision"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE meetings ALTER COLUMN "
                "recording_duration_seconds DROP NOT NULL"
            )
        )
        connection.execute(
            text(
                "DO $$ BEGIN "
                "IF NOT EXISTS ("
                "SELECT FROM pg_constraint "
                "WHERE conrelid = 'meetings'::regclass "
                "AND conname = 'meetings_recording_duration_seconds_check'"
                ") THEN "
                "ALTER TABLE meetings ADD CONSTRAINT "
                "meetings_recording_duration_seconds_check "
                "CHECK (recording_duration_seconds >= 0 AND "
                "recording_duration_seconds < 'Infinity'::double precision); "
                "END IF; END $$"
            )
        )


def check_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"Missing table {table.name}; run database migrate")
            actual = {
                column["name"]: column for column in inspector.get_columns(table.name)
            }
            missing = set(table.columns.keys()) - actual.keys()
            if missing:
                raise RuntimeError(
                    f"Missing columns in {table.name}: {sorted(missing)}; "
                    "run database migrate"
                )
            if table.name == "meetings" and any(
                not actual[name]["nullable"]
                for name in (
                    "meeting_date",
                    "meeting_type",
                    "recording_duration_seconds",
                )
            ):
                raise RuntimeError(
                    "Meeting metadata and recording duration must allow NULL; "
                    "run database migrate"
                )
            if table.name == "meetings" and not any(
                constraint["name"] == "meetings_recording_duration_seconds_check"
                for constraint in inspector.get_check_constraints(table.name)
            ):
                raise RuntimeError(
                    "Missing recording duration constraint; run database migrate"
                )


def main() -> None:
    from .archive import export_database

    load_dotenv()
    configure_logging("meetings-database")
    parser = argparse.ArgumentParser(description="Manage the transcript database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("check")
    backup = commands.add_parser("backup")
    backup.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    url = os.environ["DATABASE_URL"]
    logger = logging.getLogger(__name__)
    logger.info("Database %s started", args.command)
    if args.command == "backup":
        archive = export_database(url, args.output_dir)
        logger.info("Database backup completed: %s", archive)
        print(archive, flush=True)
        return
    engine = connect_database(url)
    try:
        if args.command == "migrate":
            migrate_database(engine)
        check_schema(engine)
        logger.info("Database %s completed", args.command)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
