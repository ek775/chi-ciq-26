# Chicago meeting ingestion and web services

Database ingestion/update pipelines and a read-only transcript browsing/search
server, with separate container boundaries and a draft Fuzzball topology. Local Whisper
transcription, pyannote diarization, and PostgreSQL/pgvector persistence remain
available as a Python library. Audio inference stays local to the worker.

## Service architecture

```text
future raw acquisition pipelines
             |
      persistent MP4 inputs
             |
      ingestion/update worker ----> PostgreSQL + pgvector
                                         |          |
                                  database jobs   web API + UI
                                  migrate/backup   read-only
                                         |
                                 append-only archives
```

The source tree mirrors the expected container boundaries:

```text
src/
  pipelines/
    downloads/       public Vimeo acquisition, upstream of transcription
    transcription/   implemented: initial build and periodic updates
    documents/       reserved: not implemented
    analytics/       reserved: not implemented
  web/               implemented: transcript UI/API
  vllm/              upstream image; deployment boundary only
  databases/
    meetings/        PostgreSQL/pgvector contract and maintenance jobs
    documents/       reserved: not implemented
```

Source ownership is explicit:

- `src/pipelines/transcription/`: content-based idempotency, staging/publication, and
  inference model setup, audio adapters, and the streaming inference algorithm.
  This is the only custom service requiring ML dependencies.
- `src/databases/meetings/`: the shared relational contract, persistence,
  additive schema migration, and PostgreSQL archive export.
- `src/web/`: FastAPI browsing/search endpoints and a small browser UI.
  It neither imports the inference package nor loads models.
- `src/databases/meetings/metadata.py`: shared filename metadata parsing.

Python imports now use `pipelines.transcription`, `databases.meetings`, and `web`.
The former `transcription` and `chi_ciq` package paths have been removed to keep
the top-level source layout container-oriented; examples, tests, package entry
points, and image build inputs use the new paths. The pipeline re-exports the
meetings persistence contract without duplicating its implementation.

The reserved modules contain only namespace placeholders, not invented
schemas, fake services, or runnable containers. Document initial-build/update
pipelines and analytics remain out of scope.

PostgreSQL and optional vLLM use upstream images. Ingestion, web, database
maintenance, and the development shell have separate Dockerfiles. An ingestion
job never runs a second PostgreSQL process against the live database's data
directory, and a web release never replaces the database container or volume.
Each custom service's Dockerfile lives in its source directory and uses the
repository root as its build context. `src/databases/meetings/Dockerfile` builds
maintenance jobs; the PostgreSQL server itself uses `pgvector/pgvector:pg17`.
vLLM uses its upstream image; `src/vllm` is deliberately excluded from Python
package discovery so it cannot shadow the installed upstream `vllm` package.

The package's base dependencies cover persistence. Install `.[ingestion]` for
ML inference, `.[web]` for the server, or `.[dev,web]` for lightweight tests.
`.[database]` explicitly selects the database-maintenance service without ML.

### Local container entry points

The original `app` development shell and `db` service names remain available.
Additional opt-in services have separate images:

```sh
docker compose up -d db
docker compose --profile pipelines run --build --rm meetings-backup
docker compose --profile pipelines run --build --rm meetings-migrate
docker compose --profile pipelines run --build --rm transcription
docker compose --profile pipelines run --build --rm meetings-backup
docker compose --profile web up --build -d web
# UI: http://127.0.0.1:8080/
```

Select these services explicitly rather than starting every pipeline-profile
service together: backup-before-migration and backup-after-ingestion are ordered
operations. The transcription job exits after processing its inputs. Repeated
submissions build initially and update subsequently; no polling daemon or
schedule is implemented in the worker. Its current local GPU configuration
matches the development shell's NVIDIA requirements.

### Fuzzball draft and deferred delivery

`fuzzball.draft.yaml` records container ownership, storage boundaries, initial
build/update sequences, and web release stages. It is an **architecture draft,
not an executable Fuzzfile or a validated Fuzzball schema**. Do not submit it.
Registry images, persistent volume bindings, secrets, private ingress, scheduler,
and cluster-compatible YAML must be resolved when hackathon networking is
available. No Fuzzball deployment or CI/CD pipeline is claimed to be operational.
The draft explicitly keeps database lifetimes independent of finite pipeline
jobs and replaceable web releases.

### Public Vimeo acquisition

