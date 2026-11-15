# Alder Audit web service

[Meetings database](../databases/meetings/README.md) | [Transcription](../pipelines/transcription/README.md) |
[Analytics](../pipelines/analytics/README.md)

Run shell examples from the repository root, not this module directory.

## Web browsing and search

```sh
chi-ciq-web
# http://127.0.0.1:8080/
```

The server binds port 8080 and requires an already migrated database.
`/health` checks meetings connectivity, `/ready` checks the meetings schema and
the analytics schema when configured, and `/docs` describes
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

## Alder Audit

The home page combines the transcript browser, policy dashboard, published
commitment/digest tracker, and the City of Chicago's official address-based
ward lookup. Address lookups are sent directly to the City's embedded service,
not to Alder Audit. The lookup is informational; verify a ward with the City.

The commitment and digest section reads batches separately published to the
analytics database by the
[tracker publisher](../pipelines/tracker/README.md#publish-a-completed-batch-to-the-analytics-database).
Grant `analytics_reader` SELECT on `tracker.batches`, `tracker.statements`,
and `tracker.actions` as described in that runbook. Until a batch is published,
the section explains that reviewed records are not available.

The web API hides quotes unless the tracker marks them verified, hides speaker
names unless the evidence confirms them, and accepts video links only from
Vimeo. Policy-dashboard representative statement queries show only manually
attributed speakers, and public statement responses suppress unverified quote
text.

## Analytics dashboard

When `ANALYTICS_DATABASE_URL` is set, `/dashboard` shows representatives,
policy topics, stance trends, and statement drill-downs from the
[analytics database](../databases/analytics/README.md). Without it, the page
loads but `/api/analytics/*` returns 503. Endpoints (all read-only, paginated):

- `GET /api/analytics/summary` and `GET /api/analytics/sources`
- `GET /api/analytics/representatives?q=&role=&active=&sort=&limit=&offset=`
- `GET /api/analytics/representatives/{id}` (positions and monthly activity)
- `GET /api/analytics/topics?area=&sort=` and `GET /api/analytics/topics/{key}?kind=meeting`
- `GET /api/analytics/positions?representative_id=&topic_key=&min_statements=&manual_only=`
- `GET /api/analytics/statements?representative_id=&topic_key=&stance=&kind=&method=&attributed=&verified_evidence=&min_confidence=&date_from=&date_to=`

Connect as `analytics_reader`, which can read only the dashboard views and the
separately granted tracker tables. Treat representative aggregates and stances
as experimental model output, not verified positions.

## Access control

[fuzzball.yaml](../../fuzzball.yaml) deploys this image as a persistent service
only after parallel ingestion/analytics, dashboard refresh, completed backups,
and schema/reader-privilege checks. Its user-scoped endpoint serves both `/` and
`/dashboard` on port 8080 and uses `/ready` as the readiness probe. See the
[deployment guide](../../README.md#fuzzball-deployment) for builds, secret
bindings, endpoint access, and release verification. The web container mounts
no raw inputs or database archives and receives only reader database URLs.

Use a separate database login with SELECT-only grants for the web service.
Neither the UI nor API implements application authentication; restrict
production access with Fuzzball's private endpoint/token mechanism or an
authenticated gateway. Do not expose this transcript/voice dataset publicly by
default.
