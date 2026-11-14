import argparse
import hashlib
import logging
import os
import re
from pathlib import Path, PurePosixPath

from dotenv import load_dotenv
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine

from databases.meetings.schema import connect_database
from shared.logging import configure_logging

metadata = MetaData()
directories = Table(
    "document_directories",
    metadata,
    Column("key", String, primary_key=True),
    Column("namespace", String, nullable=False),
    Column("relative_path", String, nullable=False),
    Column("parent_key", String, ForeignKey("document_directories.key")),
    Column("table_name", String(63), nullable=False, unique=True),
    UniqueConstraint("namespace", "relative_path"),
)
documents = Table(
    "documents",
    metadata,
    Column("key", String, primary_key=True),
    Column(
        "directory_key", String, ForeignKey("document_directories.key"), nullable=False
    ),
    Column("relative_path", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("format", String, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("processing_signature", String, nullable=False),
    Column("text", String, nullable=False),
    Column("text_embedding_model", String, nullable=False),
    Column("text_embedding_dimensions", Integer),
    Column("chunk_count", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    CheckConstraint("length(content_sha256) = 64"),
    CheckConstraint("chunk_count >= 0"),
    CheckConstraint("text_embedding_dimensions > 0"),
    UniqueConstraint("directory_key", "filename"),
)


def source_key(namespace: str, relative_path: str) -> str:
    if not namespace.strip() or "/" in namespace or "\x00" in namespace:
        raise ValueError("Namespace must be nonempty and contain no slash or NUL")
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != relative_path
        or "\x00" in relative_path
    ):
        raise ValueError("Source paths must be canonical, relative POSIX paths")
    return f"{namespace}/{relative_path}"


def directory_table_name(key: str) -> str:
    label = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")[:16] or "root"
    digest = hashlib.sha256(key.encode()).hexdigest()[:40]
    return f"doc_{label}_{digest}"


def chunk_table(name: str) -> Table:
    if not re.fullmatch(r"doc_[a-z0-9_]{1,16}_[a-f0-9]{40}", name):
        raise ValueError("Invalid document directory table name")
    table_metadata = MetaData()
    # Resolve the FK without including registry tables in directory-table DDL.
    documents.to_metadata(table_metadata)
    return Table(
        name,
        table_metadata,
        Column(
            "document_key",
            String,
            ForeignKey("documents.key", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column("sequence", Integer, primary_key=True),
        Column("start_character", Integer, nullable=False),
        Column("end_character", Integer, nullable=False),
        Column("text", String, nullable=False),
        Column("text_embedding", Vector(), nullable=False),
        CheckConstraint("sequence >= 0"),
        CheckConstraint("start_character >= 0 AND end_character > start_character"),
    )


def lock_key(connection: Connection, key: str) -> None:
    lock_id = int.from_bytes(
        hashlib.sha256(key.encode()).digest()[:8], "big", signed=True
    )
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})


def ensure_directory(
    connection: Connection, namespace: str, relative_path: str
) -> Table:
    key = source_key(namespace, relative_path)
    lock_key(connection, f"directory:{key}")
    name = directory_table_name(key)
    existing = connection.execute(
        select(directories.c.table_name).where(directories.c.key == key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing != name:
            raise RuntimeError(f"Unexpected directory table mapping for {key}")
        return chunk_table(name)
    parent_key = None
    if relative_path != ".":
        parent = str(PurePosixPath(relative_path).parent)
        ensure_directory(connection, namespace, parent)
        parent_key = source_key(namespace, parent)
    table = chunk_table(name)
    table.create(connection)
    connection.execute(
        directories.insert().values(
            key=key,
            namespace=namespace,
            relative_path=relative_path,
            parent_key=parent_key,
            table_name=name,
        )
    )
    return table


def migrate_database(engine: Engine) -> None:
    with engine.begin() as connection:
        lock_key(connection, "documents-schema")
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        metadata.create_all(connection)


def check_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = list(metadata.sorted_tables)
        if inspector.has_table(directories.name):
            tables.extend(
                chunk_table(name)
                for name in connection.execute(
                    select(directories.c.table_name)
                ).scalars()
            )
        for table in tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"Missing table {table.name}; run documents migrate")
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            missing = set(table.columns.keys()) - actual
            if missing:
                raise RuntimeError(
                    f"Missing columns in {table.name}: {sorted(missing)}"
                )


def main(argv: list[str] | None = None) -> None:
    from databases.meetings.archive import export_database

    load_dotenv()
    configure_logging("documents-database")
    parser = argparse.ArgumentParser(description="Manage the documents database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("check")
    backup = commands.add_parser("backup")
    backup.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("DOCUMENTS_DATABASE_URL") or os.environ["DATABASE_URL"]
    logger = logging.getLogger(__name__)
    logger.info("Documents database %s started", args.command)
    if args.command == "backup":
        archive = export_database(url, args.output_dir, prefix="documents")
        logger.info("Documents database backup completed: %s", archive)
        print(archive, flush=True)
        return
    engine = connect_database(url)
    try:
        if args.command == "migrate":
            migrate_database(engine)
        check_schema(engine)
        logger.info("Documents database %s completed", args.command)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
