# Analytics database

[Database overview](../README.md) | [Analytics pipeline](../../pipelines/analytics/README.md) |
[Web dashboard](../../web/README.md)

Independent PostgreSQL/pgvector database holding derived analytics for the web
dashboard. The upstream `pgvector/pgvector:pg17` server uses its own credentials
and volume (Compose: `analytics-db`, host port 5435). The module Dockerfile builds
migration/check/backup jobs, not the server. Only the
[analytics pipeline](../../pipelines/analytics/README.md) writes here; everything
can be rebuilt from the source databases plus the roster and manual labels.

## Tables

| Group | Tables |
|---|---|
| People | `representatives` (`origin` `manual`/`official`, `source_ref`), `representative_aliases` (`origin` `manual`/`generated`/`discovered`), `roster_candidates` (title-prefixed names found in source text, for review) |
| Sources | `sources` (kind `meeting`/`document`, date, body, signature), `speakers` (voice centroid), `analysis_units` (turns/chunks with text embedding) |
| Attribution | `manual_speaker_labels` (pinned to source signature), `speaker_attributions` (method, confidence, margin), `voiceprints` |
| Results | `sentiment_results`, `emotion_results`, `policy_topics`, `topic_embeddings`, `topic_relevance`, `stance_results`, `representative_mentions` |
| Operations | `analysis_runs`, `schema_state` |

Unit-level results cascade when a source is replaced. Model names are stored with
every result so model changes are detected and recomputed.

## Dashboard read models

Pre-aggregated materialized views, each with a unique index and refreshed
`CONCURRENTLY` in one transaction at the end of a run, so readers never block or
see a half-refreshed dashboard:

- `dash_representatives`: per-person statement counts (and manually attributed
  counts), speaking time, mean sentiment, emotions, positions, and mentions.
- `dash_representative_positions`: per-person, per-topic stance counts and a
  confidence-weighted stance score (−1 oppose … +1 support).
- `dash_representative_monthly`: monthly activity and sentiment per person.
- `dash_topics`, `dash_topic_trends`: per-topic totals and monthly stance trends.
- `dash_sources`: per-source coverage.

`dash_statements` (a plain view over indexed tables) serves paginated
drill-downs with stance, evidence, sentiment, emotion, and attribution columns.
Views are versioned by `DASHBOARD_VIEWS_VERSION`; `migrate` rebuilds them only
when it changes.

## Reader role

[web-role.sql](web-role.sql) creates `analytics_reader` (password from
`PGPASSWORD`, set from the shared `POSTGRES_PASSWORD`) with SELECT on the
dashboard objects only (`DASHBOARD_OBJECTS` in [views.py](views.py)). Voice centroids, embeddings,
manual-label notes, roster candidates, and speaker internals stay private. `migrate` re-applies the
grants after rebuilding views when the role exists.

## Maintenance

```sh
chi-ciq-analytics-database migrate
psql "$ANALYTICS_ADMIN_PSQL_URL" -v ON_ERROR_STOP=1 -f src/databases/analytics/web-role.sql
chi-ciq-analytics-database check
chi-ciq-analytics-database backup --output-dir data/analytics-database
```

All commands read `ANALYTICS_DATABASE_URL`. Backups are append-only custom-format
dumps; restore into a new empty database, never over the live one.
