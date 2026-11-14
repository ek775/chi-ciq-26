> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Brainstorm Handoff" (created 2026-11-01).

# Brainstorm Handoff — govbot hackathon ideas

Handoff for a new brainstorming session. Written Nov 1, 2026, summarizing a long earlier session (Oct 20–27, 2026). **Updated later on Nov 1, 2026** after a second brainstorm session (new ideas, the Fuzzball catalog, and the Vimeo captions finding). It records the constraints, what was learned, every idea considered and why it was kept or dropped, so the next session doesn't repeat the same ground.

**Current pick (still not final):** "Know Your Alder", see section 5, now reframed as **"who said what, named correctly"**, since Vimeo already has automatic transcripts. Its transcription part depends on a 5-minute comparison (Implementation Plan, step 2).

---

## 1. The situation

- **Hackathon:** 2 days. Judges come from high-performance computing (HPC) and AI. Purpose (from the project's "Background and Purpose" doc): build an AI application or workflow that **demonstrates the advantage of clustered computing**.
- **Sponsors:** CIQ (makers of Fuzzball) and Lenovo (makers of the ThinkStation PGX).
- **Recommended tracks:** (1) Multi-node training, (2) Inference at scale, (3) Scientific AI, (4) Cluster & scheduling.
- **Team:** 3–4 software developers, **beginners in both AI and cluster computing**.
- **Hardware:** two Lenovo ThinkStation PGX (NVIDIA GB10: 20 Arm cores + one Blackwell GPU each, 128 GB shared memory at 273 GB/s, ConnectX-7 200 Gb/s link between them). Arm (arm64) software only.
- **Software:** CIQ Fuzzball (YAML "Fuzzfile" workflows: jobs, task arrays, services, GPU resources) and its official app catalog (section 7).
- **No access to the PGX or Fuzzball before the event.** All practice happens on laptops; day 1 starts with a one-hour bring-up.
- **Base:** the application should build on **govbot** (Chi Hack Night): every U.S. legislature as git repos of JSON (56 jurisdictions), a CLI, DuckDB loading, a small embedding tagger, an MCP server, `llms.txt`, a hearings feed (IL, WA, MA, AK, federal comment periods), and Chicago election races and ward maps. The user has said the idea may also go beyond govbot.

## 2. How the user wants to work

- **Plain words.** Replace jargon with simple terms; define anything technical. The user asked for background on containers and arm64; assume more such questions are welcome.
- **Honesty over enthusiasm.** Say plainly when an idea is weak or already done by someone else.
- **No invented problems.** The idea must solve a real, documented need.
- **AI must be organic.** A natural heavy AI workload, not AI bolted on.
- **Actionability matters.** Someone must be able to *do* something with the output while it still counts.
- **Two machines must genuinely be needed**, not a stunt. Simulated users count as a stunt unless the real load is plausible.
- **State the stance explicitly.** E.g. how the catalog is being used (section 7) — the user noticed when this was left implicit.

## 3. Criteria learned (use these to score new ideas)

| Criterion | Question to ask |
| --- | --- |
| Real value | Who needs it, and is there evidence? |
| Not on-demand | Could anyone get this by pasting one item into an AI assistant? If yes, pre-computing it adds little |
| Actionable | Who acts, what action, and in what time window? |
| Organic heavy AI | Is the AI model the main work, not an add-on? |
| Needs the cluster | Does it truly need two GPUs (a backlog too big for the time available, a deadline one machine can't meet, a model too large for one, or many *real* users at once)? |
| Measurable | Is there an answer key to score accuracy against? |
| Data access | Can the data be obtained legally before the event? |
| Beginner-feasible | Can beginners build it in 2 days, practicing only on laptops? |
| Not already done | Does a paid or free tool already do it? **Check the data source itself too** (e.g. Vimeo's own automatic captions) |
| Neutral | Politically neutral: facts, not ratings or verdicts |

## 4. Ideas considered and verdicts

| Idea | Verdict | Main reason |
| --- | --- | --- |
| Ask all 56 legislatures (search + Q&A over bill text) | Dropped | Paid tools exist; the index fits on one machine; no real need for a cluster |
| Copycat bill finder (copied "model bills" across states) | Dropped as main idea | Real value (USA Today's 2019 "Copy, Paste, Legislate": ~10,000 copied bills, 2,100+ became law, 150 computers for months), but the AI wasn't organic: core is word matching |
| Bill summaries / "fact sheets" for every bill | Dropped | About 25 states already publish official summaries; Plural sells AI summaries; summaries are cheap on demand; low trust for legal text. Remaining value: filterable fields (who a bill affects, money, places) |
| Policy-topic coding for researchers (Comparative Agendas Project codes) | Dropped | Already done: Dee & Garlick (2025) coded Congress and all 50 states, 2009–Feb 2023, ~87–90% agreement with humans; small classifiers suffice, so no cluster need; data license is CC BY-NC-SA |
| Hearing transcripts for a state (Washington TVW) | Dropped for now | Copyrighted video ("all rights reserved"); Quorum sells nationwide transcripts; Illinois doesn't post committee video at all |
| Policy spread between states (embeddings) | Not pursued | Research value; AI seen as light |
| Legislator ideology scores from votes | Not pursued | Patchy vote data; politically sensitive |
| Bill passage prediction | Not pursued | Paid tools exist; weak cluster need |
| Very large model split across both PGX | Not pursued as main | Value mostly to the sponsors' audience, not the public; hard for beginners |
| **Chicago: Know Your Alder** | **Current pick** | See section 5 |
| Chicago: "Before the vote" alerts | Future work | Most actionable, but light compute; limited by Chicago's 48-hour posting rule |
| Chicago: "What was decided near me" | Not pursued | Light compute |
| Chicago: "Are residents heard?" (public comment analysis) | Stretch | Builds on transcripts |
| One issue across all levels (state bills + city decisions) | Future work | High value, too much integration for 2 days |
| *Session 2 (Nov 1):* | | |
| Every meeting, live, in your language (live transcription + translation of simultaneous public meetings) | Not chosen | Deadline-driven cluster need, but one PGX may handle Chicago's real number of simultaneous meetings; translation quality hard to check; needs a Whisper container (not in catalog) |
| What's being built near me (zoning applications read by a vision-language model, mapped by ward) | Best brand-new direction, not chosen | Actionable before approval; catalog `rag` entry reads scanned PDFs; cluster need only if the backfill is large |
| Every small government around Chicago, read (agendas/minutes of many agencies) | Not chosen | Real scale and least-covered level; scraping many sites is a 2-day trap (lean on City Bureau's City Scrapers); check for existing projects first |
| Candidates in their own words (2027 forums transcribed, answers side by side) | Not chosen | Reuses the pipeline; forum videos belong to organizers; audio total may be small |
| Live by day, backfill by night (live meetings + Know Your Alder backlog sharing two machines) | Kept as an idea for the scheduling story | Strong cluster story (priorities, preemption); depends on Fuzzball features and the live part's load |
| Ask City Hall (Q&A with citations over agendas/minutes/zoning, built from catalog entries) | Dropped | Same weakness as "Ask all 56 legislatures": index fits on one machine; two machines only needed with simulated users. One PGX serves ~30 users at once with an 8B model |
| Petition signature checker for candidates | Parked | Timing gone (Chicago petitions due Oct 26, 2026); restricted voter-file data; could be used to knock rivals off the ballot |
| Briefing packets for alders; simulated "AI residents" | Dropped | Light compute / invented problem |

## 5. Current pick: Know Your Alder

- **What:** a resident picks their ward and sees what their alder said in City Council budget hearings, word for word, linked to the moment in the City Clerk's official video. Aimed at voters before Chicago's **Feb 23, 2027** municipal election.
- **Data:** the City Clerk's Vimeo archives (full council 2011–2026 plus 23 committees). **Downloads are enabled** by the Clerk (the user checked). Budget committee: 173 videos (12 in 2026, 29 in 2025), each 1–11 hours.
- **New finding (Nov 1): Vimeo already shows automatic transcripts** ("English (auto-generated)"). A sample showed: no speaker changes marked (the chair and a public speaker run together in one block), names misspelled ("Chair Irvin", very likely Ald. Jason Ervin), misheard words ("signify by saying I. I." for "aye, aye"), timestamps only every ~10–15 seconds.
- **Reframed value:** not "a transcript" but **who said what, named correctly, linked to the exact moment**. Vimeo's text becomes the baseline in every accuracy check.
- **Workload:** speech-to-text (Whisper) with a name hint from the alder roster, plus a voice-based speaker split (pyannote diarization), both as task arrays across both PGX; language-model steps (naming speakers, pulling quotes) on the catalog's autoscaled vLLM behind the LiteLLM gateway. Hackathon set: 41 videos, roughly 80–160 hours of audio, about 1.6–8 hours on two PGX at an assumed 10–25× real time (3.2–16 hours on one).
- **Decision gate:** compare Vimeo, Whisper, and Whisper + name hint on 5 hand-transcribed minutes. If Whisper isn't clearly better, fall back to Vimeo's text as input and move the heavy work to the language model over the whole archive (Implementation Plan, section 8).
- **Weak points:** if Vimeo's AI captions are as good as Whisper, transcription has no case; if transcription is much faster than assumed, the two-machine story gets thin (fallback: the whole 173-video archive); the GB10 is new and ready-made images may not support it; publishing transcripts needs the City Clerk's OK; quotes must never be attributed to the wrong person.
- **Docs:** see section 9.

## 6. Key facts gathered (with sources)

- **Bill volume:** ~135,500 state bills introduced in 2025, ~29,000 enacted ([FiscalNote](https://fiscalnote.com/2025-state-sessions-recap)). Congress: 133 laws in 2025 vs 2,441 federal agency rules ([CEI](https://cei.org/publication/chapter-12-10kc-2026/)).
- **Local government:** 91,438 local governments in 2025 ([Census Bureau](https://www.census.gov/library/publications/2026/econ/govtorg2225.html)).
- **govbot data:** measured bill text averages ~2,400–4,100 tokens per bill (WY, CO, IL samples); true bill total is unknown, roughly 150,000–250,000 (an earlier "149k" undercounted).
- **PGX speed** (sold by NVIDIA as DGX Spark; [LMSYS](https://www.lmsys.org/blog/2025-10-13-nvidia-dgx-spark)): Llama 3.1 8B reads ~8,000 tokens/s and writes ~370 tokens/s at 32 requests in flight; a 70B model reads ~800 tokens/s. Writing is limited by memory bandwidth.
- **Whisper speed** on common GPUs: ~15–25× real time per stream ([RunPod](https://www.runpod.io/articles/guides/best-gpu-for-whisper)); not yet measured on GB10.
- **Ready-made images for GB10 lag behind:** "most pre-built images either do not exist yet or lag behind current vLLM releases"; NVIDIA's NGC vLLM image works but trails upstream; community builds exist ([Techno Tim, updated June 2026](https://docs.technotim.com/posts/vllm-gb10-docker/); [vLLM issue #31128](https://github.com/vllm-project/vllm/issues/31128)). vLLM's docs only explain building an arm64 image yourself ([vLLM Docker docs](https://docs.vllm.ai/en/stable/deployment/docker/)).
- **Official bill summaries in govbot data:** real summaries in the abstract field for CA, CO, DE, FL, HI, IN, KY, NY, OR, RI; summary documents linked for ID, WY, MD, CT, LA, MI, AZ, FL, MO, OH, TX, KS, AK, TN.
- **Existing tools:** Quorum (AI bill tracking, nationwide hearing transcripts since Nov 2025, paid), Plural (AI summaries, paid), CalMatters' Digital Democracy (free, California only; 800,000+ users in 2024), Chicago Councilmatic (free legislation tracking, no transcripts), City Bureau's Documenters (people taking notes at meetings) and City Scrapers (open-source meeting listings for Chicago and Cook County), Council Data Project (open-source city meeting transcripts, ~17 cities in 2023), **Vimeo's automatic captions on the City Clerk's videos**.
- **City Bureau's Open Gov Report Card:** graded 148 Chicago and Cook County agencies on sharing agendas and minutes; Chicago averaged a D-.
- **Chicago 2027 election:** petitions were due Oct 26, 2026; ballot certification Dec 17, 2026; election Feb 23, 2027; runoff Apr 6, 2027 ([Axios](https://www.axios.com/local/chicago/2026/10/19/chicago-mayoral-hopefuls-file-petitions-amid-controversy)).
- **Democratic tech with evidence:** Pol.is / vTaiwan (26 legislative proposals in 5 years), Community Notes bridging ranking, Ireland's citizens' assembly, risk-limiting audits (Arlo), ERIC voter-roll matching.
- **Chicago participation limits:** items posted only 48 hours ahead; remote testimony registration by the morning of the day before; ~10 public speakers per meeting ([BGA/Civic Federation/LWV letter, 2024](https://civicfed.org/sites/default/files/2024-02/Transparency_Letter_BGA_CF_LWV.pdf)).
- **Sponsor messaging:** Lenovo positions the PGX for "local prototyping, fine-tuning, and inferencing" that later moves to bigger hardware; CIQ says "one workflow definition runs across every cloud and on-premises environment".

## 7. The Fuzzball app catalog and how we use it

Source: [ctrliq/ciq-fuzzball-catalog](https://github.com/ctrliq/ciq-fuzzball-catalog/tree/main/applications), 58 entries, read Nov 1, 2026.

- **What's there:** a general `vllm` entry plus 11 per-model presets (gpt-oss 20B/120B, Gemma, Llama 4 Scout, Nemotron, Qwen coder, Ministral, Mistral Medium) with a built-in **autoscaler** (adds a copy when ≥2 requests wait, removes copies after 5 idle minutes, can scale to zero; a copy can span several machines); a **LiteLLM gateway** that discovers every running copy and wakes scaled-to-zero ones; **`rag`** (haiku.rag: reads PDF/Word/HTML including OCR of scans, hybrid search with citations, exposed as MCP tools; the only entry that explicitly offers arm64 and names DGX Spark); **`hermes-agent`** (wires itself to the gateway); **task arrays** in Blender and Boltz-2 entries; a job-dependency example; classic HPC apps (GROMACS, LAMMPS, OpenFOAM, WRF, ANSYS…).
- **What's missing:** no Whisper, audio or speech entry.
- **The message it sends:** CIQ wants Fuzzball to be where you run self-scaling AI services.
- **Our stance (agreed with the user):** **use catalog entries for the plumbing** (model serving, scaling, routing, document reading, batch splitting) and **be different in the application** (the civic problem, real Chicago data, the interface, the measurements). Fill a catalog gap (e.g. Whisper) only when the idea truly needs it. Reasons: 2-day time limit for beginners; the judges built these parts; their health checks and scaling rules are already tuned.
- **Arm64 caution:** default images are `vllm/vllm-openai:v0.28.0`, `ghcr.io/berriai/litellm:v1.97.0` and `litellm-database:v1.97.0`; arm64/GB10 support not confirmed. The image is just a setting (`VllmImage`), so a backup image can be swapped in without losing the entry. Backups: NVIDIA's NGC vLLM container, a pinned community GB10 build. Last resort: Ollama on the PGX.

## 8. Lessons that shaped the thinking

1. In legislation, almost every high-value AI product is already sold to paying customers (Quorum, Plural, LegiScan). The honest gap is **free, open, and local**, for residents, small newsrooms and nonprofits.
2. Pre-computing something only helps if it **can't be done on demand** (audio/video, cross-item questions, large scale).
3. **Bills are a small share** of what affects daily life; budgets, agency rules and **local government** matter more, and local is the least covered.
4. Many promising tasks need **light** compute; heavy compute usually comes from audio/video, very large models, or many simultaneous users.
5. For the judges, the technical story (measured speedups, an "optimization ladder" of techniques, accuracy checks) matters as much as the idea; real-world value needs to be credible, not unique.
6. A cluster is truly needed in two situations: **a backlog too big for the time available**, or **a deadline** one machine can't meet. Search/Q&A apps usually fail both unless real user load is high.
7. **Check what the data source already provides** before building on it: the Vimeo captions changed the pick's value proposition. An existing AI output is a useful baseline to beat, not only a threat.

## 9. Where things are

- **govbotgo project docs:** "Background and Purpose", "Know Your Alder - Plan & Architecture", "Know Your Alder - Implementation Plan" (updated Nov 1 with the Vimeo finding, the decision gate, voice split, catalog services and image backups), and this handoff.
- **Live design doc** (Claude Docs): "Know Your Alder — Plan & Architecture", https://claude.ai/artifact/GdTSZAbatwM8FWAQUid3wB — **not yet updated** with the Nov 1 changes.
- **Code folder** on the user's computer: `/Volumes/My Shared Files/InVitro/govbot-chi` with `IMPLEMENTATION_PLAN.md` (an older copy; replace with the project version), `CLAUDE.md`, `docs/design.md`, `.gitignore`, and one sample video (Oct 13, 2026 Budget committee hearing, 240p, 205 MB).
- **govbot repo:** `/Volumes/My Shared Files/InVitro/govbot`.

## 10. Open questions

- **Decision gate result:** is Whisper with a name hint clearly better than Vimeo's automatic captions on 5 hand-transcribed minutes?
- **Getting Vimeo's caption files:** yt-dlp returned "403 Forbidden" on the showcase link (also: zsh needs the link in quotes). Next: update yt-dlp, use the direct video link, then `--referer` or `--cookies-from-browser`.
- **Organizers:** which catalog entries were tested on the PGX; catalog installed?; Fuzzball version; driver/CUDA version; venue internet; hackathon date.
- **Catalog images on arm64:** results of `docker manifest inspect` for the vLLM and LiteLLM images.
- **City Clerk:** can transcripts be published after the event; are human-made captions available for any meetings (a free answer key)?
- Is there still an idea that beats Know Your Alder on **both** a heavy, organic AI workload **and** genuine need for two machines?
