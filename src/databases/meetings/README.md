# Meetings database

[Database overview](../README.md) | [Transcription](../../pipelines/transcription/README.md) | [Web](../../web/README.md)

Run shell examples from the repository root, not this module directory.

## Maintenance commands

The server uses the upstream `pgvector/pgvector:pg17` image. This module's
[Dockerfile](Dockerfile) builds maintenance jobs without inference dependencies.

```sh
chi-ciq-database migrate
chi-ciq-database check
chi-ciq-database backup --output-dir data/database
```

Use `DATABASE_URL` for the intended database. Compose supplies container
connections at `db:5432`; host connections use `127.0.0.1:5433`.
`create_database` creates missing tables but does not migrate existing ones.
The migration command serializes additive changes and checks compatibility.

## Database durability

The live database must use persistent storage, separate from worker scratch and
model caches. Backups are separate jobs, not database overwrites:

```sh
chi-ciq-database backup --output-dir data/database
```

Each successful `pg_dump` creates a private, uniquely named archive. Completed
archives are never overwritten, including on a name collision; failed exports
remove only their own partial file. Back up before migration and after successful
ingestion. An archive is not a substitute for durable live PostgreSQL storage.
Restore only into a new empty database as described below; no pipeline runs
`pg_restore --clean` against the live database.

Migrations serialize through a database advisory lock, create missing tables,
and relax meeting date/type nullability for opaque inputs. They preserve legacy
data and leave previously unknown recording durations
NULL. Web releases check compatibility rather than mutating the database.
For multiple independent databases, give each database its own persistent
volume, connection secret, and backup destination; never share a PostgreSQL data
directory between database containers.

## Restore into a new database

Restore into a **new, empty** PostgreSQL database on a server with pgvector
installed, using a role allowed to create the extension:

```sh
pg_restore --dbname="$RESTORE_DATABASE_URL" --no-owner --no-acl \
  data/database/transcription-<timestamp>-<id>.dump
```

`RESTORE_DATABASE_URL` is a standard PostgreSQL/libpq URL (`postgresql://...`),
not SQLAlchemy's `postgresql+psycopg://...`. Restoring a client-17 archive to
PostgreSQL 17 or newer is recommended. Install matching or newer PostgreSQL
client tools. Archives contain transcripts, voice vectors, and source paths;
treat them as sensitive. Other output directories are not automatically
gitignored.

## Recording metadata

Filenames identify source recordings only. Transcription does not infer a date
or meeting type from them, even when they look dated. `meeting_date` and
`meeting_type` are nullable; unknown values are stored as SQL NULL. Analytics
derives missing metadata from transcript content in its own database. The web
transcript browser displays undated recordings using their source filenames.

Run `chi-ciq-database migrate` before deploying these workers to an existing
database: it drops the old NOT NULL constraints without removing existing values.
The schema check rejects older constraints with an explicit migration instruction.

The legacy filename parser remains available for explicitly reviewed overrides.

Supported examples:

- `2026_november_10_-_committee_on_budget_and_government_operations (240p).mp4`
- `2026-11-10_committee_on_budget_and_government_operations.mp4`

When explicitly parsed, both produce the date `2026-11-10` and meeting type
`committee on budget and government operations`. The original filename and source
path are also stored. Invalid or missing dates/types raise `ValueError`.

## Relational schema

```text
published_sources
  key (namespace/filename), content_sha256, meeting_id, updated_at
    |
    +-- current completed meeting (one pointer per ingested input)

meetings (one run)
  id, source_path, filename, meeting_date, meeting_type, status, recording_duration_seconds
  transcription_model, diarization_model, text_embedding_model, voice_embedding_model
  text_embedding_dimensions, voice_embedding_dimensions, created_at, updated_at
    |
    +-- speakers (many): id, meeting_id, label, created_at
    |
    +-- transcript_segments (many)
          id, meeting_id, speaker_id, sequence, text
          start_seconds, end_seconds, text_embedding, voice_embedding, created_at
```

Foreign keys enforce speaker ownership within the meeting. Meeting deletion
cascades to its speakers and segments. A published meeting cannot be deleted
until its source pointer is updated; the ingestion pipeline does this in the
same transaction as replacement. Speakers can have many transcript segments.
`recording_duration_seconds` is the full decoded audio track length, not elapsed
processing time or the sum of speech segments. `start_seconds` (inclusive) and
`end_seconds` (exclusive) are offsets from the start of that audio track, not
wall-clock times. Segment boundaries now come from the first and last assigned
Whisper words, rounded to audio samples; individual words are not stored.
The store requires duration
before appending segments and rejects end offsets beyond it. Direct store callers
must call `set_recording_duration(meeting_id, duration_seconds)` before `append`.
`created_at` and `updated_at` remain timezone-aware database audit times only;
they are never used to locate audio.
Explicit overrides supply a date only; no meeting start time or timezone is invented.

For databases created before the duration field was added, apply this schema
change before using the updated library (`create_database` does not alter tables):

```sql
ALTER TABLE meetings ADD COLUMN recording_duration_seconds double precision
    CHECK (recording_duration_seconds >= 0
           AND recording_duration_seconds < 'Infinity'::double precision);
```

Existing rows remain `NULL`; backfill their duration from the original audio,
not the last transcript segment (which may omit trailing silence).

Vector dimensions are recorded and checked per meeting. The schema permits
different models across meetings, so search only within a matching embedding model
and dimension. The initial template uses exact pgvector search without an ANN
index; add dimension-specific indexes/migrations when the model choice is fixed.

Example semantic search (use a query vector from the meeting's text model):

```python
from sqlalchemy import select
from sqlalchemy.orm import Session
from pipelines.transcription import TranscriptSegment

with Session(engine) as session:
    matches = session.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting_id)
        .order_by(TranscriptSegment.text_embedding.cosine_distance(query_vector))
        .limit(10)
    ).all()
```

Use `voice_embedding.cosine_distance(...)` for voice similarity with the same
voice model; smaller distances indicate greater similarity, not verified identity.
