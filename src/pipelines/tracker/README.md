# Alderman speech and action tracker

This job reads published transcript segments from the meetings PostgreSQL
database and writes a dataset for the tracker. Run it independently of
transcription and the stance dashboard. It uses a read-only transaction.

The first version accepts quote candidates prepared by a reviewer or a
separate extraction job. It checks each quote against its referenced segment,
uses the segment's time range to resolve evidence, and saves the source
recording link. The resolver supplies speaker names and person IDs. Each
reviewed follow-up and digest citation is resolved to a verified statement ID.
An invalid quote is rejected. If a digest or follow-up references a rejected
quote, the build fails so that it cannot publish a broken citation.

## Run the demo

Load the seed into a development database using the
[commitment demo instructions](../../../data-reference/commitment-demo/README.md).
Install the project with `python -m pip install -e .` to expose the CLI.
Use the reader connection URL in `MEETINGS_DATABASE_URL`. To include official
actions, extract the relevant `Legislation/SO2025-0021894.json` from the
January 21 City Council directory in the archive. Run from the repo root:

```sh
chi-ciq-tracker build \
  --candidates data-reference/commitment-demo/candidates.json \
  --evidence-dir data-reference/evidence \
  --legislation /path/to/Legislation/SO2025-0021894.json \
  --output-dir data/tracker/congress-theater
```

`--legislation` can be omitted for a speech-only dataset or repeated with
additional archive legislation files. The importer reads the filing sponsor
and individual votes explicitly recorded in each legislative action. It
preserves recorded vote wording and source locators. The voice-vote discussion
in the speech records cannot assign an individual a vote.

The output directory contains `tracker.json` with the statements, official
actions, optional reviewed follow-up and digest, and rejected candidates.
It also contains `statements/<meeting-id>.jsonl` for each supplied meeting.
Each build replaces the addressed JSONL partitions and creates a new
`tracker.json` snapshot of exactly the inputs supplied for that build.
Use `tracker.json` for queries. Use separate output directories for different
pilot batches.

For example, retrieve the official records for the bill's filing sponsor
using the person ID recorded in the archive:

```sh
chi-ciq-tracker query data/tracker/congress-theater/tracker.json \
  --person-id 41A0E156-F788-EC11-8D20-001DD804FD38 \
  --from-date 2025-01-01 --through-date 2026-12-31
```

This returns cited data for a later answer layer. Person queries include
speech only when the evidence resolves a person ID. Unknown speakers are
still available in the full dataset. The demo speech covers selected
excerpts about one agenda item.

## Point it at real transcripts

Use the candidate manifest in the demo as a template. Set each
`published_source_key` to the existing key in `published_sources` and
`segment_sequence` to the referenced transcript segment. Supply the
original recording ID and URL from a reviewed recording mapping. For clips,
supply `recording_offset_s` in seconds. Where available, include the
published source's `content_sha256` to reject a candidate built against
different recording bytes. Quote timing has segment precision, and the
first version accepts quotes that fit within a single segment.

For a new meeting, supply reviewed candidates and evidence for that
recording. An automated candidate extractor and an LLM answer interface
can call the same build and retrieval steps when ready.

## Run on Fuzzball after the current workflow

The [standalone Fuzzfile](../../../fuzzball-tracker.yaml) restores a completed
`meetings-backup-after` dump into its own disposable PostgreSQL service, then
runs the tracker with a SELECT-only role. It uses a separate service port and
never mounts the live PostgreSQL data directory. The original workflow can
keep serving its dashboard. Both jobs are CPU-only.

1. Wait for `meetings-backup-after` to finish. Find the exact dump path in its
   logs and set `TRACKER_MEETINGS_DUMP` to that path in a copy of the Fuzzfile.
   The dump must be readable by the restore job on the shared datasets volume.
2. In `TRACKER_INPUT_DIR` on that volume, stage `candidates.json` for the
   transcripts in the dump, an `evidence/` directory of JSONL records, and
   optionally `Legislation/*.json` from the archive. The demo manifest refers
   to seed-only source keys. For production transcripts, use their published
   source keys and segment sequences. Quote candidates still need to be
   prepared before submission.
3. Use a fresh `TRACKER_OUTPUT_DIR` for each batch. The Fuzzfile uses the
   existing `chi-ciq-postgres-password` secret for the disposable database.
   After this PR merges, the ARM64 image workflow publishes the tracker image
   under the `fuzzball` tag. To run before merge, dispatch the image workflow
   on the PR branch and use its `sha-<full-commit>` image tag in your Fuzzfile.
4. Validate and submit the configured copy:

   ```sh
   fuzzball workflow validate fuzzball-tracker.yaml
   fuzzball workflow start fuzzball-tracker.yaml
   ```

The tracker job reports counts of accepted statements, rejected candidates,
and imported actions. Read `tracker.json` from the output directory. The
temporary PostgreSQL service needs enough local disk space to restore the
meetings database, including its indexes.
