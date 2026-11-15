# Local commitment tracker fixture

This fixture links the Finance Committee's January 14, 2026 report-out
commitment on the Congress Theater loan ordinance to its January 21 Council
follow-up. It also supplies a digest of the selected Finance Committee agenda
item. The words and start times come from the timestamped reference files in
../Transcripts. End times use the next cue boundary. The fixture has partial
coverage, and the underlying recordings have not been reviewed.

Apply the meetings schema and load the fixture into a local development
database. From the repo root, with Compose configured:

```sh
docker compose up -d db
docker compose --profile pipelines run --rm meetings-migrate
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U transcription -d transcription \
  < data-reference/commitment-demo/seed.sql
```

The seed is transactional and repeatable. It creates two completed meetings,
two published sources, and seven segments using the same tables that
analytics and web read. The published keys start with `commitment-demo/`.
The seed uses placeholder speaker labels and nonzero vectors to satisfy the
schema and allow analytics turn assembly. They carry no identity or model
inference information. Do not use them for voice matching or similarity
evaluation. The completed status makes the excerpts visible to the reader.

Use `candidates.json` as input to the new verifier and digest code. Locate
each candidate by published source key and segment sequence, check the quote
against that segment, and use its recorded offsets and the candidate's
recording URL to build the link. Speakers remain null. The follow-up and
digest reference candidate keys that the implementation can resolve to
verified statement IDs. The expected fulfilled status refers to reporting the
ordinance to Council.
