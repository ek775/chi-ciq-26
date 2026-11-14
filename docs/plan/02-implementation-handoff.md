> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Implementation Handoff" (created 2026-11-12).

# Implementation Handoff

Written Nov 12, 2026. For a fresh chat that will **build**. This carries the decisions and the data contract so you don't re-derive context. Read these first, in order:

1. **"Product Direction & Scale Re-evaluation"** (Nov 12) — the current direction after the pivot. Most important.
2. **"Know Your Alder - Implementation Plan"** — still the source of truth for **hardware, Fuzzball, containers, arm64, the vLLM catalog, the 2-day playbook**. Reuse all of that; only the *application* on top has changed.
3. **"Brainstorm Handoff"** and **"Application Drafting Handoff"** — idea history and constraints.
4. **"Background and Purpose"** — hackathon framing (2 days, HPC/AI judges, two ThinkStation PGX, CIQ Fuzzball, build on govbot).

**One-line summary of where we landed:** stop building "a transcript viewer." Do **one heavy AI pass** over one year of Chicago City Council video that produces a clean, quote-verified, linked knowledge base of what was *said*; then build thin public views on top. Lead view: a **promise/commitment tracker** ("did they do it?"), with **five-minute plain-language meeting digests** as the everyday backbone.

---

## 1. Direction (decided) and what's still open

**Decided:**
- Theme is no longer tied to "Know Your Alder." Frame: an **accountability layer built from the spoken record** — the thing no structured record or clean minutes would give you.
- Scope: **one year of video, 2026** (not back to 2011).
- Architecture: **do the expensive reading once** → knowledge base → thin views (§3).
- The unique, defensible value is the **deliberation**: reasoning, questions, verbal commitments, dodges. Verified fact: **~95% of committee meetings record no per-member vote** (179 of 188 real meetings), so the transcript is the only member-level record of who shaped most committee items.

**Still open — a builder must get these answered before sizing the cluster work:**
- **Product-first vs HPC-demo-first.** Product-first → least AI that delivers the deliberation layer; don't inflate the cluster. HPC-demo-first → civic data is the vehicle, bar is "real, measured scaling." Pick on purpose.
- **How heavy to make per-item work** (model size, number of passes, whether document text is folded in) so two machines are genuinely needed **without padding**. Size it from the real token count (see §4).
- **Which views to ship** beyond the promise tracker + digests.

## 2. The data (verified against the connected archive)

Folder: `chicago-city-council-archive/` (device "zs-virtual-machine-local", under `know-your-alder/`). Coverage Nov 11 2025 – Nov 11 2026.

- `index.json` — summary + per-meeting manifest for all 261 meetings. **Start here.**
- `Meetings/<YYYY-MM>/<date body [meetingId]>/` each contains:
  - `meeting.json` — body, date, agenda, attendance, file list, video link.
  - `manifest.json` — source links, downloaded files, caption tracks, failures.
  - `Transcripts/` — WebVTT + timestamped plain text. Most are Vimeo auto-captions (errors; usually **no speaker labels** — but a few budget hearings DO carry `>> NAME:` labels).
  - `Documents/` — agendas, ordinance text, committee reports, EDS, exhibits (+ OCR text sidecars).
  - `Votes/` — roll-call rows `{voterName, vote, personId}` (mostly City Council only).
  - `Legislation/` — matter records `{recordNumber, title, filingSponsor, sponsors[], matterCategory, type, status, actions[]}`.

Counts: 261 meetings (43 cancelled → ~218 real), 202 with transcripts, 10,522 documents.

**Join keys that make the knowledge base possible:**
- **Spoken file numbers → matters.** Chairs read item numbers aloud ("file number 23146-T1", "document number O2026-0027579"); these match `recordNumber` in `Legislation/`. This links a transcript passage to the agenda item and its documents.
- **Speaker name → roster/votes.** Match `>> NAME` or chair recognition ("Alderperson X, you're recognized") to the alder roster and `Votes/` `voterName`.
- **Meeting body + date** ties everything to `meeting.json`.

## 2a. Measured token budget (Nov 12, 2026)

Measured from `index.json` (video durations + caption-cue counts), calibrated at 7.57 words/cue against 68 read transcripts; sanity-checks at ~6,900 words/hour.

