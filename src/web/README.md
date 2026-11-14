# Transcript web service

[Meetings database](../databases/meetings/README.md) | [Transcription](../pipelines/transcription/README.md) |
[Analytics](../pipelines/analytics/README.md)

Run shell examples from the repository root, not this module directory.

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

Connect as `analytics_reader`, which can read only the dashboard views. The
dashboard labels voice-inferred attributions and their confidence; treat stances
as model output, not verified positions.

## Access control

Use a separate database login with SELECT-only grants for the web service.
Neither the UI nor API implements application authentication; restrict
production access with Fuzzball's private endpoint/token mechanism or an
authenticated gateway. Do not expose this transcript/voice dataset publicly by
default.
