# Pipeline containers

[Project overview](../../README.md) | [Downloads](downloads/README.md) | [Transcription](transcription/README.md) | [Documents](documents/README.md) | [Analytics](analytics/README.md)

Run shell examples from the repository root, not this module directory.

## Module ownership

- [Downloads](downloads/README.md) acquires complete MP4 files before dispatch.
- [Transcription](transcription/README.md) handles initial database building and
  periodic updates through the same per-recording worker image.
- [Documents](documents/README.md) mirrors supported document trees into a
  separate pgvector database using CPU text embeddings by default.
- [Analytics](analytics/README.md) remains unimplemented.

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

## Fuzzball draft and deferred delivery

[fuzzball.draft.yaml](../../fuzzball.draft.yaml) records container ownership, storage boundaries, initial
build/update sequences, and web release stages. It is an **architecture draft,
not an executable Fuzzfile or a validated Fuzzball schema**. Do not submit it.
Registry images, persistent volume bindings, secrets, private ingress, scheduler,
and cluster-compatible YAML must be resolved when hackathon networking is
available. No Fuzzball deployment or CI/CD pipeline is claimed to be operational.
The draft explicitly keeps database lifetimes independent of finite pipeline
jobs and replaceable web releases.
