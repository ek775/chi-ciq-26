# Transcription pipeline

[Pipeline overview](../README.md) | [Downloads](../downloads/README.md) | [Meetings schema and metadata](../../databases/meetings/README.md) | [Web](../../web/README.md)

Run shell examples from the repository root, not this module directory.

## Navigation

- [Fuzzball batch input](#fuzzball-batch-input)
- [Initial ingestion and updates](#ingestion-versus-updates)
- [Development setup](#development-setup)
- [Directory ingestion and backup](#directory-ingestion-and-database-archive)
- [Library usage](#single-recording-library-usage)
- [Streaming and failure contracts](#iteration-and-failure-semantics)
- [GPU access](#nvidia-gpu-access-in-docker)
- [Tests](#tests)
- [Investigated alternatives](#streaming-inference-options-investigated)

This module owns the [worker image](Dockerfile), [dispatcher](dispatch.py),
[ingestion CLI](pipeline.py), and streaming inference adapters. Database schema,
filename parsing, and backups belong to the
[meetings module](../../databases/meetings/README.md).

## Fuzzball batch input

The Fuzzball volume `team8/volume_test` contains an `audio` directory at the
volume root. Inspection on 2026-11-14 found dated MP4s and a growing set of
`vimeo-<id>-audio-low.mp4` files; sampled files contain AAC audio without video.
When the volume is mounted at `/volume`, use `/volume/audio`, not
`/volume/data/audio`. The ID-only filenames do not identify the meeting date or
type. Generate a mapping in the downloads environment (with Vimeo network
access), review the titles and dates, and supply it to the transcription worker:

```sh
chi-ciq-vimeo-metadata --input-dir /volume/audio > metadata.pending.json
# Review and correct metadata.pending.json; then make it available to the worker.
chi-ciq-ingest --input-dir /volume/audio --namespace recordings \
  --metadata-file /path/to/metadata.pending.json
```

The generator queries public Vimeo titles and fails without producing a partial
JSON mapping if a title cannot be parsed. It never uses an upload date as a
meeting date. Supply or correct entries manually when titles lack meeting
metadata, and rerun with `--metadata-file overrides.json` to skip title lookup
for those IDs. The JSON maps IDs to dated MP4 filenames, for example
`{"1200176629": "2026-06-11_economic_development.mp4"}`. Dated files need no
entry. The batch validates the complete mapping before inference and processes
recordings sequentially in one GPU job. Configure the worker's
`DATABASE_URL` and `HF_TOKEN` using cluster secrets. The worker must be able to
reach the private meetings database and download required models; mount a
persistent model cache if the cluster supports it. Run the schema migration
before ingestion and back up only after the job succeeds.

Wait for acquisition to finish before generating the mapping or ingesting:
the observed ID-named files appeared while inspection was in progress, and
their names alone do not prove that downloads are complete. Mount inputs
read-only for inference. A cluster-specific runnable Fuzzfile still needs the
supported schema, image registry reference, database endpoint, and secret
bindings; `fuzzball.draft.yaml` is an architecture note, not that Fuzzfile.

`chi-ciq-dispatch --input-dir /volume/audio --metadata-file mapping.json ...`
also accepts this mapping and embeds each resolved filename in the individual
worker command; the worker does not need the mapping file. Use
`--meeting-filename` with `--input-file` for an individual ID-named recording.

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
- SHA-256 of the input bytes and resolved meeting date and type determine whether
  to skip it. A corrected mapping reprocesses and atomically replaces the
  published run. Skipping an unchanged
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

Direct library calls to `TranscriptionWorkflow.transcribe()` create new runs.
Existing standalone runs and restored archives are preserved by migrations;
they are not silently deleted or automatically published. Run the ingestion
pipeline on their raw inputs to establish published source identities. Failed
stages may accumulate and need an explicit retention policy.

Public Vimeo acquisition is provided by `pipelines.downloads`; other raw
document/video acquisition remains an upstream handoff, not implemented here.
Current transcription ingestion accepts complete, filename-validated MP4 files directly inside
the input directory (non-recursive). It does not claim to ingest arbitrary
documents. Vimeo downloading is a separate acquisition service.

## Development setup

1. Copy `.env.example` to `.env`. Set a Hugging Face read token and replace the
   example database password in both places. Use a URL-safe local password.
2. Accept the model conditions for
   [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1).
3. Run `docker compose up --build -d`, or use VS Code **Reopen in Container**.
4. Put MP4 files under `data/mp4/`. File contents and credentials are gitignored.
5. Run:

   ```sh
   docker compose exec app chi-ciq-database migrate
   docker compose exec app chi-ciq-ingest --input-file \
     "data/mp4/2026_november_10_-_committee_on_budget_and_government_operations (240p).mp4"
   ```

The container uses Python 3.12 and installs system FFmpeg and its shared libraries.
Model weights are downloaded on first use and cached in a Docker volume. Inference
is local; Hugging Face is used to download models, not to receive meeting audio.
Review each model's license and conditions before use.

For host development, install FFmpeg, use Python 3.12, run
`python -m pip install -e ".[dev,ingestion,web]"`, and start only the database with
`docker compose up -d db`. The ingestion CLI loads `.env` automatically.
PostgreSQL is exposed on `127.0.0.1:5433` for host connections; use port `5433`
in your local `.env` `DATABASE_URL`. Containers still connect to `db:5432`.
Dependencies are intentionally unpinned; resolve and lock a tested PyTorch,
TorchAudio, and TorchCodec combination before production use.

## Directory ingestion and database archive

Use the production CLI for directory processing and a separate backup job:

```sh
chi-ciq-database migrate
chi-ciq-ingest --input-dir data/mp4 --namespace recordings
chi-ciq-database backup --output-dir data/database
```

Run backup only after ingestion succeeds. Input discovery is non-recursive and
sorted by filename; invalid filenames and empty directories fail before inference.
The worker loads models once per invocation, skips unchanged inputs, and
publishes each successful recording independently. Failures preserve previously
published results and propagate to the caller.

Progress logs report model loading, extraction, diarization, inference, embedding,
and database stages. Timers use monotonic wall time, not recording timestamps.
Empty Whisper output warns and skips a window; malformed output/model errors fail.
CLI logs are JSON on stderr. Set `LOG_LEVEL` and optionally `LOG_SERVICE_NAME`
in the cluster worker template; no writable shared log mount is required.
Use the submitter-side [log helper](../../../README.md#logging) to save each
Fuzzball job's output locally, including completed or failed workers.

For backup scope, client compatibility, and archive handling, see the
[meetings database guide](../../databases/meetings/README.md).

## Single-recording library usage

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

## Iteration and failure semantics

These contracts describe `TranscriptionWorkflow.transcribe()`.
The ingestion CLI adds the source-level deduplication and atomic
publication described in [Ingestion versus updates](#ingestion-versus-updates).

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
  construction, not package import. The shared runtime checks `torch.cuda.is_available()`
  once at run setup, print the selected device, and pass it to all four adapters.
  They use CUDA when available and CPU otherwise. GPU model-loading errors
  propagate rather than silently retrying on CPU. Direct library callers can
  pass `device="cuda"` explicitly; adapter defaults remain CPU.

## NVIDIA GPU access in Docker

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

## Tests

```sh
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
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
