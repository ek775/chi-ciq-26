# Analytics pipeline

[Pipeline overview](../README.md) | [Analytics database](../../databases/analytics/README.md) |
[Meetings database](../../databases/meetings/README.md) |
[Documents database](../../databases/documents/README.md) | [vLLM](../../vllm/README.md) |
[Web dashboard](../../web/README.md)

Run shell examples from the repository root, not this module directory.

A finite batch job that reads published meetings and mirrored documents
**read-only**, analyzes what city representatives say about policy topics, and
publishes the results to the separate [analytics database](../../databases/analytics/README.md)
that backs the web dashboard. Source databases are never written.

## Stages

`chi-ciq-analytics run` executes these stages in order (select a subset with
`--stages`). Each stage is incremental and safe to rerun; a session advisory lock
prevents concurrent runs, and each run is recorded in `analysis_runs`.

| Stage | Work |
|---|---|
| `sync` | Snapshot sources by processing signature. Changed sources are atomically replaced; removed sources are deleted. Meetings become speaker *turns* (consecutive segments, ≤1500 chars); document chunks become units. |
| `roster` | Seed current officials and mine misspelled names from source text (see [Roster](#roster)). |
| `attribute` | Map speakers to representatives: manual labels first, then voice-print matching against centroids built from manually labeled speakers (`--voice-threshold`, `--voice-margin`). |
| `mentions` | Find roster names/aliases mentioned in each unit (rescans when the roster changes). |
| `sentiment` | Local transformers sentiment (`--sentiment-model`, default `cardiffnlp/twitter-roberta-base-sentiment-latest`). Score = P(positive) − P(negative). |
| `emotion` | Local transformers emotion (`--emotion-model`, default `j-hartmann/emotion-english-distilroberta-base`): anger, disgust, fear, joy, neutral, sadness, surprise. |
| `topics` | Embed the taxonomy with each source's own text-embedding model and score unit relevance by pgvector cosine similarity (`--relevance-threshold`, `--relevance-top-k`). |
| `stance` | For relevant units, ask vLLM for a schema-constrained stance (support/oppose/mixed/neutral/not_addressed) toward each topic's proposition, with confidence, summary, and a verbatim evidence quote. Reasoning models get `--vllm-reasoning-effort` (default `low`; empty to omit) and `--vllm-reasoning-tokens` of thinking budget; `--vllm-timeout` defaults to 300 s. |
| `refresh` | Refresh the dashboard materialized views in one transaction. |

Results are keyed by model (and, for stance, prompt version and topic definition
hash), so changing a model, the prompt, or a topic's text recomputes only the
affected rows. Only attributed units are stance-analyzed with
`--stance-scope attributed`; `--stance-limit` caps requests per run and
`--stance-concurrency` bounds parallel vLLM calls. The stage fails if every
request fails, so an unreachable vLLM server is reported rather than skipped.

## Roster

The `roster` stage builds the `representatives` table on every run:

- **Official seeding** (`--roster-source portal`, the default): all 50
  alderpersons from the City of Chicago Data Portal
  [Ward Offices](https://data.cityofchicago.org/resource/htai-wnw4.json) dataset,
  plus the mayor and city clerk from the bundled
  [chicago_officials.json](chicago_officials.json) snapshot, which is also the
  fallback when the portal is unreachable (`--roster-source snapshot` uses it
  only; `off` skips seeding). Officials no longer listed get `active_to` set;
  they keep their history. The treasurer is not seeded; add it by CSV.
- **Generated aliases**: full name, first + last name, and title forms
  ("alderman", "alderwoman", "alderperson", "ald.", "chair", "chairman", ...)
  with spelling variants ("o'shea"/"oshea", "sigcho lopez"). Aliases shared by
  two people are dropped. Mentions count the longest alias, so "Alderman
  Rodriguez Sanchez" is not also counted as "Alderman Rodriguez".
- **Discovered aliases**: transcripts are speech recognition output, so names
  after a title are collected from all units and matched to alderperson
  surnames by spelling similarity and Soundex ("Riley" → Reilly, "Knutson" →
  Knudsen, "Lospada" → La Spata). Names seen at least
  `--roster-min-occurrences` (default 3) times that match exactly one person
  become aliases. Review all names, including unmatched ones, with
  `chi-ciq-analytics roster-candidates --output candidates.csv`.

Manual entries always win: an official is not seeded when a manual
representative has the same id or currently holds the same seat (ward, or role
for citywide offices). `chi-ciq-analytics export-roster --output roster.csv`
writes the current roster (without generated aliases) in the CSV format below
for editing and reloading.

## Inputs

**Roster overrides** (`chi-ciq-analytics roster roster.csv`, optional):

```csv
id,display_name,role,ward,aliases,active_from,active_to
ald-smith,Pat Smith,alderperson,12,Alderperson Smith;Ald. Smith,2023-05-15,
mayor-jones,Lee Jones,mayor,,Mayor Jones,,
```

`id` is a lowercase slug; `role` is one of alderperson, mayor, clerk, treasurer,
official, staff, public, other; aliases are `;`-separated. Reloading upserts
representatives as manual entries and replaces their aliases, keeping aliases
discovered in transcripts.

**Speaker labels** (`chi-ciq-analytics label-speakers labels.csv`):

```csv
source_key,speaker_label,representative_id,note
meetings:recordings/2024-01-10 City Council.mp4,SPEAKER_03,ald-smith,introduced item 4
```

Generate a worksheet of known speakers with
`chi-ciq-analytics export-speakers --unattributed-only --output speakers.csv`.
Labels are pinned to the source's current signature; when a source is
reprocessed its diarization labels may change, so stale labels are ignored,
logged, and counted in the run until they are re-labeled.

**Taxonomy**: [taxonomy.json](taxonomy.json) is an editable starting set of
directional policy propositions grouped by area. Pass `--taxonomy file.json` to
use another. Removed topics are deactivated (history is retained).

## Running

```sh
export ANALYTICS_DATABASE_URL=postgresql+psycopg://analytics:...@localhost:5435/analytics
export MEETINGS_DATABASE_URL=postgresql+psycopg://web_reader:...@localhost:5433/transcription
export DOCUMENTS_DATABASE_URL=postgresql+psycopg://documents:...@localhost:5434/documents
export VLLM_BASE_URL=http://127.0.0.1:8000/v1   # served model name: VLLM_SERVED_MODEL=local-ai
chi-ciq-analytics-database migrate
chi-ciq-analytics roster data/analytics/roster.csv   # optional overrides
chi-ciq-analytics run --device cpu            # or --device cuda
chi-ciq-analytics roster-candidates --output candidates.csv
chi-ciq-analytics run --sources meetings --stages sync attribute refresh
chi-ciq-analytics refresh
```

`MEETINGS_DATABASE_URL` falls back to `DATABASE_URL`. Use SELECT-only logins for
sources. `VLLM_API_KEY` is sent as a bearer token when set. The Compose
`analytics` service mounts `ANALYTICS_INPUT_DIR` (default `data/analytics`) at
`/inputs`; see the [project overview](../../README.md#analytics-dashboard).

## Interpretation caveats

- **Attribution is inferred.** Voice matches are similarity, not verified
  identity; the dashboard exposes method (`manual`/`voice`) and confidence. Use
  manual labels for anything consequential.
- **Stance is model output**, by default from `zai-org/GLM-5.3-Flash`; stances
  are keyed by the served model, so switching models recomputes them. Evidence quotes are checked
  against the source text (`evidence_verified`), and the dashboard can filter to
  verified evidence. Source text is treated as untrusted data: it is delimited in
  the prompt and output is constrained to a JSON schema of known topics.
- **Sentiment/emotion models** are trained on social-media text and capture tone,
  not policy position.
- **Duplicated sources.** A meeting may appear both as a recording and as a
  transcript document; filter by `kind` rather than summing both.
- **Discovered aliases are heuristic.** A misheard name can map to the wrong
  alderperson; check `roster-candidates` and pin corrections with a manual CSV.
- Topic relevance thresholds depend on the embedding model; tune
  `--relevance-threshold` after inspecting results.
