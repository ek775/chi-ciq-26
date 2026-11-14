> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Product Direction & Scale Re-evaluation" (created 2026-11-12).

# Product Direction & Scale Re-evaluation

Written Nov 12, 2026. Captures a working session that re-examined the project after two changes: the theme is **no longer tied to "Know Your Alder"** (it can be anything that benefits the public or helps officials), and the processing scope is **one year of video (2026), not the full archive back to 2011**. Companion to "Brainstorm Handoff", "Application Drafting Handoff", and the two Know Your Alder docs, which still hold the hackathon constraints (2-day event, HPC/AI judges, two ThinkStation PGX, Fuzzball, govbot base).

---

## 1. What the data actually is (verified against the connected archive)

The `chicago-city-council-archive` folder, Nov 11 2025 – Nov 11 2026:

- **261 meetings** in the Clerk's inventory; **43 are cancellations**, so ~218 real meetings.
- **202 meetings have transcripts** (Vimeo auto-captions; 3 of the ones read also carry speaker labels like `>> JASON ERVIN:`).
- **10,522 documents**: agendas, the actual ordinance text, committee reports, EDS filings, exhibits, plus OCR of 1,288 scanned pages.
- Per meeting: `meeting.json` (body, date, agenda, attendance, files), `Documents/`, `Transcripts/`, and where they exist `Votes/` (roll-call rows: voter name + vote) and `Legislation/` (full matter records: sponsors, category, status, actions, title).
- So for most meetings, four things line up: what was **scheduled** (agenda), **said** (transcript), **on paper** (documents), and **voted** (vote sheets).

A teammate note (`PRESENTATION-STRATEGY.md`) already proposes a "What Changed at City Hall?" legislative-trail browser. The directions below lean harder on the spoken record, which only the transcript provides.

## 2. The unique value of the video (what no structured record gives you)

The structured data answers *what happened*. The transcript is the only source for *how it happened*:

- **The reasoning behind a vote** — not just that Alder X voted yes, but what they worried about and what they extracted first.
- **The questions, and the officials' answers** — e.g. why 91% of $426M West Side flood money went to infrastructure while residents still have mold; the department's reply or non-reply.
- **Verbal commitments** — "we'll get that to you through the chair," "the rules are in draft." These appear in no document.
- **What got dodged** — a non-answer is only visible in the exchange (the Amtrak yard hearing).
- **Resident testimony as spoken** — only 8 written comments are filed; the rest is voice only (though noisy — see §3).
- **Off-record moments** — e.g. the Feb 2025 episode where the mayor's office asked to edit out pre-meeting audio.

**Strongest single finding (verified):** at the committee level — where most shaping happens — **~95% of committee meetings (179 of 188 real ones) record no per-member vote at all.** Committees dispose of business by voice vote ("all in favor say aye… the ayes have it"), often a whole block of items at once. Full Council is the opposite (nearly all items roll-called). So for most committee business, the paperwork can't tell you who backed or resisted an item — the transcript is the only member-level record that exists.

## 3. Scale, re-evaluated honestly (one year is small)

- One year ≈ ~200 transcribed meetings, ~450–600 hours of video, a few million words → **order 4–6 million tokens of transcript**.
- A single read by a small (8B) model: ~10–15 min on **one** machine. A careful large (70B) model: ~1–2 hours on one machine.
- **Conclusion: the size of one year does not force two machines.** A laptop could grind through a year overnight.

What this means for the HPC story — two different claims need different amounts of data:
- *"The backlog is too big for one machine in the time available."* One year does **not** support this. Weak; a sharp judge will catch it.
- *"Here is a demanding AI job, measured, that two machines do ~2× faster than one."* One year is **more than enough** to demonstrate this cleanly. This is what HPC/AI judges actually want.

To make two machines genuinely earn their place (and to make the autoscale-to-second-node moment visible rather than finishing in minutes), make the **work per item** heavier — a bigger model, multiple passes (extract → link → verify → summarize), and joining the document text — **not** by padding. The quality bar justifies the heaviness; keeping GPUs busy does not.

## 4. Architecture principle

**Do the expensive reading once.** One heavy pass over the year produces a clean, linked knowledge base: each statement tied to speaker, agenda item, document, and vote, with every quote verified word-for-word against the transcript and a video link. Every public-facing app is then a thin, cheap view on top. That one pass is also the honest heavy job for the cluster.

## 5. Product use cases (public-facing, strongest first)

