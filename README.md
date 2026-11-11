# MP4 transcription

A Python library for local Whisper transcription, pyannote speaker diarization,
and direct PostgreSQL/pgvector persistence. There is no JSON serialization step.

## Development setup

1. Copy `.env.example` to `.env`. Set a Hugging Face read token and replace the
   example database password in both places. Use a URL-safe local password.
2. Accept the model conditions for
   [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1).
3. Run `docker compose up --build -d`, or use VS Code **Reopen in Container**.
4. Put MP4 files under `data/mp4/`. File contents and credentials are gitignored.
5. Run:

   ```sh
   docker compose exec app python examples/transcribe.py \
     "data/mp4/2026_november_10_-_committee_on_budget_and_government_operations (240p).mp4"
   ```

The container uses Python 3.12 and installs system FFmpeg and its shared libraries.
Model weights are downloaded on first use and cached in a Docker volume. Inference
is local; Hugging Face is used to download models, not to receive meeting audio.
Review each model's license and conditions before use.

For host development, install FFmpeg, use Python 3.12, run
`python -m pip install -e ".[dev]"`, and start only the database with
`docker compose up -d db`. The example loads `.env` automatically.
PostgreSQL is exposed on `127.0.0.1:5433` for host connections; use port `5433`
in your local `.env` `DATABASE_URL`. Containers still connect to `db:5432`.
Dependencies are intentionally unpinned; resolve and lock a tested PyTorch,
TorchAudio, and TorchCodec combination before production use.

## Python API

### Directory batch and database archive

Process every MP4 directly inside a directory (non-recursive, sorted by filename),
then write a custom-format PostgreSQL archive to the output directory:

```sh
docker compose up --build -d
docker compose exec app python -m examples.batch_transcribe \
  --input-dir data/mp4 --output-dir data/database
```

Or set `TRANSCRIPTION_INPUT_DIR` and `TRANSCRIPTION_OUTPUT_DIR` in `.env` and run:

```sh
docker compose exec app python -m examples.batch_transcribe
```

For host execution, use the same `python -m examples.batch_transcribe` command
from the repository root. Install `pg_dump` and `pg_restore` from PostgreSQL client
tools, with a client major version matching or newer than the server.
The development image includes client version 17 for its PostgreSQL 17 service.

The models load once for the batch. All filenames are validated before processing.
Non-MP4 files are ignored; an empty input directory or invalid filename is an
error. Processing stops on the first failure, preserves previously committed
records, and does not create an archive. A successful batch exports the **entire
connected database**, including prior runs, not just this batch. Rerunning inputs
creates new meeting records; it does not skip existing files.

Both examples emit timestamped progress logs for model loading, audio extraction,
full-file diarization, and each chunk's Whisper, embedding, and database stages.
Chunk logs identify the meeting, speaker, and recording-relative audio offsets.
Each video reports elapsed wall-clock time as `H:MM:SS` and seconds, including
on failure; the batch timer also includes model loading and database export.
Timers use a monotonic clock and do not change stored recording timestamps.
They report at stage boundaries, not as a continuously updating countdown.
Empty Whisper output still raises an error and marks the meeting incomplete;
logging does not skip silent chunks or suppress model warnings.

Archives have unique names such as `transcription-20261111T201500Z-<id>.dump`.
Incomplete exports are removed, and completed archives are not overwritten.
The default `data/database/` directory is gitignored except for its `.gitkeep`.
Other output directories are not automatically ignored; treat archives as
sensitive because they contain transcripts, voice vectors, and source paths.
PostgreSQL remains the live database; the output is a backup, not a database
that can be opened directly as a file.

Restore into a **new, empty** PostgreSQL database on a server with pgvector
installed, using a role allowed to create the extension:

```sh
pg_restore --dbname="$RESTORE_DATABASE_URL" --no-owner --no-acl \
  data/database/transcription-<timestamp>-<id>.dump
```

`RESTORE_DATABASE_URL` is a standard PostgreSQL/libpq URL (`postgresql://...`),
not SQLAlchemy's `postgresql+psycopg://...`. Restoring a client-17 archive to
PostgreSQL 17 or newer is recommended.

### Single-recording library usage

```python
import os
from contextlib import closing

from dotenv import load_dotenv

from chi_ciq_transcription import (
    PyannoteDiarizer,
    PyannoteVoiceEmbedder,
    SentenceTextEmbedder,
    TranscriptStore,
    TranscriptionWorkflow,
    WhisperTranscriber,
    create_database,
)

load_dotenv()
token = os.environ["HF_TOKEN"]
engine = create_database(os.environ["DATABASE_URL"])
try:
    workflow = TranscriptionWorkflow(
        store=TranscriptStore(engine),
        diarizer=PyannoteDiarizer(token),
        transcriber=WhisperTranscriber(language="en"),
        text_embedder=SentenceTextEmbedder(),
        voice_embedder=PyannoteVoiceEmbedder(token),
        max_chunk_seconds=20,
    )
    with closing(workflow.transcribe("data/mp4/2026-11-10_budget.mp4")) as segments:
        for segment in segments:
            # This row is already committed and remains accessible outside a session.
            print(segment.id, segment.speaker_id, segment.text)
finally:
    engine.dispose()
```