The moved ID list is packaged at `src/pipelines/downloads/vimeo-ids.txt`.
`chi-ciq-download-vimeo` uses it by default when no positional IDs or explicit
`--batch-file` are supplied. The moved `download-vimeo.py` path remains a wrapper;
the importable implementation is `pipelines.downloads.vimeo`.

```sh
docker compose --profile downloads run --build --rm downloads
# Once acquisition succeeds, prepare the worker fan-out:
chi-ciq-dispatch --input-dir data/mp4 --output-dir data/dispatch
```

The downloads container uses CPU, FFmpeg/ffprobe and `.[downloads]` (yt-dlp),
without inference models or database access. It shares `data/mp4` with the
transcription input mount. It downloads only public Vimeo recordings, retaining
the original 240p-or-lower selection and optional `--best-audio`.
`--dry-run` contacts Vimeo and reports formats but publishes no recording; it
is **not** an offline test.

Vimeo titles must contain the actual meeting date and type in the existing
filename format. Whitespace is normalized to underscores; missing meeting
metadata is an error, not inferred from the upload date. For titles that do not
conform, supply `--metadata-file` containing a JSON object such as
`{"1234158910": "2026-11-10_committee_on_budget.mp4"}`. Mount this file into the
downloads container and pass its container path. All mappings are validated
before downloading, and two IDs cannot own the same output filename.

Downloads stay in private scratch subdirectories until yt-dlp succeeds and
ffprobe confirms both audio and video. Only then is the complete MP4 atomically
published; the dispatcher ignores scratch subdirectories. A `.vimeo/` receipt
records each ID's stable filename and SHA-256. Repeated acquisition skips managed
files matching their receipts. `--refresh` explicitly redownloads managed files
and atomically replaces them, leaving the previous file intact on download or
media-validation failure. Unmanaged files and files owned by another ID are
never overwritten. Invalid receipts fail visibly, rather than treating arbitrary
nonempty files as completed downloads.
Locally modified managed files require `--refresh`; they are not silently
overwritten merely because their checksum differs.

The output filesystem must support POSIX locks and atomic rename. Acquisitions
sharing this output are serialized; start dispatch only after the acquisition
command succeeds. Failures are logged with video IDs, the batch continues, and
the command exits nonzero if any failed. Previously successful files remain.
No Vimeo network download or Fuzzball cluster execution was tested during the
hackathon networking restriction.

### Transcription fan-out

`chi-ciq-dispatch` runs on the submitter, not inside a long-running inference
container. It discovers recordings and prepares **one independent Fuzzball
workflow per complete file**, all using the same transcription image. Fuzzball
chooses worker nodes; no local process pool or node list is used.

Each assigned worker runs Whisper, streaming diarization, and embeddings for
its own recording, then writes directly to the shared meetings database.
Recording-level distribution preserves local speaker labels and audio offsets;
it does not split one recording's diarization across nodes.

Build/push the transcription image to a registry reachable by worker nodes when
networking is available. Configure `TRANSCRIPTION_WORKER_IMAGE` with its immutable
tag or digest. Prepare a plan now without contacting Fuzzball:

```sh
chi-ciq-dispatch --input-dir data/mp4 \
  --image docker://your-registry/chi-ciq-transcription:commit-tag \
  --output-dir data/dispatch
```

Every run gets a new private directory containing `plan.json`. Jobs include the
exact command argv, source identity, SHA-256, and container input path. File
names with spaces stay single arguments; no shell interpolation is performed.
The inference image is built from `src/pipelines/transcription/Dockerfile` with
the repository root as context. The submitter needs the base Python package and
Fuzzball CLI, not Torch, models, a GPU, or a database connection.

File availability must be provided by the cluster, **not** by assuming the
submitter's local path exists on every node:

- For already-uploaded inputs, set `TRANSCRIPTION_INPUT_BASE_URI` to an `s3://`
  or `https://` directory. The dispatcher percent-encodes each filename and
  provides `${input.uri}` for Fuzzball's input-ingestion configuration.
- Alternatively, the Fuzzfile template must mount cluster-wide input storage
  read-only at `/inputs` (override with `--worker-input-dir`).
- The dispatcher does not upload files, download recordings, or embed storage
  credentials. Local discovery establishes the expected byte hash; remote or
  mounted files must match it. Workers reject changed/stale content before
  inference or publication, including changes while making their snapshot.

