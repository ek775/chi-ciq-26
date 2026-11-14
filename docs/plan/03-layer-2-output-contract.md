> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Layer 2 Output Contract" (created 2026-11-13).

# Layer 2 Output Contract

Written Nov 13, 2026. For the person building **Layer 2 (the heavy pass)**. This is the single source of truth for what the heavy pass must emit, so Layer 2 can be built now without waiting on which views (Layer 3/4) ship. Companion to "Implementation Handoff" and "Product Direction & Scale Re-evaluation" (both Nov 12) — read those for the why; this doc is the *what*.

**Why this doc exists:** every public-facing view is a thin query over one file, `statements.jsonl`. The schema of that file is the contract between the expensive half (Layer 2) and the cheap half (Layer 3/4). Lock the schema and Layer 2 is fully specified — the view designs can be decided later and change nothing about the heavy pass.

---

## 0. The one principle that governs everything

**Re-running the heavy pass is the single expensive operation in the whole system.** So the output must be a **superset** of what any plausible view needs — capture generously at the statement grain, including fields no current view uses. Adding a view later must always be a new query, never a re-run. When in doubt, emit the field.

The only thing that forces a re-run later is discovering a statement-grain field that wasn't captured. Everything else (new views, new topic groupings, cross-meeting analysis) is a read over what's already there.

---

## 1. The record grain (lock this first — everything depends on it)

One record = **one unit of deliberation**: a single speaker's statement, question, answer, commitment, or public comment.

- Not a whole speaking turn if it spans topics.
- Not a single sentence.
- Pin the exact unit down on 2–3 sample meetings and do not change it after. The grain is what every view counts and groups by, so a change means re-deriving everything.

---

## 2. The record schema

The canonical shape of one line in `statements.jsonl`:

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

Field notes:

- `statement_id` — stable and unique. Views and the commitment-resolution linker reference records by this. A re-run must reproduce the id or cleanly replace it.
- `meeting_id`, `date`, `body` — tie the record to `meeting.json`.
- `speaker` — the attributed name, or `null` when unconfirmed (see §3). Never a guess.
- `speaker_confidence` — the trust gate. `named` = a name was said aloud just before (chair recognition, or the speaker naming themselves); `continuing` = the same voice kept talking from a `named` turn; `unconfirmed` = no name evidence.
- `role` — alder / chair / department / public / clerk.
- `type` — the kind of utterance. `commitment` records are the raw material for the promise tracker; `vote_voice` captures voice-vote moments ("the ayes have it").
- `topic`, `department` — what it's about and who's being questioned. Start the topic vocabulary loose; it can be refined by a later query without a re-run.
- `matter_id`, `agenda_item` — the **join keys**. These link the quote to the ordinance text, documents, and vote sheets. `null` when the link couldn't be made; never guessed. Chairs read file/record numbers aloud ("document number O2026-0027579"); match them to `recordNumber` in `Legislation/`.
- `quote` — the exact words, verbatim (see §3).
- `start_s`, `end_s` — seconds from the start of the whole meeting (not the piece). `start_s` builds the `#t=` video link.
- `verified` — always `true` in the file (see §3).
- `video_link` — Vimeo link with the timestamp, built from `start_s`.
- `source`, `model` — provenance on every record: which transcript source and which model/pass produced it. Lets views show confidence and lets one pass be re-run selectively.

**For `type: "commitment"` records, capture extra** (the promise tracker is the lead view and is only as good as these): who made the commitment, what was promised, and any stated deadline ("by the next meeting", "the rules are in draft", "we'll get that to you through the chair"). Put these in the record rather than forcing the view to re-parse the quote.

---

## 3. Invariants (enforce in code, not in the prompt)

1. **Every quote is verified word-for-word.** `quote` must be a verbatim substring of the joined transcript after lowercasing and collapsing whitespace. If it doesn't match, drop the record. There is no such thing as an unverified quote reaching Layer 3 — `verified` is always `true` in the file.
2. **Never attribute to an `unconfirmed` speaker.** The record is still emitted (searchable, unattributed) with `speaker: null` and `speaker_confidence: "unconfirmed"`. A wrong name is the one error that sinks trust — an unattributed quote is fine, a misattributed one is not.
3. **Nulls are explicit.** `matter_id`, `agenda_item`, `speaker` are `null` when unknown — never filled by guessing.
4. **Provenance on every record** (`source`, `model`).
5. **Re-runs are idempotent.** Re-running one meeting cleanly replaces that meeting's records.

---

## 4. File and partition layout

- **One `statements.jsonl` per meeting**, e.g. `data/statements/<meeting_id>.jsonl`. Records appended as produced.
- Partition by meeting so a killed run loses only one meeting and re-runs in isolation; the view layer just globs all files.
- This also lets the demo page fill in live as meetings finish.
- Format is JSON Lines (one self-contained JSON object per line, no array wrapper) specifically so records can be appended one at a time, read back a line at a time, streamed through Unix tools, and survive a single malformed line.

---

## 5. Second output: per-meeting digests

Phase-2 pass 5 also emits a short **digest per meeting** — a small separate JSON file per meeting: decided / argued / dodged / what residents said. Lock a minimal shape for it; it's the everyday-backbone view's input.

---

## 6. What to lock now vs. what waits on the view decision

**Lock now — this unblocks Layer 2 completely (none of it depends on which views ship):**
- the record grain (§1)
- every field in the schema (§2)
- the invariants (§3)
- the file/partition layout (§4)

**Does NOT block Layer 2 — leave extensible:**
- the topic taxonomy's granularity — start loose, refine later with a query
- any view-specific tags
- the **cross-meeting work**: resolving a commitment against later meetings, and multi-issue synthesis. These are *derived* from `statements.jsonl` after the fact — Layer 3/4 queries, not heavy-pass output. Layer 2 only needs to capture enough at statement grain to make them possible (hence commitments carrying who/what/when).

**Net:** with the schema, invariants, and file layout locked, all five Phase-2 steps (transcribe/clean + speaker-ID → extract → link → verify → digest) can start now.
