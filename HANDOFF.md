# Handoff: plan, current code, and path forward

Context pack for coding this repo with Claude Code. Written 2026-11-14;
updated 2026-11-14 for the `chi-ciq-26-clean3` code.

This file is self-contained on purpose: the parts that matter for coding are
copied here. Doc names in **quotes** below refer to the fuller original plan
docs, which are now in **`docs/plan/`** (copied from the claude.ai project
"govbotgo"); start with `docs/plan/00-INDEX.md` for the reading order.

**What changed in this update:** the cluster is no longer a non-executable
draft — there is now a real, locally-validated `fuzzball.yaml` plus an
env-rendering tool (`chi-ciq-fuzzball-env`) and sharding helpers. The product
itself is unchanged (still the stance + sentiment dashboard; still voiceprint
attribution; no promise tracker / digests / `statements.jsonl` yet), so §2, §4
(product differences), §5 steps 1-2, and §6 still stand. §3, the cluster bullet
in §4, and §5 step 3 are updated below.

---

## 1. What this project is

- An application for Chicago City Council meetings, built for a
  1-day HPC/AI hackathon judged on **real cluster scaling**.
- Hardware: **two Lenovo ThinkStation PGX machines, one GPU each** (Arm / GB10
  chip, so container images must be **arm64**; the GPU shares memory with the
  system). A GPU **cannot be shared** between two jobs.
- Cluster runner: **CIQ Fuzzball** — you describe a pipeline (steps + their
  order + the CPU/GPU each needs) in one YAML file (a "Fuzzfile") and submit it;
  Fuzzball places each step on a machine. Think "Makefile + job scheduler."
- Election timing: Chicago municipal election is **Feb 23, 2027**, which is why
  anything public-facing needs to be trustworthy now.

## 2. The plan that was decided (source of truth for the product)

From **"Implementation Handoff"**, **"Product Direction & Scale Re-evaluation"**,
and **"Layer 2 Output Contract"** (all Nov 12-14):

**Core architecture principle:** *do the expensive reading once.* One heavy AI
pass over one year (2026) of meeting video produces a clean, linked, quote-
verified knowledge base of what was **said**. Every public view is then a thin,
cheap query on top of that.

**Lead product:** a **promise / commitment tracker** — collect every spoken
commitment ("we'll get back to you through the chair," "the rules are in draft,"
"by the next meeting"), then check whether it was resolved in a later meeting.
**Everyday backbone:** **five-minute plain-language meeting digests** (per
meeting: decided / argued / dodged / what residents said). Other views
(issue-timeline, "how did my alder show up") come later as more queries.

**Why this product:** ~95% of committee meetings record **no per-member vote**
(voice votes only), so the transcript is the only record of who shaped most
committee business. The unique value is the **deliberation** — reasoning,
questions, verbal commitments, dodges — which no clean minutes would give you.

**Neutral by construction:** same treatment for everyone, their own words,
verified quotes with a video link, **no scores or labels.**

### The data contract to build toward (from "Layer 2 Output Contract")

The heavy pass emits **one `statements.jsonl` per meeting** (one JSON object per
line). Each record = one unit of deliberation:

```json
{
  "statement_id": "2026-11-04-budget#0042",
  "meeting_id": "2026-11-04-budget",
  "date": "2026-11-04",
  "body": "Committee on Budget and Government Operations",
  "speaker": "Ald. Example",
  "speaker_confidence": "named | continuing | unconfirmed",
  "role": "alder | chair | department | public | clerk",
  "type": "statement | question | answer | commitment | vote_voice | public_comment",
  "topic": "police overtime",
  "department": "Police",
  "matter_id": "O2026-0027579 | null",
  "agenda_item": "file 23146-T1 | null",
  "quote": "exact words, verbatim from the transcript",
  "start_s": 3725.4,
  "end_s": 3790.0,
  "verified": true,
  "video_link": "https://vimeo.com/<id>#t=1h02m05s",
  "source": "whisper-large-v3+names",
  "model": "gpt-oss-20b@pass2"
}
```