Before actual submission, supply a **cluster-specific Fuzzfile JSON template**
using the supported Fuzzball schema. JSON is valid YAML, and rendered workflows
are saved as individual `.json` files. The template must use these placeholders
as entire JSON values, never inside shell strings:

| Placeholder | Rendered value |
|---|---|
| `${worker.image}` | Transcription container image |
| `${worker.command}` | Complete command argument array |
| `${job.name}` | Per-source/content job name |
| `${input.path}` | Assigned container filename |
| `${input.uri}` | Remote input URI, when configured |
| `${input.filename}` | Original filename |
| `${input.sha256}` | Expected content digest |
| `${source.key}` | Stable namespace/filename identity |

For example, use `"${worker.command}"` as the value of the cluster schema's
command field; rendering replaces it with a JSON array. This is a substitution
contract, not a guessed Fuzzball schema. Bind GPU/CPU/memory limits, input staging
or shared-volume mounts, model caches, and `DATABASE_URL`/`HF_TOKEN` cluster
secret references in that template. Do not include literal credentials.
`fuzzball.draft.yaml` is not usable as this template.

```sh
# Render workflows offline after supplying your cluster-specific template:
chi-ciq-dispatch --input-dir data/mp4 --output-dir data/dispatch \
  --template /path/to/cluster-worker.json

# When networking and the logged-in Fuzzball context are ready:
chi-ciq-dispatch --input-dir data/mp4 --output-dir data/dispatch \
  --template /path/to/cluster-worker.json --submit --max-jobs 32
```

Submission explicitly invokes `fuzzball workflow validate` for **all** generated
workflows before starting any with `fuzzball workflow start`; the CLI executable
can be overridden with `FUZZBALL_BIN` or `--fuzzball`. Default operation only
prepares files, without invoking the CLI. `--max-jobs` caps jobs submitted by
one invocation; it is not a cluster-wide concurrency limit. Starts do not wait,
so Fuzzball can schedule recordings concurrently. Configure actual concurrency
through cluster quotas/resources.

Per-job CLI receipts and an fsynced `submissions.jsonl` persist accepted and
failed submission attempts. If a later submission fails, earlier workflows are
left running, not silently cancelled or automatically resubmitted. Inspect
receipts and workflow status before retrying: submission is not completion.
Wait for every workflow to succeed before running the post-batch backup;
database schema migration and backups are not duplicated in each worker.

Workers invoke `chi-ciq-ingest --input-file <assigned-path> --expected-sha256
<hash> --namespace recordings`; they never scan and process every file mounted
on their node. `--force` on the dispatcher propagates to each worker. Same-source
database locks and atomic publication still protect overlapping/retried jobs;
different recordings can write concurrently. Both initial builds and periodic
updates use this fan-out; external scheduling and cluster submission remain
deferred until networking is available.

## Ingestion versus updates

After configuring `DATABASE_URL`, initialize or additively migrate the schema:

```sh
chi-ciq-database migrate
chi-ciq-database check
chi-ciq-ingest --input-dir data/mp4 --namespace recordings
```

Use these installed commands in the corresponding service containers. The same
ingestion command handles both initial loading and subsequent updates:

- Input identity is **namespace + filename**, independent of container mount
  paths. Keep the namespace stable across jobs; different source collections
  should use distinct namespaces. Renaming a file creates another identity.
- SHA-256 of the input bytes determines whether to skip it. Skipping an unchanged
  input does not load inference models. Models otherwise load once per batch.
- Changed files are copied to private scratch space; the hash represents exactly
  the snapshot transcribed, even if the acquisition pipeline later modifies the
  original. Acquisition jobs should publish complete raw files atomically.
- New segments commit into a staged meeting. The previous published run remains
  readable during processing. Only successful completion publishes the new run
  and deletes the previous published run and its dependent rows, in one
  transaction. Failed/incomplete staged runs remain for diagnostics but are
  never served by the web API.
- A PostgreSQL session advisory lock serializes workers for the same identity.
  A terminated worker releases its lock when its connection closes. A retry
  reprocesses a failed input; this is not partial-segment resume.
- `--force` reprocesses unchanged bytes, for example after changing models.
  It uses the same staging and atomic replacement rules.
- Missing files do **not** delete database records. Each successful file is its
  own update transaction, not an all-or-nothing directory transaction.