1. **"Did they actually do it?" — promise tracker.** Collect every spoken commitment, then check whether it was resolved in a later meeting. Live threads already found: West Side flood money; ~7,000 illegal sweepstakes machines the city gets only a $150 sticker fee from. **Most novel; only possible with the video.**
2. **"The council meeting in five minutes" — plain digests.** Each 2–9 hour meeting → a short, readable summary: decided / argued / dodged / what residents said. Broadly useful; clearly heavy.
3. **"The story of one issue."** Full-year timeline of one topic (flooding, SRO housing, the Amtrak yard) through the debate, not just record numbers. (This extends the teammate's trail-browser idea.)
4. **"How did my alder show up?"** Election-timely (Feb 23 2027). Better than a vote list — because most committee votes aren't named — built from what they asked, pushed for, stayed quiet on, and whether they attend.

Recommendation: build the one heavy reading pass as the foundation; **lead the demo with the promise tracker** (most surprising, clearest "only because we processed the video"), keep **five-minute digests** as the everyday backbone.

**Carry-through caution:** most transcripts don't label speakers and names get misheard. A wrong attribution is the one error that sinks trust, so correct speaker ID + verified quotes + a video link to check must be part of the heavy pass, not an afterthought.

## 6. The honest gut-check: is this over-engineering?

Partly, and the "partly" matters:
- **True:** the "who supported this item" gap is a record-keeping failure. If the city recorded committee votes and published labeled transcripts, that chunk needs no AI. Build heavy machinery to reconstruct it and you're over-compensating.
- **Survives the critique:** (a) the year already happened — better records are prospective, and the election is now; (b) minutes record outcomes by design, not reasoning, questions, promises, or dodges — the deliberation layer is not something clean records would hand you; (c) clean input still isn't synthesis across meetings. Also, Chicago isn't about to fix this (City Bureau graded local agencies a D- on open government).
- **Discipline:** make the AI as heavy as the *accuracy bar* demands (right speaker, verified quotes), never heavier to fill GPUs. Adding passes only to keep machines busy is the over-compensation tell.
- **Reframe:** taking the critique seriously sharpens the design toward the one layer that is both most useful and most legitimately heavy — the deliberation over the existing year. Drop the parts that are really just a records fix.

**Decide on purpose which game you're playing.** Product-first → do the least AI that delivers the deliberation layer; don't inflate the cluster. HPC-demo-first → the civic data is a legitimate, sympathetic vehicle, and the bar is "is this a real, well-measured scaling demo," not "could policy fix it." Both can be honest; pretending one is the other is not.

## 7. Future vision (seed → something bigger)

The durable asset isn't a Chicago app; it's a **method**: turn messy meeting video + scattered records into a clean, linked, checkable account of what a government said and did.

- **Many cities.** ~91,000 US local governments; almost all post video, almost none searchable (the open Council Data Project reaches ~17 cities). At this scale the compute stops being a stretch: thousands of governments, weekly video, re-processed as models improve, is a genuinely endless heavy job. The hackathon is then the *unit that scales*, demonstrated once — the clean bridge from "small demo" to "real HPC."
- **The accountability loop** (the distinctive bet): measure what officials say they'll do against what they actually do, across time and governments — a substrate reporters, watchdogs, and residents build on. Clean records would never give this.
- **From rear-view to upcoming:** use the 48-hour agenda posting to warn residents before a decision, with everything said about it before.
- **Across levels** (hard, someday): link city decisions to state/federal action on the same issue, using govbot's 56 legislatures.

**The real constraint on "bigger" is organizational, not technical:** who runs it in year three, how it stays trusted and non-partisan, and never misattributing a quote at scale (one wrong name becomes a lawsuit, not a bug). Volunteer civic-tech burns out; Council Data Project's modest reach is the cautionary tale.

**One-line thesis worth planting:** *an accountability layer for local democracy that measures what officials say they'll do against what they actually do — rebuilt from the spoken record that currently evaporates — and that works in any city with a camera in the room.* Chicago before the February election is the proof; the clustered pipeline is what makes it national.

## 8. Open decisions

- Product-first vs HPC-demo-first (§6) — pick deliberately; it changes what to build.
- Which use case leads the demo (recommendation: promise tracker + digests).
- How heavy to make the per-item work (model size, number of passes, whether to fold in document text) so two machines are genuinely needed without padding — size it from the real token count.
- Speaker-ID quality bar: the non-negotiable for any public accountability claim.
