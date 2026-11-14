# Transcript web service

[Meetings database](../databases/meetings/README.md) | [Transcription](../pipelines/transcription/README.md)

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

Use a separate database login with SELECT-only grants for the web service.
Neither the UI nor API implements application authentication; restrict
production access with Fuzzball's private endpoint/token mechanism or an
authenticated gateway. Do not expose this transcript/voice dataset publicly by
default.
