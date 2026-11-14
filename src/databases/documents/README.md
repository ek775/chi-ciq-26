# Documents database

[Database overview](../README.md) | [Document pipeline](../../pipelines/documents/README.md)

Independent PostgreSQL/pgvector database. The upstream `pgvector/pgvector:pg17`
server uses its own credentials and persistent volume. The module Dockerfile
builds migration/check/backup jobs, not the database server.

## Directory mirror

`document_directories` maps `(namespace, relative_path)` to a deterministic table
name, with a parent reference preserving hierarchy. The root path is `.`.
Names use a readable sanitized prefix and a path hash to remain safe PostgreSQL
identifiers (at most 63 ASCII characters), even for spaces, punctuation,
non-ASCII names, and repeated directory basenames. Find exact names in the
registry rather than guessing or interpolating input paths into SQL.

`documents` stores one row per supported file: source key, directory key,
relative path, filename, format, SHA-256, processing signature, full extracted
text, embedding model/dimensions, chunk count, and publication audit time.
Per-directory tables contain `(document_key, sequence)` primary keys, character
offsets, chunk text, and `text_embedding vector`. Foreign keys cascade chunk
deletion when a document is replaced. Empty files have a document record without
chunks; empty directories still have tables. Files removed upstream are retained.

```text
inputs/
  intro.md                  -> root table: intro.md chunks
  Council/
    minutes.txt             -> Council table: minutes.txt chunks
    Committees/             -> Council/Committees table (possibly empty)
```

`migrate` creates pgvector and the registry tables additively under a transaction
lock. The pipeline creates directory tables transactionally as directories are
discovered. `check` validates registry and mapped-table columns. Only completed
per-file transactions become visible; there are no partially published chunks.

## Maintenance

```sh
docker compose --profile documents run --build --rm documents-migrate
docker compose --profile documents run --build --rm documents-migrate \
  chi-ciq-documents-database check
docker compose --profile documents run --build --rm documents-backup
```

Host equivalents: `chi-ciq-documents-database migrate`, `check`, or
`backup --output-dir data/documents-database`. Set `DOCUMENTS_DATABASE_URL`
explicitly; `DATABASE_URL` is only a fallback. The local server is bound to
`127.0.0.1:5434`, database/user `documents`. No meetings storage is reused.

Back up before migration and after successful ingestion. Custom-format dumps
are private, append-only files named `documents-TIMESTAMP-UUID.dump`, published
only after `pg_dump` succeeds. They include directory tables and registry data.
Restore only into a new empty database, never overwrite the live database.
Use the [meetings backup safety guide](../meetings/README.md) for general
PostgreSQL dump/restore procedures, substituting the documents connection/archive.

## Vector queries

Look up the table through `document_directories`, then use the pipeline's
`chunk_table(table_name)` helper to construct SQLAlchemy queries safely:

```python
from sqlalchemy import select
from databases.documents.schema import chunk_table, directories, documents
from shared.embeddings import SentenceTextEmbedder

model = SentenceTextEmbedder()
query_vector = model.embed("consumer protection")
with engine.connect() as connection:
    name = connection.execute(
        select(directories.c.table_name).where(
            directories.c.key == "documents/Transcripts"
        )
    ).scalar_one()
    chunks = chunk_table(name)
    results = connection.execute(
        select(documents.c.relative_path, chunks.c.text)
        .join(documents, chunks.c.document_key == documents.c.key)
        .where(
            documents.c.text_embedding_model == model.model_name,
            documents.c.text_embedding_dimensions == len(query_vector),
        )
        .order_by(chunks.c.text_embedding.cosine_distance(query_vector))
        .limit(10)
    ).all()
```

Search only matching models and dimensions; different embeddings are not
interchangeable. Vectors have unconstrained dimensions as in transcription.
No ANN index is created automatically; queries use exact cosine distance.
Documents and meetings are separate databases; cross-database joins and a
document search web endpoint are not provided.
