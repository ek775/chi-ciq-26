"""Apply the Flyway SQL files to a test database without the Flyway program.

The integration tests need the schema that Flyway makes, in a private test
schema, without Java. This applies the same files in Flyway's order: versioned
migrations by version number, then repeatable migrations by name.
"""

from pathlib import Path

from sqlalchemy.engine import Engine

MIGRATION_ROOT = Path(__file__).resolve().parents[1] / "src/databases"


def migration_version(path: Path) -> tuple[int, ...]:
    """Return (1, 2) for V1_2__name.sql and (10,) for V10__name.sql."""
    version_text = path.name[1:].split("__", 1)[0]
    return tuple(int(part) for part in version_text.replace("_", ".").split("."))


def migration_files(database_name: str) -> list[Path]:
    directory = MIGRATION_ROOT / database_name / "migrations"
    versioned = sorted(directory.glob("V*__*.sql"), key=migration_version)
    repeatable = sorted(directory.glob("R__*.sql"))
    return [*versioned, *repeatable]


def apply_migrations(engine: Engine, database_name: str) -> None:
    with engine.begin() as connection:
        for path in migration_files(database_name):
            connection.exec_driver_sql(path.read_text())