`create_database` enables pgvector and creates missing tables. The database role
must have permission to enable the extension, or an administrator must enable it
first. This is initial schema setup, not a schema migration system.

### Iteration and failure semantics

- FFmpeg extracts the first audio track to a temporary mono 16 kHz WAV file.
  Its sample count determines `meetings.recording_duration_seconds`, including
  silence, before diarization begins. Duration remains `NULL` if extraction fails.
- pyannote analyzes the **whole file first**. This is incremental segment
  transcription, not real-time online diarization, and diarization memory use
  depends on recording length.
- Speaker turns are split into chunks of at most `max_chunk_seconds`. Each chunk
  gets Whisper text, a normalized semantic text embedding, and a voice embedding.
  Turns shorter than one second are zero-padded only for voice embedding.
- Each segment and its speaker are committed in one transaction **before yield**.
  Exhausting the iterator marks the meeting `completed`. Errors propagate, and
  failures or explicit iterator closure mark it `incomplete`. Earlier commits
  remain available. Use `closing` when you might stop iteration early.
- Empty transcripts, invalid vectors/timestamps, and text exceeding the embedding
  model's context raise errors rather than silently dropping data. Reduce chunk
  length or choose a larger-context embedding model if needed.
- A new invocation creates a new meeting/run, even for the same file. Automatic
  resume, deduplication, and concurrent writers to the same run are not implemented.
  A process killed without cleanup can leave a meeting `processing`.
- Overlapping diarization turns remain overlapping and can contain another voice;
  resulting voice vectors are not guaranteed to identify a person. Speaker labels
  are local to a meeting, not verified or globally matched identities.
- Default adapters target the pyannote 4 API. Model loading is lazy at adapter
  construction, not package import. Both examples check `torch.cuda.is_available()`
  once at run setup, print the selected device, and pass it to all four adapters.
  They use CUDA when available and CPU otherwise. GPU model-loading errors
  propagate rather than silently retrying on CPU. Direct library callers can
  pass `device="cuda"` explicitly; adapter defaults remain CPU.

### NVIDIA GPU access in Docker

The single Compose configuration exposes all NVIDIA GPUs to the app container.
It requires NVIDIA drivers and NVIDIA Container Toolkit configured for Docker,
as well as CUDA-compatible PyTorch. Start it with the normal command:

```sh
docker compose up --build -d
docker compose exec app python -c \
  'import torch; print("CUDA available:", torch.cuda.is_available())'
```

Then start a new single-file or batch run normally. Existing Python processes
do not switch devices mid-run. Enabling GPU access recreates the app container,
so let an active transcription finish first. If Docker reports
`could not select device driver ... with capabilities: [[gpu]]`, install
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on the host and configure its Docker runtime. Docker must support GPU requests
before the container can start; the Python CPU fallback applies only after startup.
There is no separate GPU configuration or startup option.

## Filename metadata

Supported examples:

- `2026_november_10_-_committee_on_budget_and_government_operations (240p).mp4`
- `2026-11-10_committee_on_budget_and_government_operations.mp4`

Both produce the date `2026-11-10` and meeting type
`committee on budget and government operations`. The original filename and source
path are also stored. Invalid or missing dates/types raise `ValueError`.

## Relational schema

```text
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
cascades to its speakers and segments. Speakers can have many transcript segments.
`recording_duration_seconds` is the full decoded audio track length, not elapsed
processing time or the sum of speech segments. `start_seconds` (inclusive) and
`end_seconds` (exclusive) are offsets from the start of that audio track, not
wall-clock times or word-level Whisper timestamps. The store requires duration
before appending segments and rejects end offsets beyond it. Direct store callers
must call `set_recording_duration(meeting_id, duration_seconds)` before `append`.
`created_at` and `updated_at` remain timezone-aware database audit times only;
they are never used to locate audio.
The filename supplies a date only; no meeting start time or timezone is invented.

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
from chi_ciq_transcription import TranscriptSegment

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

## Tests

```sh
python -m pytest
python -m ruff check src tests examples
python -m ruff format --check src tests examples
```

PostgreSQL integration tests skip unless `TEST_DATABASE_URL` is set. They create
and drop uniquely named test schemas; dump/restore tests also create and drop a
uniquely named database. The test role needs schema/extension and database creation
privileges. Dump/restore tests require `pg_dump` and `pg_restore`. To include them
inside the development container:

```sh
docker compose exec app sh -c 'TEST_DATABASE_URL="$DATABASE_URL" python -m pytest'
```

Tests use synthetic MP4 audio, real FFmpeg/PostgreSQL/pgvector, and deterministic
model stand-ins. They do not download weights or validate recognition accuracy.
