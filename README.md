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
   and replace the database password in both places. Use a URL-safe password.
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