The legacy examples deliberately retain their append-a-new-run behavior.
Existing standalone runs and restored archives are preserved by migrations;
they are not silently deleted or automatically published. Run the ingestion
pipeline on their raw inputs to establish published source identities. Failed
stages may accumulate and need an explicit retention policy.

Public Vimeo acquisition is provided by `pipelines.downloads`; other raw
document/video acquisition remains an upstream handoff, not implemented here.
Current transcription ingestion accepts complete, filename-validated MP4 files directly inside
the input directory (non-recursive). It does not claim to ingest arbitrary
documents. Vimeo downloading is a separate acquisition service.

## Web browsing and search

```sh
chi-ciq-web
# http://127.0.0.1:8080/
```

The server binds port 8080 and requires an already migrated database.
`/health` checks connectivity, `/ready` checks the schema, and `/docs` describes
the API:

- `GET /api/meetings?q=budget&limit=50&offset=0`
- `GET /api/meetings/{id}`
- `GET /api/meetings/{id}/segments?q=comment&limit=100&offset=0`
- `GET /api/stats`

Search is literal, case-insensitive substring search over meeting types and
transcript text, not semantic vector search. Vector data remains available for
separately designed semantic consumers. Only published completed meetings are
visible; raw paths, credentials, and voice/text embeddings are not returned.
Responses are paginated (maximum 200 rows). Web transactions are read-only and
use repeatable-read snapshots so an update cannot split one request across runs.
The UI renders transcript text as text, not HTML, and reports request failures.

Use a separate database login with SELECT-only grants for the web service.
Neither the UI nor API implements application authentication; restrict
production access with Fuzzball's private endpoint/token mechanism or an
authenticated gateway. Do not expose this transcript/voice dataset publicly by
default.

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

Migrations serialize through a database advisory lock and make additive changes
only. They preserve legacy data and leave previously unknown recording durations
NULL. Web releases check compatibility rather than mutating the database.
For multiple independent databases, give each database its own persistent
volume, connection secret, and backup destination; never share a PostgreSQL data
directory between database containers.

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
`python -m pip install -e ".[dev,ingestion,web]"`, and start only the database with
`docker compose up -d db`. The example loads `.env` automatically.
PostgreSQL is exposed on `127.0.0.1:5433` for host connections; use port `5433`
in your local `.env` `DATABASE_URL`. Containers still connect to `db:5432`.
Dependencies are intentionally unpinned; resolve and lock a tested PyTorch,
TorchAudio, and TorchCodec combination before production use.

## Optional AI inference server

A separate `vllm` container serves OpenAI-compatible text completion and chat
completion endpoints for future pipelines. It uses the official, version-pinned
vLLM image; it does not install vLLM into the transcription container or change
the current transcription workflow.

Set `VLLM_MODEL` in `.env` to a supported Hugging Face text-generation model with
a chat template. The default in `.env.example`, `Qwen/Qwen2.5-0.5B-Instruct`, is a
small chat model for initial integration, not a production-quality recommendation.
`HF_TOKEN` is passed through for gated models; accept their terms before use.
Weights are downloaded on first startup and kept in a separate Docker volume.

The service requires a working NVIDIA driver, NVIDIA Container Toolkit, and
enough GPU memory for the selected model. `VLLM_MAX_MODEL_LEN` defaults to 4096
tokens and `VLLM_GPU_MEMORY_UTILIZATION` to 0.5 (a fraction of total GPU memory).
Adjust these in `.env` for your hardware. Transcription also uses the GPU, so
these settings do not guarantee both workloads fit; stop one if memory is tight.
The `ai` profile keeps vLLM out of normal development startup:

```sh
docker compose --profile ai up -d vllm
docker compose logs -f vllm
```

First startup can take several minutes to download and load the model. Wait for
`docker compose --profile ai ps vllm` to show healthy, then check and submit a
request:

```sh
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/v1/models
curl --fail-with-body http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"local-ai","messages":[{"role":"user","content":"Say hello."}],"max_tokens":64}'
```

Use base URL `http://vllm:8000/v1` from `app` or another container on the same
Compose network, and `http://127.0.0.1:8000/v1` from the host. Request model name
`local-ai` is a stable alias even when `VLLM_MODEL` changes. The default supports
`/v1/chat/completions` and `/v1/completions`, not embeddings or audio APIs.
Clients that require an API key can use a non-secret placeholder such as `local`.
There is no authentication configured: the host port binds only to loopback,
but other containers on the Compose network can access it. Do not expose this
development service to untrusted users or networks without adding authentication.
Requests are processed locally; Hugging Face only supplies model downloads.

