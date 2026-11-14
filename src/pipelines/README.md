# Pipeline containers

[Project overview](../../README.md) | [Downloads](downloads/README.md) | [Transcription](transcription/README.md) | [Documents](documents/README.md) | [Analytics](analytics/README.md)

Run shell examples from the repository root, not this module directory.

## Module ownership

- [Downloads](downloads/README.md) acquires complete MP4 files onto shared input storage.
- [Transcription](transcription/README.md) handles initial database building and
  periodic updates with deterministic shards of a mounted recordings directory.
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

[fuzzball.yaml](../../fuzzball.yaml) is a validated v4 deployment workflow.
See the [deployment guide](../../README.md#fuzzball-deployment) for image builds,
input staging, secrets, submission, and endpoint verification.

128 queued transcription shards (two active GPU workers) and four CPU document
shards run in parallel.
Both arrays must finish before analytics sync. After roster preparation,
attribution, mentions, sentiment, emotion, and topics fan out. Stance joins
attribution and topics, and refresh joins every analysis branch. Sentiment,
emotion, and stance each have 32 unit shards with four active tasks; stance
permits two requests per task, eight total. Backups and
reader/schema checks gate the persistent, private web service.

Shard assignment hashes the input's relative path, so different mounts and
newly added files do not move existing inputs between shards. Every shard keeps
the same namespace and input root; each source remains an atomic update.
Freeze inputs during ingestion and do not overlap whole release workflows.
Workers do not write inputs; use cluster ACLs to enforce that boundary.
Only archive jobs write to `/datasets/database`. The per-recording dispatcher
remains available for independent submissions outside the deployment DAG.
