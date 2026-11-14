import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from sqlalchemy.engine import URL, make_url


def export_database(
    database_url: str, output_directory: Path, *, prefix: str = "transcription"
) -> Path:
    """Publish a private, append-only archive only after pg_dump succeeds."""
    executable = shutil.which("pg_dump")
    if executable is None:
        raise FileNotFoundError(
            "pg_dump is required; install the PostgreSQL client tools"
        )
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Database export requires a PostgreSQL URL")
    environment = os.environ.copy()
    if url.password is not None:
        environment["PGPASSWORD"] = url.password
    connection_url = URL.create(
        "postgresql",
        username=url.username,
        host=url.host,
        port=url.port,
        database=url.database,
        query=url.query,
    ).render_as_string(hide_password=False)
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not prefix.isascii() or not prefix.replace("-", "").isalnum():
        raise ValueError("Archive prefix must contain only ASCII letters, digits or '-'")
    destination = output_directory / f"{prefix}-{timestamp}-{uuid4().hex}.dump"
    with NamedTemporaryFile(
        dir=output_directory, prefix=f".{prefix}-", suffix=".partial", delete=False
    ) as archive:
        temporary = Path(archive.name)
        try:
            result = subprocess.run(
                [
                    executable,
                    "--dbname",
                    connection_url,
                    "--no-password",
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                ],
                stdout=archive,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"pg_dump failed: {result.stderr.strip()}")
            archive.flush()
            os.fsync(archive.fileno())
            archive.close()
            # link() fails rather than overwriting an existing completed archive.
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination
