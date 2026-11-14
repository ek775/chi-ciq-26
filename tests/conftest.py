import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pipelines.transcription import TranscriptStore, create_database


@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    schema = f"test_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(
            text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        )
    scoped_url = make_url(url).update_query_dict(
        {"options": f"-csearch_path={schema},public"}
    )
    database = create_database(scoped_url.render_as_string(hide_password=False))
    try:
        yield database
    finally:
        database.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def store(engine):
    return TranscriptStore(engine)