After changing model settings, rerun the startup command to recreate the service.
Stop it with `docker compose --profile ai stop vllm` to release GPU memory; cached
weights are retained.

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
streaming diarization, and each chunk's Whisper, embedding, and database stages.
Chunk logs identify the meeting, speaker, and recording-relative audio offsets.
Each video reports elapsed wall-clock time as `H:MM:SS` and seconds, including
on failure; the batch timer also includes model loading and database export.
Timers use a monotonic clock and do not change stored recording timestamps.
They report at stage boundaries, not as a continuously updating countdown.
Whisper windows returning no words emit a warning and produce no segment; the
meeting continues. Model exceptions and malformed word timestamps still fail
the meeting. Model warnings are not suppressed.

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

from pipelines.transcription import (
    PyannoteVoiceEmbedder,
    SentenceTextEmbedder,
    StreamingPyannoteDiarizer,
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
        diarizer=StreamingPyannoteDiarizer(token),
        transcriber=WhisperTranscriber(language="en"),
        text_embedder=SentenceTextEmbedder(),
        voice_embedder=PyannoteVoiceEmbedder(token),
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
- `StreamingPyannoteDiarizer` uses the community-1 **segmentation and embedding
  checkpoints directly**, not its full-file `Pipeline`. It analyzes overlapping
  native model windows (currently 10 seconds), publishing each central 5-second
  interval once, with approximately 2.5 seconds of future audio context.
  Beginning/end context is zero-padded; padded speech is not used for identification.
  This is incremental processing of an extracted WAV, not microphone/network input.
- Speaker identities are assigned online using normalized embedding centroids,
  a one-to-one cosine-distance match (default threshold `0.35`), and new clusters
  when no existing speaker matches. Labels remain fixed after publication; they
  reset for each recording. At least one second of clean, non-overlapping speech
  in the context window is required to identify a local speaker. Other speech
  is retained as `UNKNOWN` with warnings. There is no speaker-cluster limit;
  unmatched voices create new labels without a capacity-exhaustion failure.
  Configure `distance_threshold` on the diarizer if needed. Fragmented identities
  are retained for later post-hoc analysis of stored voice embeddings; automatic
  post-hoc merging is not implemented.
- This replaces community-1's offline VBx clustering with a simpler online
  algorithm. **The same checkpoints do not imply equivalent diarization accuracy.**
  Thresholds need evaluation on representative meetings; short and overlapping
  speech can be harder to identify. `PyannoteDiarizer.diarize()` remains available
  for standalone offline analysis, but is not a streaming workflow backend.
- A worker continuously produces diarized chunks while the calling thread runs
  Whisper, embeddings, and database commits. A **two-item bounded queue** applies
  backpressure: when full, the worker waits rather than analyzing more audio.
  The queue carries offsets/turns, not full audio arrays. Each thread owns its WAV
  reader, and each model instance is called serially. Do not share a workflow's
  adapters between concurrent recordings.
- Whisper receives recording-aligned audio windows, not individual speaker turns.
  `max_chunk_seconds=None` now selects a bounded **60-second streaming batch**,
  rather than a whole-recording call. Explicit finite durations of at least one
  audio sample remain supported **without a 30-second upper limit**. Changing this
  batch duration does not change the diarizer's native model context. The final
  batch can be shorter. Silent chunks still advance the stream but skip Whisper.
  Tiny speaker turns therefore do not become tiny standalone Whisper inputs.
- Whisper itself still uses sliding 30-second model windows internally; removing
  the workflow limit does not change its native context size. Inference buffers
  are bounded by model context and the configured batch duration, not recording
  length. Clustering state is separate: one centroid and counter per discovered
  cluster, with no fixed memory ceiling. Clustering memory and matching work grow
  with cluster count, including erroneous splits of the same person.
  There can be two queued chunks, one producer chunk waiting
  to enqueue, and one consumer chunk being processed. Larger batch durations
  increase memory use and time to the first commit; concurrent CUDA inference
  can still exhaust device memory. No silent sequential or CPU retry is performed.
- Closing the iterator or a consumer error cancels queue waits and joins the
  worker **before** deleting temporary audio. Worker exceptions propagate after
  previously queued valid chunks, preserving committed rows. A truncated, invalid,
  or out-of-order stream fails rather than completing successfully. Running native
  inference cannot be forcibly interrupted by a Python thread; shutdown waits for
  that call to return. If critical memory/runtime problems occur, use separate
  Fuzzball-managed inference containers.
- Whisper word timestamps are assigned to the speaker with the greatest total
  diarization overlap for each word. Ties use the first speaker in sorted turn
  order. Words without any overlap are retained as `UNKNOWN` with a warning.
  Consecutive words assigned to the same speaker form one stored segment within
  each window; long monologues continue in subsequent windows. Words are not
  duplicated to represent overlapping speakers.
- Each segment gets a normalized semantic text embedding and a voice embedding
  from its word-aligned audio span. Spans shorter than one second are zero-padded
  only for voice embedding. Long text is split into embedding-model-sized pieces
  without truncation; their normalized vectors are token-count-weighted and
  averaged, then normalized again. A single word exceeding the embedding context
  or an invalid pooled vector remains an error.
- Custom workflow transcribers must implement
  `transcribe_words(audio) -> list[TranscribedWord]`, with ordered, finite,
  window-relative word offsets and original word spacing. The default
  `WhisperTranscriber.transcribe(audio) -> str` remains available for standalone
  use. Zero-duration words are retained with a one-sample audio span.
- Custom workflow diarizers must implement
  `diarize_chunks(audio_path, chunk_frames) -> Iterable[DiarizedChunk]`.
  Each chunk has integer audio-sample `start`/`end` offsets and a list of
  recording-relative `SpeakerTurn`s clipped to that interval. Chunks must cover
  the WAV contiguously from zero through EOF, including silence, and never exceed
  `chunk_frames`. Emit finalized results incrementally, not a full-file list.
- Each segment and its speaker are committed in one transaction **before yield**.
  Exhausting the iterator marks the meeting `completed`. Errors propagate, and
  failures or explicit iterator closure mark it `incomplete`. Earlier commits
  remain available. Use `closing` when you might stop iteration early.
- Empty Whisper windows are explicitly reported and skipped, including windows
  where diarization detected speech but Whisper found none. A fully processed
  meeting can therefore complete without transcript rows. Invalid word output,
  vectors/timestamps, and model failures still raise errors; empty transcript
  rows are never stored.
- A new invocation creates a new meeting/run, even for the same file. Automatic
  resume, deduplication, and concurrent writers to the same run are not implemented.
  A process killed without cleanup can leave a meeting `processing`.
- Overlapping diarization turns can contain another voice; assigning a word to
  the greatest-overlap speaker does not separate mixed audio. Resulting voice
  vectors are not guaranteed to identify a person. Speaker labels are local to
  a meeting, not verified or globally matched identities.
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
model stand-ins. Default tests do not download weights or validate recognition
accuracy. Streaming tests verify sustained overlap beyond the first two batches,
two-item queue backpressure, speaker-slot permutation tracking, overlap identity
constraints, final partial chunks, per-recording reset, worker errors and cleanup.

## Streaming inference options investigated

- [community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1):
  its standard pipeline returns full-file annotations, not finalized streaming turns.
  Moving that same call into a Hugging Face hosted endpoint would not make its
  algorithm streaming and would send meeting audio off-machine.
- [pyannote Model/Inference API](https://github.com/pyannote/pyannote-audio/blob/main/src/pyannote/audio/core/inference.py):
  selected here. `Model.from_pretrained(..., subfolder="segmentation", token=...)`
  and the bundled `embedding` subfolder load the existing weights. `Inference.infer`
  handles evaluation/device transfer and powerset-to-multilabel conversion.
  An online clustering layer replaces the offline global clustering step.
- [Diart](https://github.com/juanmc2005/diart): offers overlap-aware online
  clustering, but release 0.9.2 still uses `use_auth_token` in its built-in loader
  and requires NumPy below 2, conflicting with this project's pyannote 4 stack.
  Adapting its loaders/dependencies was not the simplest compatible option.
- [pyannoteAI streaming](https://docs.pyannote.ai/tutorials/streaming-real-time):
  a hosted alternative, not local community-1 inference. It requires separate
  service credentials and uploading audio, so it was not selected.