**2026 scope:** 189 real meetings (224 scheduled), **175 with transcripts, 465 hours of video**, ~3.2M spoken words → **~4.3M tokens of transcript.** (Earlier docs' "4–6M" estimate → this is the measured figure.)

What one full read of the year costs, using the Implementation Plan's PGX read speeds (~8,000 tok/s for 8B, ~800 tok/s for 70B):

| One read of the year | 1 machine | 2 machines |
| --- | --- | --- |
| Small (8B) model | ~9 min | ~5 min |
| Large (70B) model | ~1.5 h | ~45 min |

**Implication: the transcript alone (4.3M tokens) is too small to force two machines.** The levers that make the cluster genuinely necessary, highest first:
1. **Model size** — 8B → 70B is ~10×, and justified (accurate speaker ID + extraction want the bigger model).
2. **Passes** — extract → link → verify → digest → synthesis ≈ 5 reads, several re-reading context (~2× effective input).
3. **Documents** — the big **unmeasured** lever: 10,522 docs (council meetings carry 717 legislation files each). Folding matched ordinance text in per item could rival or exceed the transcript tokens. **Most likely to make two machines truly necessary. Measure this next.**

Stacked (70B × ~5 passes × ~2× context ≈ ~40M effective tokens): very roughly **10–15 h on one machine, ~5–8 h on two**; adding documents credibly fills an overnight two-node run. That is the honest path to "needs the cluster" — quality + documents, not padding.

Separate clean HPC demo regardless: **re-transcribing the 465 hours of audio is a tidy 1-vs-2-node speedup measurement** on its own (only needed if not using Vimeo captions).

## 3. Architecture to build

**Phase A — the heavy pass (run on the cluster, the HPC story).** For every meeting, produce a `statements.jsonl` where each record is one unit of deliberation:

```json
{
  "meeting_id": "...", "date": "2026-11-04", "body": "Committee on Budget...",
  "speaker": "Ald. Example", "speaker_confidence": "named|continuing|unconfirmed",
  "role": "alder|chair|department|public|clerk",
  "type": "statement|question|answer|commitment|vote_voice|public_comment",
  "topic": "police overtime", "department": "Police",
  "matter_id": "O2026-00xxxxx or null", "agenda_item": "file 23146-T1 or null",
  "quote": "exact words", "start_s": 3725.4,
  "verified": true, "video_link": "https://vimeo.com/<id>#t=1h02m05s"
}
```

Rules that are non-negotiable:
- **Every quote verified word-for-word against the transcript** (drop any that isn't). No invented quotes.
- **Never attribute to an unconfirmed speaker.** `unconfirmed` stays searchable but unattributed. A wrong name is the one error that sinks trust.
- `commitment` records are the raw material for the promise tracker — tag "we will / we'll get back / in draft / by <date>" statements and who made them.

Passes (each adds load, the honest lever for "needs two machines"): (1) transcribe/clean + speaker-ID, (2) extract statements, (3) link to matters/documents/votes, (4) verify quotes, (5) per-meeting digest + per-issue synthesis. Use a larger model and/or fold in document text to make the job genuinely fill an overnight two-node run — **only to the extent accuracy needs it**, never padding.

**Phase B — the views (thin, cheap, can run on a laptop for the demo):**
- **Promise tracker** — list commitments; for each, search later meetings for resolution; show status + both video moments.
- **Five-minute digest** — per meeting: decided / argued / dodged / residents said.
- **Issue timeline** — one topic across the year (known live threads to prototype on: **West Side flood money $426M**; **~7,000 illegal sweepstakes machines / $150 sticker fee**; **SRO housing ordinance**; **Amtrak rail yard at 33rd & Canal**).
- (Optional) **"How did my alder show up?"** — questions asked, topics engaged, attendance; election-timely (Feb 23 2027).

## 4. Suggested first build steps

1. **Load `index.json` into a table** (DuckDB fits the govbot stack): one row per meeting with paths, body, date, caption presence, vote presence.
2. **Token budget: measured (see §2a) — ~4.3M transcript tokens for 2026.** Remaining to measure: the **document corpus** (the lever that decides whether two machines are genuinely needed).
3. **Build the file-number → matter linker** and test it on a few committee transcripts (zoning reads file numbers constantly).
4. **Build the Phase-A extractor** on 2–3 meetings against Ollama locally; lock the `statements.jsonl` schema as the contract between the heavy pass and the views.
5. **Prototype the promise tracker** on the flood-money and sweepstacks threads end to end — proves the value before scaling.
6. **Then** take the cluster/Fuzzball/vLLM mechanics straight from "Know Your Alder - Implementation Plan" (containers, arm64, two-phase run, autoscaling, the optimization-ladder and 1-vs-2-node measurements).

## 5. Carry-through cautions

- **Speaker ID is the quality gate**, not an afterthought — most transcripts are unlabeled and names get misheard.
- **Don't manufacture heaviness.** Make the AI as heavy as the accuracy bar demands; adding passes only to keep GPUs busy is the "over-compensation" tell (see "Product Direction" §6).
- **Neutral by construction:** same treatment for everyone, their own words, verified quotes with a video link, no scores or labels.
- **Before any public release:** human review and the City Clerk's OK (312-742-5375). The demo runs locally.
- Hardware/Fuzzball realities (arm64 image risk, GPU can't be shared, autoscale may barely trigger on a short job) are already documented in the Implementation Plan — don't re-solve them here.