Invariants (enforce in code, not in a prompt):
1. **Every quote verified word-for-word** against the transcript; drop any that
   isn't. `verified` is always true in the file.
2. **Never attribute to an unconfirmed speaker.** Emit the record with
   `speaker: null`, `speaker_confidence: "unconfirmed"` — searchable but
   unattributed. A wrong name is the one error that sinks trust.
3. **Nulls are explicit** — `matter_id`, `agenda_item`, `speaker` are null when
   unknown, never guessed.
4. **Provenance on every record** (`source`, `model`).
5. **Re-runs are idempotent** — re-running one meeting cleanly replaces its records.

`commitment` records carry extra fields (who promised, what, any deadline) — they
are the raw material for the promise tracker.

### The cluster story the plan wanted

Two phases, each its own Fuzzfile, because the GPU can't be shared:
- **Phase 1 (GPU):** transcribe + speaker-split the audio as a task array; a
  single `concurrency` switch (1 vs 2) is the whole "1-node vs 2-node" speed
  demo.
- **Phase 2 (CPU jobs calling a GPU model server):** extract statements / check
  quotes by sending requests to a **vLLM** model server; request rate triggers
  Fuzzball to start a second model copy on the second machine (the autoscaling
  demo).
- Model sized to fit **one GPU per machine** (plan's choice: ~20B, e.g.
  `gpt-oss-20b`).

Fuzzball rules that already bit the team (keep these):
1. Task arrays start at **1**, not 0.
2. Task arrays can't combine with `network` / `multinode`.
3. A `resource` block needs **both** `cpu` and `memory`.
4. Persistent volumes must exist **before** the workflow runs.
5. Reach a service by its endpoint address, never a hard-coded `node:port`.
6. Every step should **skip work already done** (idempotent).
7. Pre-load big images at bring-up (first use on a node pays a conversion cost).

---

## 3. What the current code actually does

The code is a well-engineered **multi-service analytics system** (separate Docker
containers, three independent PostgreSQL databases, a FastAPI web app):

- **downloads** — pulls meeting audio from Vimeo.
- **transcription** — speech-to-text + **diarization** (splits audio into
  anonymous "speaker 1/2/..." without names) into a "meetings" database.
- **documents** — loads agendas/ordinances into a "documents" database with
  pgvector (vector search = find text by meaning).
- **analytics** — batch job reading the two source DBs read-only, writing a third
  "analytics" DB. Stages: `sync` (statements become speaker *turns*) -> `roster`
  (seed 50 alders + mine misspelled names) -> `attribute` (map anonymous turns to
  people) -> `mentions` -> `sentiment` -> `emotion` -> `topics` (embed a fixed
  policy taxonomy, score relevance) -> `stance` (ask vLLM support/oppose/mixed/
  neutral per topic, with confidence + evidence quote) -> `refresh`
  (materialized views = pre-computed query results for the dashboard).
- **web** — transcript browse/search + a dashboard of per-person positions,
  per-topic stance trends, sentiment, and emotion.
- **cluster deployment** (new in this version) — `fuzzball.yaml` is now a real,
  locally-validated v4 Fuzzfile that runs the whole thing on the cluster: DB
  backup/migrate -> transcription (GPU task array, `concurrency: 2`) and document
  ingest in parallel -> the analytics stages as separate jobs -> backups ->
  a persistent `web` service with a user-scoped endpoint. Work is split into
  shards via `src/shared/sharding.py` and the `--shard-index/--shard-count`
  flags. `chi-ciq-fuzzball-env` renders/validates the secrets and env from a
  `.env` into a submittable file. The workflow starts an internal, persistent
  one-GPU vLLM service after both ingestion jobs; it serves
  `openai/gpt-oss-20b` and keeps its model cache on a persistent volume.
- **Recording metadata contract:** filenames are opaque identifiers, not dates
  or committee labels. Transcription stores NULL date/type unless supplied as an
  explicit reviewed override. Analytics infers missing metadata from source
  content, retaining unknown/conflicting values as unknown. Existing meetings
  databases require the migration relaxing date/type NOT NULL constraints.

