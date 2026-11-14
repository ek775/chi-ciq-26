import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, inspect, text

from .models import Base


def connect_database(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("This library requires PostgreSQL with pgvector")
    return engine


def migrate_database(engine: Engine) -> None:
    """Apply only additive changes, serialized across ingestion/deployment jobs."""
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(738491025)"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(connection)
        connection.execute(
            text(
                "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS "
                "recording_duration_seconds double precision "
                "CHECK (recording_duration_seconds >= 0 AND "
                "recording_duration_seconds < 'Infinity'::double precision)"
            )
        )


def check_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"Missing table {table.name}; run database migrate")
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            missing = set(table.columns.keys()) - actual
            if missing:
                raise RuntimeError(
                    f"Missing columns in {table.name}: {sorted(missing)}; "
                    "run database migrate"
                )


def main() -> None:
    from .archive import export_database

    load_dotenv()
    parser = argparse.ArgumentParser(description="Manage the transcript database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("check")
    backup = commands.add_parser("backup")
    backup.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    url = os.environ["DATABASE_URL"]
    if args.command == "backup":
        print(export_database(url, args.output_dir), flush=True)
        return
    engine = connect_database(url)
    try:
        if args.command == "migrate":
            migrate_database(engine)
        check_schema(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
