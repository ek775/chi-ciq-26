# Chicago meeting ingestion and web services

Container-oriented pipelines acquire public recordings, transcribe and diarize
them locally, and publish results to PostgreSQL/pgvector for a read-only web UI.
Fuzzball distribution is prepared but cluster deployment remains unverified.

## Architecture and module guides

```text
Vimeo acquisition -> complete MP4 inputs -> transcription workers
                                               |
                                      PostgreSQL/pgvector -> web UI/API
                                               |
                                        database backups
```

| Module | Responsibility and detailed documentation |
|---|---|
| [Pipelines](src/pipelines/README.md) | Container orchestration and Fuzzball handoff |
| [Downloads](src/pipelines/downloads/README.md) | Public Vimeo IDs, filename metadata, safe acquisition |
| [Transcription](src/pipelines/transcription/README.md) | Per-file workers, updates, inference API, GPU setup, tests |
| [Document pipeline](src/pipelines/documents/README.md) | Reserved; not implemented |
| [Analytics](src/pipelines/analytics/README.md) | Reserved; not implemented |
| [Databases](src/databases/README.md) | Independent persistent database boundaries |
| [Meetings database](src/databases/meetings/README.md) | Schema, vectors, recording metadata, migrations, backups |
| [Documents database](src/databases/documents/README.md) | Reserved; not implemented |
| [Web](src/web/README.md) | Transcript browsing/search API and access controls |
| [vLLM](src/vllm/README.md) | Optional upstream text-inference service |

Each module guide lives beside its `__init__.py`. Custom service Dockerfiles
live in their source directories and use the repository root as build context.
PostgreSQL and vLLM use upstream images; web releases never replace live databases.

## Quick local start

Run these commands from the repository root:

1. Copy [.env.example](.env.example) to `.env`, set a Hugging Face read token,
   and set separate passwords for the admin and web database logins. Use
   URL-safe passwords.
2. Accept the
   [pyannote model conditions](https://huggingface.co/pyannote/speaker-diarization-community-1).
3. Put complete, dated MP4s in `data/mp4/`, or run the downloads command below.
   Credentials and data are gitignored.

```sh
docker compose up -d db
# Optional acquisition; requires Vimeo/CDN network access:
docker compose --profile downloads run --build --rm downloads

docker compose --profile pipelines run --build --rm meetings-backup
docker compose --profile pipelines run --build --rm meetings-migrate
docker compose --profile pipelines run --build --rm transcription
docker compose --profile pipelines run --build --rm meetings-backup
docker compose --profile web up --build -d web
```

Open **http://127.0.0.1:8080/**. Select jobs explicitly: acquisition, migration,
transcription, and backup are ordered operations, not simultaneous batch services.
NVIDIA Docker support is required for the current transcription configuration.
First builds and model downloads need network access and can take time.
Vimeo titles lacking meeting date/type require an explicit metadata mapping;
see the [downloads guide](src/pipelines/downloads/README.md).

Unchanged transcription inputs are skipped. Changed inputs replace the previous
published run only after successful processing; failures preserve published
results. Backups are append-only. See the [update contract](src/pipelines/transcription/README.md#ingestion-versus-updates).

## Quick worker and distribution commands

```sh
# One recording using the transcription container:
docker compose --profile pipelines run --build --rm transcription \
  chi-ciq-ingest --input-file /inputs/2026-11-10_budget.mp4

# With the Python package installed, prepare per-file Fuzzball jobs offline:
chi-ciq-dispatch --input-dir data/mp4 --output-dir data/dispatch \
  --image docker://your-registry/chi-ciq-transcription:commit-tag
```

[fuzzball.draft.yaml](fuzzball.draft.yaml) is an architecture draft, **not a
submittable Fuzzfile**. Actual submission needs a logged-in context and a
cluster-specific template for storage, secrets, resources, and networking.
See the [fan-out guide](src/pipelines/transcription/README.md#transcription-fan-out).

## Logging

No logging server, extra container, or shared worker filesystem is needed.
From your logged-in **Fuzzball submitter**, collect a job's logs locally:

```sh
fuzzball workflow describe WORKFLOW
bash scripts/logs.sh WORKFLOW JOB
# In another terminal (substitute the actual workflow ID and job name):
tail -f data/logs/WORKFLOW/JOB.log
grep '"level": "ERROR"' data/logs/WORKFLOW/JOB.log
```

The helper runs `fuzzball workflow log WORKFLOW JOB --follow` and appends to
gitignored `data/logs/WORKFLOW/JOB.log`, while also showing output in the terminal.
Run it for each container/job you want to collect: downloads, transcription
workers, migrations, backups, web, PostgreSQL, and vLLM, using the job names in
your cluster template. Workflow IDs are in the dispatch CLI receipts; job names
are the **template's job keys**, not necessarily the dispatch workflow name.
Collection is explicit, not automatic discovery of every submitted workflow.
Run several collectors in separate terminals; they write separate files.

```sh
# Download retained logs and exit, including completed/failed jobs:
bash scripts/logs.sh WORKFLOW JOB --lines -1
# Follow only new output, without re-appending history:
bash scripts/logs.sh WORKFLOW JOB --follow --lines 0
# Inspect all collected jobs together:
tail -f data/logs/*/*.log
```

Set `LOG_DIR` to change the host directory, or `FUZZBALL_BIN` to select the CLI.
Ctrl-C stops collection, not the remote job. CLI failures remain visible and
return a nonzero exit code. Re-running collection with history enabled appends
duplicates; there is no automatic reconnect, deduplication, or rotation.
Archive the directory between hackathon runs. Fetch logs before deleting
workflows or the cluster's log retention expires.

Python services use shared JSON logging on stderr: UTC `timestamp`, `level`,
`service`, `logger`, `message`, and `exception` when present. Uvicorn access/error
logs and Python warnings use the same format. Set `LOG_LEVEL` (default `INFO`)
and optionally `LOG_SERVICE_NAME` in each Fuzzball job's environment.
Machine-readable CLI results remain on stdout. PostgreSQL, vLLM, subprocesses,
and CLI output keep their native format, so collected files are mixed text,
not guaranteed JSONL. Add `--timestamp` to collection for server timestamps on
native output. Keep logs private: URLs, paths, and exception details may be sensitive.

The Fuzzball CLI flags above were checked locally; cluster connectivity and
deployment remain unverified. For placeholder Compose development, use
`docker compose --profile '*' logs --follow --timestamps --no-color`;
this does not collect remote Fuzzball jobs.

## Development and quick checks

```sh
docker compose up --build -d app
docker compose exec app sh -c \
  'TEST_DATABASE_URL="$DATABASE_URL" python -m pytest -q'
```

For host development, use Python 3.12+, install FFmpeg and the needed extras:

```sh
python -m pip install -e ".[dev,ingestion,web,downloads]"
python -m pytest -q
python -m ruff check src tests
```

Without `TEST_DATABASE_URL`, PostgreSQL integration tests skip. Tests use model
stand-ins; they do not measure recognition accuracy or verify Fuzzball scheduling.
GPU requirements, library API usage, streaming behavior, and validation details
are in the [transcription guide](src/pipelines/transcription/README.md).

The development web/API and vLLM services have no application authentication.
Keep them private; review their module guides before exposing them. Dependencies
are not locked for production.