One line: **the code is a per-representative policy-stance-and-sentiment
dashboard, now packaged to run as one Fuzzball workflow on the cluster.**

## 4. How the code differs from the plan

- **Lead product missing.** No commitment/promise tracker, no meeting digests.
- **Data contract not implemented.** No `statements.jsonl`; results are DB rows
  keyed to a policy taxonomy instead of verified deliberation units.
- **Speaker ID uses the risky method.** Attribution is mostly by **voiceprint**
  (acoustic similarity to hand-labeled examples) — the code's own README calls it
  "inferred, not verified." The plan's trust gate was **names spoken aloud**
  (chair recognition, `>> NAME:` caption labels) and *never* attaching an unsure
  name. This is the biggest risk to fix.
- **Adds scores the plan avoided.** Sentiment/emotion/stance scores are exactly
  the "scores or labels" the plan said to leave out; the sentiment/emotion models
  are trained on social-media tone, not policy.
- **Cluster: executable, but not yet the planned scaling demo.** `fuzzball.yaml`
  is now a real Fuzzfile (validated locally; **not yet run or measured on the live
  cluster**). But its shape differs from the plan's: the plan wanted two phases
  with vLLM started from the catalog *between* them and **autoscaling** to the
  second machine as the headline demo; the code now runs one persistent,
  in-workflow vLLM service after ingestion. With `concurrency: 2` hard-set on
  transcription, there is still **no exposed 1-node-vs-2-node switch and no
  autoscaling measurement** — the "real, measured scaling" story the HPC judges
  want still has to be wired.
- **Local stance model still too big by default.** Compose and the analytics
  README still default to `zai-org/GLM-5.3-Flash` (~306 GB, needs ~8 high-end
  GPUs), which does **not** fit the two one-GPU machines. The Fuzzball service is
  separately pinned to `openai/gpt-oss-20b` with tensor parallelism 1.

What the code got right and is worth keeping: "expensive pass once -> thin views"
structure; three isolated databases with a read-only web role; incremental /
resumable processing with results tagged by model; vLLM with schema-constrained
output and quote-checking; solid tests, logging, and module boundaries.

## 5. Path forward (priority order)

1. **Fix speaker attribution first — it gates anything public.** Make
   name-based attribution (chair recognition cues + `>> NAME:` labels) the
   primary source of truth; enforce in code: no confirmed name -> leave
   unattributed, don't guess. Keep voiceprint only as a secondary hint.
2. **Decide the product on purpose** (still-open decision, see §6). Either own the
   stance dashboard as the product (drop/de-emphasize sentiment+emotion, make the
   verified quote + video link the star), or return to the plan by adding two new
   analytics stages on top of the existing turns: a **commitment extractor** and a
   **per-meeting digest**. Recommended: do a thin version of both — keep the
   dashboard as the heavy HPC demo, add the commitment tracker as the distinctive
   public value.
3. **Turn the Fuzzfile into the measured scaling demo.** The Fuzzfile exists and
   validates; what's left is the HPC story itself: (a) run it on the live cluster;
   (b) expose a 1-node-vs-2-node comparison — either the transcription
   `concurrency` (1 vs 2) or vLLM autoscaling the plan described — and actually
   **measure** the speedup; (c) keep any deployed vLLM model within one GPU per
   machine (the current workflow selects `openai/gpt-oss-20b`).
4. **Before any public release:** human review + the City Clerk's OK; demo locally.

## 6. The one real open decision

Pick deliberately — it changes what to build:
- **Product-first:** do the least AI that delivers the deliberation layer
  (promise tracker + digests); don't inflate the cluster.
- **HPC-demo-first:** the civic data is the vehicle; the bar is "a real,
  well-measured 1-vs-2-node scaling demo," and the stance pass is a legitimate
  heavy job for that.

Both are honest; the code currently sits between them without choosing. Decide
this before sizing the cluster work.
