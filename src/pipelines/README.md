# Pipeline containers

[Project overview](../../README.md) | [Downloads](downloads/README.md) | [Transcription](transcription/README.md) | [Documents](documents/README.md) | [Analytics](analytics/README.md)

Run shell examples from the repository root, not this module directory.

## Module ownership

- [Downloads](downloads/README.md) acquires complete MP4 files onto shared input storage.
- [Transcription](transcription/README.md) handles initial database building and
  periodic updates by processing the mounted recordings directory in one batch.
- [Documents](documents/README.md) mirrors supported document trees into a
  separate pgvector database using CPU text embeddings by default.
- [Analytics](analytics/README.md) reads the meetings and documents databases
  read-only and publishes representative, topic, sentiment, emotion, and vLLM
  stance results to the analytics database that backs the web dashboard.

Python imports use `pipelines.transcription`, `databases.meetings`, and `web`;
the former `transcription` and `chi_ciq` paths have been removed. Persistence
re-exports within the pipeline share the meetings implementation, not a second
database schema. See the [database overview](../databases/README.md).

## Local container entry points

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

## Fuzzball pipeline notes

[fuzzball.draft.yaml](../../fuzzball.draft.yaml) records the mounted-input
pipeline design, not an executable Fuzzfile or validated cluster schema. The CLI
reports an enabled persistent `shared/datasets` volume backed by external NFS
and an enabled persistent `team8/volume_test` volume. An inspection on
2026-11-14 found dated and Vimeo-ID audio-only MP4s under `audio` at the root of
`team8/volume_test`; new files appeared during inspection. See the
[transcription guide](transcription/README.md#fuzzball-batch-input) for metadata
mapping and the requirement to wait for completed downloads. The checkout
contains 202 transcript `.txt` files and one MP4; the contents of
`shared/datasets` remain unverified. The analytics batch reads
its roster and speaker labels from `/datasets/analytics`, runs after source
updates, and needs a reachable vLLM endpoint for the stance stage.

The transcription batch mounts the recordings directory read-only and processes
all validated MP4 files in that job. The documents batch mounts the transcript
directory read-only and ingests its supported files. Downloads and completed
database backups are the only pipeline writers to the shared volume.
Credentials remain cluster secrets. The Fuzzball workflow schema, registry
images, database endpoints, and secret bindings still need confirmation before
deployment. The per-recording dispatcher remains available for larger workloads.
