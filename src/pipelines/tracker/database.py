"""Publish tracker batches in a dedicated schema in the analytics database."""

from sqlalchemy import Column, ForeignKey, MetaData, String, Table, delete, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from databases.meetings.schema import connect_database

metadata = MetaData(schema="tracker")
batches = Table(
    "batches", metadata,
    Column("batch_id", String, primary_key=True),
    Column("details", JSONB, nullable=False),
)
statements = Table(
    "statements", metadata,
    Column("batch_id", ForeignKey(batches.c.batch_id, ondelete="CASCADE"), primary_key=True),
    Column("record_id", String, primary_key=True),
    Column("record", JSONB, nullable=False),
)
actions = Table(
    "actions", metadata,
    Column("batch_id", ForeignKey(batches.c.batch_id, ondelete="CASCADE"), primary_key=True),
    Column("record_id", String, primary_key=True),
    Column("record", JSONB, nullable=False),
)


def migrate(url: str) -> None:
    engine = connect_database(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(738491027)")
            connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS tracker")
            metadata.create_all(connection)
    finally:
        engine.dispose()


def publish(url: str, batch_id: str, dataset: dict) -> None:
    if not batch_id.strip():
        raise ValueError("Batch ID must not be blank")
    details = {key: value for key, value in dataset.items() if key not in ("statements", "actions")}
    rows = []
    for table, field, key in (
        (statements, "statements", "statement_id"),
        (actions, "actions", "action_id"),
    ):
        rows.append((table, [
            {"batch_id": batch_id, "record_id": row[key], "record": row}
            for row in dataset[field]
        ]))
    engine = connect_database(url)
    try:
        with engine.begin() as connection:
            # The upsert locks this batch until its records have been replaced.
            connection.execute(
                insert(batches).values(batch_id=batch_id, details=details)
                .on_conflict_do_update(index_elements=[batches.c.batch_id], set_={"details": details})
            )
            for table, records in rows:
                connection.execute(delete(table).where(table.c.batch_id == batch_id))
                if records:
                    connection.execute(table.insert(), records)
    finally:
        engine.dispose()


def load_batch(url: str, batch_id: str) -> dict:
    engine = connect_database(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            details = connection.scalar(select(batches.c.details).where(batches.c.batch_id == batch_id))
            if details is None:
                raise ValueError(f"Tracker batch not found: {batch_id}")
            return {
                **details,
                **{
                    table.name: list(connection.scalars(
                        select(table.c.record).where(table.c.batch_id == batch_id)
                        .order_by(table.c.record_id)
                    ))
                    for table in (statements, actions)
                },
            }
    finally:
        engine.dispose()
