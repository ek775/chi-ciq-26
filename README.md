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
Dependencies are intentionally unpinned; resolve and lock a tested PyTorch,
TorchAudio, and TorchCodec combination before production use.

## Python API

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
  construction, not package import. Pass `device="cuda"` to each adapter only in
  an appropriately configured GPU environment; the supplied container is CPU-first.

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
and drop uniquely named test schemas; the test role needs schema/extension
creation privileges. To include them inside the development container:

```sh
docker compose exec app sh -c 'TEST_DATABASE_URL="$DATABASE_URL" python -m pytest'
```

Tests use synthetic MP4 audio, real FFmpeg/PostgreSQL/pgvector, and deterministic
model stand-ins. They do not download weights or validate recognition accuracy.
