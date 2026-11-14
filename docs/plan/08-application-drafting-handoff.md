> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Application Drafting Handoff" (created 2026-11-02).

# Application Drafting Handoff — Know Your Alder

Handoff for the conversation drafting the **hackathon application text**. Written Nov 1, 2026; updated later Nov 1 (single text box, technical details added, scope widened beyond budget hearings); **updated Nov 3** (the language model runs *after* transcription, not alongside it; proposals on the first batch and on candidate forums recorded).

For the full idea history, read "Brainstorm Handoff". For the build, read "Know Your Alder - Implementation Plan". Note: "Know Your Alder - Plan & Architecture" (snapshot of Oct 26) is **out of date** on two points: it says the Clerk's videos have no captions (they have Vimeo auto-captions), and it treats speaker separation (diarization) as a stretch (now core).

---

## 1. The task

The application has **one text box** for "what we are building". It must convince reviewers (HPC and AI people) that the idea is **useful**, **feasible**, and clearly **AI inference at scale**.

**User's instructions:**
- Don't parrot the hackathon's product list; describe the *shape of the workload* (models, data size, how work is split, what's measured) instead.
- Plain words. Honest claims only; no unmeasured numbers.
- Include impact. Keep only the most important information.
- Don't limit the scope to budget hearings: they were the first batch, not the whole idea.

## 2. Current drafts

**Title:** Know Your Alder

**One-line summary:**
> Know Your Alder turns Chicago City Council hearing videos into a searchable record of what each alder said, with every quote linked to the exact moment in the official video, so residents can hear their alder's own words before the February 2027 election.

**Text box (latest, Nov 3):**
> Chicago elects all 50 alderpersons on February 23, 2027. Votes are public, but what an alder pushed for or questioned is only in the City Clerk's hearing videos: full council meetings since 2011 and 23 committees, with single hearings running up to 11 hours. The automatic captions don't say who is speaking and misspell names. **Know Your Alder** turns these hearings into a searchable record of who said what. A resident picks their ward and sees their alder's own statements, each checked word for word against the transcript and linked to the moment in the official video. Reporters and advocates can search the same record. Under the hood it's batch inference with three model types: speech recognition with officials' names as a hint, voice-based speaker separation, and an open-weight language model that names speakers and extracts quotes. Audio is cut into equal chunks spread across both nodes so no GPU sits idle; once transcription frees the GPUs, the language model runs as a service that scales from one node to both as requests arrive. We start with the 41 budget hearings from 2025–2026 and scale toward the full archive as measured throughput allows. We'll report throughput and GPU utilization at each optimization step, one node versus two, a projection for the whole archive, and accuracy against hand-labeled transcripts, with the existing captions as the baseline. The videos are downloadable, and the pipeline is built on laptops beforehand. It's open source, built on govbot, and neutral: alders' own words, no ratings.

**If the 5-minute test sends the project to the fallback** (Vimeo's text as input), replace the "Under the hood…" and "Audio is cut…" sentences with:
> Under the hood it's batch inference over the existing captions and audio: voice-based speaker separation runs first as parallel batch jobs on both nodes, then an open-weight language model, served as a service that scales from one node to both, names speakers and extracts quotes across the full archive.

**Track:** Inference at scale (primary). Cluster & scheduling (secondary, if two are allowed).

## 3. Proposed but not yet agreed by the user

- **First batch = "the budget fight"** instead of "the 41 budget hearings": hearings, Finance committee and full Council meetings on the 2026 and 2027 budgets (the Council passed an alternative 2026 budget without the mayor's head tax; the 2027 budget has an $882M gap and a revived head tax, voted on weeks before the election). Wording if accepted: "We start with the hearings and Council meetings on the last two city budgets, the biggest fight of this term, and scale toward the full archive as measured throughput allows." Suggested check first: use Vimeo's captions to count how often alders are named per hour in each meeting type.
- **Candidate forums** to cover challengers and open seats: same pipeline on forum videos (e.g. League of Women Voters of Chicago; 12 wards in 2023, videos linked in the Illinois Voter Guide; site says "All rights reserved", so ask permission). Would let the text say: "Every candidate gets the same format: incumbents' hearing records plus all candidates' forum answers." Contacting the League would also supply the missing user evidence.
- **Roll-call votes** next to incumbents' statements (City Clerk legislative records, Chicago Councilmatic).

## 4. Before submitting

- **Hackathon date vs the election (most important).** The date is still unknown. If the event is after Feb 23, 2027 (or after the Apr 6 runoff), the voter framing no longer works; lead with the accountability record for residents, reporters and advocates instead.
- **Run the 5-minute Whisper vs Vimeo test first** (Implementation Plan, step 2), or the "speech recognition" claim may become false. Use the fallback sentence above if needed.
- **"the pipeline is built on laptops beforehand"**: only true if done by submission; otherwise "will be built".
- **"full council meetings since 2011 and 23 committees"**: from the City Clerk's video page as read in October; recheck the count.
- **Council Data Project** (open-source city meeting transcripts, ~17 cities in 2023): check whether it covers Chicago; a reviewer might know it.

## 5. Decisions made while drafting (don't undo without the user)

| Decision | Why |
| --- | --- |
| Don't say hearings are the most important thing for voters | Not true; hearings show what votes can't (what an alder pushed for or questioned) |
| Scope: first batch = 41 budget hearings from 2025–2026; scale toward the full archive as measured throughput allows | Matches the design doc ("decide data size after the day-1 speed measurement"); budget was the starting batch, not the limit |
| Don't state archive size in hours ("thousands of hours") | Not measured; the design doc only says "probably thousands" for all committees |
| Mention 2011 only as the archive's extent, not as voter relevance | Most current alders took office in 2019 or later; old statements must be linked to the person, not the ward number |
| Dropped "track department promises" | User judged the promises aren't in the hearings |
| One short beyond-election clause (reporters and advocates search the record) | Real but modest; value needs to be credible, not unique |
| Don't claim plain transcription is new | Vimeo already shows automatic captions |
| Accessibility (better captions) not claimed | Only valid if the 5-minute test shows ours is better |
| Technical block kept short, no product names | Reviewers need the workload shape to see "inference at scale"; product names don't add that |
| **The language model runs after transcription, not "while"/"alongside" it** (Nov 3) | Each vLLM copy and each Whisper task claims a whole GPU, and each PGX has one; the service scales from one node to both once batch work frees the GPUs |

## 6. Facts available to cite (verified Nov 1, 2026)

- Election Feb 23, 2027; runoff Apr 6, 2027; all 50 wards on the ballot.
- Of the current 50 alders, 11 have served since 2011 or earlier; 30 took office in 2019 or later, 15 since 2023 (Wikipedia member list).
- Ward numbers and boundaries changed in the remaps effective 2015 and 2023.
- Budget committee archive: 173 videos, each 1–11 hours; 41 from 2025–2026. Full council since 2011 plus 23 committee archives. City Clerk enabled downloads on Vimeo.
- Vimeo captions are "English (auto-generated)": no speaker changes, names misspelled ("Chair Irvin", very likely Ald. Jason Ervin), misheard words, timestamps every ~10–15 seconds.
- Catalog `vllm` entry autoscaling (read Nov 3): adds a copy at ≥30 requests a minute through its built-in proxy, removes one after 5 idle minutes; each copy claims a whole GPU.

## 7. Known limits (state openly if there's room)

- Covers incumbents only; challengers have no hearing record (unless candidate forums are added, section 3).
- Nothing published without human review and the City Clerk's OK; the demo runs locally.
- Quotes with an uncertain speaker are left out rather than guessed.

## 8. Still missing from the application

1. **Evidence someone wants it:** one sentence from a real user (Chi Hack Night organizer, City Bureau Documenters, a local reporter, or the League of Women Voters).
2. **What judges see at demo:** hearings processed, a working ward page, a speedup chart, a model-copies-over-time chart, an accuracy table against the captions.
3. **Team:** three roles; honest that the team is new to HPC, with a plan and fallbacks.
4. **Risks and fallbacks in one line.**
5. **After the event:** open source, lives with govbot / Chi Hack Night.
