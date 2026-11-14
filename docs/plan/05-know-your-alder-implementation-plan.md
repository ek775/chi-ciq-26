> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Know Your Alder - Implementation Plan" (created 2026-10-27).
> NOTE: This predates the mid-November pivot (docs 01-04). The *application* on
> top changed (promise tracker + digests, not a ward transcript viewer), but the
> hardware, Fuzzball, container, arm64, vLLM-catalog and 2-day-playbook detail
> here is still the source of truth. Read it with that framing.

# Know Your Alder — Implementation Plan

What we can build **now, on laptops**, before the hackathon, so that day 1 on the two ThinkStation PGX computers is mostly "copy in and run".

Companion to the design doc "Know Your Alder — Plan & Architecture" (in the govbotgo project). That doc says *what* and *why*; this one says *how*, in build order, with a "done when" test for every step, plus the **playbook** for the weeks before and the two event days (sections 5 and 6).

Written Oct 27, 2026. **Updated Nov 1 and Nov 3, 2026** (see "What changed" below). The hackathon date isn't fixed yet, so the weeks before the event are counted back from it (week −4 … week −1). Event-day times assume **day 1 runs 9:00–18:00 and the hackathon ends at 12:00 on day 2**, with presentations before then.

---

## What changed on Nov 3, 2026

**Roles rebalanced** (latest; see the Claude Doc "Rebalancing Team Roles: Know Your Alder"). Role B had ~4–7 days of pre-event work and nearly all the risk. Now:
- **A — Audio + speakers** also owns step 7 (naming speakers), so the voice split feeds straight into speaker naming.
- **B — Cluster plumbing** owns only steps 12–13 and day-1 cluster operations, with deliberate slack for surprises.
- **C — Quotes + page + story** also owns step 8 (statements and the quote check).
- **D — Measurement** owns step 11 (`bench.py`, `gpu_log.sh`, all charts) as well as step 10 (accuracy).
- **A pairs with B** in week −2 and runs the dress rehearsal's commands in week −1, so two people can run Fuzzball.

**The event ends at 12:00 on day 2.** Day 2 is only about three hours, so the schedule in section 6 was rebuilt:
- **All computing that the demo depends on finishes on day 1:** transcription by 16:00, the language-model phase by 18:00.
- **Speed tests are shorter** (10:30–12:00) and the production run starts at 12:00.
- **The production size is chosen to fit a 4-hour window** (section 6.3); anything more runs overnight only as a bonus, if allowed.
- **Rehearsal 1 and a first backup video happen on day 1 evening;** day 2 morning is final checks, freeze at 10:00, rehearsal, then the presentation.

**The language-model service** (from a close read of the catalog's `vllm` entry, `applications/vllm/values.yaml` and `template.yaml` in `ctrliq/ciq-fuzzball-catalog`):

1. **No separate gateway.** The `vllm` entry has its own request proxy built in (`Proxy=true` by default) that gives one address in front of every copy of the model. One catalog command replaces the two from Nov 1. The separate `litellm` entry is only needed if several model workflows must share one address; we don't.
2. **The language model and Whisper can't share the GPUs at the same time.** Each vLLM copy ("replica") claims a whole GPU, so does each Whisper task, and each PGX has one GPU. The run is therefore **two phases**: (1) transcription and voice split on both GPUs; (2) then the vLLM service starts with one copy and the autoscaler adds a second copy on the other PGX as requests arrive. Our Fuzzfile is split into two workflows to match (step 12).
3. **Lower GPU memory use on this hardware:** `GpuMemoryUtilization=0.7`. The entry's own notes say 0.9 is rarely free on DGX Spark-type machines, whose GPU shares memory with the rest of the system.
4. **Day-1 trap fixed:** the vLLM test at bring-up must be stopped afterwards, or it holds a GPU and halves transcription speed.
5. **New chart:** model copies over time, to show the scale-up and scale-down (step 11).
6. **Honest caveat:** for 41 hearings the language-model phase takes only minutes, so the second copy may barely start. Scaling shows clearly only with a bigger batch (more hearings, the full archive, or the fallback in section 8).

**The playbook** (sections 5 and 6): a role table with backups, a week-by-week plan before the event with a milestone each week, hour-by-hour plans for both event days, go/no-go gates with fallbacks, **benchmark sets** so speed tests don't eat the whole day (step 14), and a 4-minute demo script.

## What changed on Nov 1, 2026

1. **Vimeo already has transcripts.** The City Clerk's videos come with Vimeo's **automatic** captions ("English (auto-generated)"). A sample from a Budget hearing showed: no marks where one speaker stops and another starts, names misspelled ("Chair Irvin", very likely Ald. Jason Ervin), misheard words ("signify by saying I. I." for "aye, aye"), and timestamps only every ~10–15 seconds.
2. **So the value is now "who said what, named correctly", not "a transcript".** Plain transcription is no longer something we can claim as new.
3. **Vimeo's text becomes the baseline** in every accuracy check. The demo shows a measured improvement over what really exists.
4. **Two additions give Whisper a real job:** a hint listing alder names (so names come out spelled right), and a voice-based speaker split ("diarization": a model that marks where one voice stops and another starts).
5. **A decision gate early on (step 2):** compare Vimeo's text and our Whisper on the same 5 hand-transcribed minutes *before* investing in the transcription pipeline. If Whisper isn't clearly better, we switch to the fallback (section 8).
6. **Language-model servers come from CIQ's Fuzzball catalog** instead of being set up by hand (refined Nov 3: one `vllm` entry with its built-in proxy). Our scripts don't change: they only need the proxy's address.
7. **Container backups.** The GB10 chip is new and ready-made images often lag behind. We check the catalog's images before the event and bring backups (step 13, section 7).

---

## 1. What can be done now, and what can't

| Can do now, on laptops | Must wait for the event |
| --- | --- |
| Download videos, convert to audio, and download Vimeo's captions | Real GPU speeds on the PGX |
| The 5-minute comparison: Vimeo vs Whisper (decision gate) | Fuzzball behavior (storage, scheduling, service addresses, whether a GPU can be shared) |
| Every pipeline script (cut, transcribe, voice split, join, speakers, quotes, check) | Whether each arm64 image really runs on the GB10's GPU |
| Whisper and the voice split on small samples (slow, but the same code) | Catalog vLLM (with its built-in proxy) and its autoscaling on the PGX |
| Language-model steps with Ollama, including sending many requests at once | The real speed tests and the optimization ladder |
| Ward lookup and the Streamlit page | — |
| Hand labels for the accuracy checks | — |
| Timing log, chart scripts, evaluation scripts | — |
| Our two containers for both chip types; both Fuzzfiles drafted; arm64 checks of catalog images | — |
| A timed dress rehearsal of the whole pipeline and the demo, on laptops | — |

**Rule for everything we write:** each step is a plain Python script that reads files and writes files, and takes `--slice N` where work can be split. The same command then runs in a laptop loop, by hand on a PGX, or in a Fuzzball task array. Nothing in the code knows which.

---

## 2. Folder layout

```
govbot-chi/
  IMPLEMENTATION_PLAN.md      this file
  README.md                   how to set up and run (step 0)
  pyproject.toml              Python dependencies
  manifest/
    hearings.csv              one row per hearing (step 1)
    bench_3h.csv              benchmark sets for speed tests: ~3, 6 and 12 hours of audio,
    bench_6h.csv                each including one long hearing (step 14)
    bench_12h.csv
  ref/
    alders.csv                ward -> current alder, name variants (step 6)
    name_hint.txt             short list of names for Whisper's hint (step 4)
  labels/
    check_a/                  hand transcripts of 5-minute passages (steps 2, 10)
    check_b_turns.csv         ~100 hand-labelled speaking turns (step 10)
  src/alder/
    ingest.py                 video -> audio, measure length (step 1, A)
    captions.py               Vimeo captions -> segments in our format (step 1, A)
    cut.py                    audio -> 30-min pieces, balanced slices (step 3, A)
    transcribe.py             Whisper on one slice, with name hint (step 4, A)
    voices.py                 voice-based speaker split on one slice (step 5, A)
    join.py                   pieces -> one transcript + voice turns per hearing (step 5, A)
    speakers.py               name who is speaking (step 7, A)
    statements.py             pull out each alder's statements (step 8, C)
    quotecheck.py             keep only quotes found word for word (step 8, C)
    wards.py                  ward lookup (step 9, C)
    bench.py                  timing log for every run (step 11, D)
    evaluate.py               accuracy checks A-D (steps 2, 10, D)
    charts.py                 ladder, per-machine timeline, model copies, accuracy (step 11, D)
  app/
    streamlit_app.py          the ward page (step 9, C)
  deploy/                     all B (step 12), except gpu_log.sh (D, step 11)
    Dockerfile.tools          ffmpeg + Python tools (CPU)
    Dockerfile.whisper        Whisper + voice split (GPU), based on NVIDIA's PyTorch image
    fuzzfile_audio.yaml       phase 1: cut, transcribe, voice split, join
    fuzzfile_text.yaml        phase 2: speakers, statements, quote check, page
    catalog.md                exact catalog commands and values we use
    run_event.sh              runs phase 1, starts vLLM, runs phase 2, stops vLLM
    gpu_log.sh                logs what runs on each GPU every 15 s
    run_local.sh              laptop stand-in for both phases
  demo/
    script.md                 the 4-minute demo script (section 6.5, C)
    backup.mp4                recorded backup of the demo (day 1 evening, re-recorded day 2)
  tests/                      small tests with tiny sample files
  data/                       NOT in git: videos, audio, captions, pieces, outputs
    video/  audio/  captions/  pieces/  transcripts/  voices/  turns/  statements/  runs/
```

Keep `data/` out of git (add it to `.gitignore`). Everything else goes in git.

---

## 3. File formats (agree on these first)

Everything is plain CSV or JSON Lines (one JSON object per line), so each step can be tested on its own. These formats are the contracts between roles: A's turns feed C's statements, and every script logs through D's `bench.py`.

**`manifest/hearings.csv`** (the benchmark sets use the same columns)

| Column | Example |
| --- | --- |
| hearing_id | `2026-10-13-budget` |
| date | `2026-10-13` |
| committee | `Budget and Government Operations` |
| title | as shown on Vimeo |
| vimeo_url | `https://vimeo.com/<video id>` |
| video_file | `data/video/....mp4` |
| audio_file | `data/audio/2026-10-13-budget.flac` |
| captions_file | `data/captions/2026-10-13-budget.vtt` (empty if none) |
| duration_s | filled in by `ingest.py` |

**Pieces:** `data/pieces/<hearing_id>/<nnn>.flac`, plus `data/pieces/slices.json` mapping slice number → list of pieces.

**Transcript segments** (`data/transcripts/raw/<hearing_id>/<nnn>.jsonl`, then joined to `data/transcripts/<hearing_id>.jsonl`). Vimeo's captions are converted to the same format with `"source": "vimeo"`, so every later step and check can run on either.

```json
{"hearing_id": "2026-10-13-budget", "start_s": 3725.4, "end_s": 3731.9,
 "text": "Thank you, Chair. My question is about overtime.", "confidence": -0.21,
 "source": "whisper-large-v3+names"}
```

`start_s` and `end_s` are seconds from the start of the *whole hearing*, not the piece.

**Voice turns** (`data/voices/<hearing_id>.jsonl`): where one voice stops and another starts. Labels are anonymous; they only mark the boundaries.

```json
{"hearing_id": "...", "start_s": 3724.8, "end_s": 3790.0, "voice": "p012_SPEAKER_03"}
```

Labels are only consistent *within* one piece (`p012_` = piece 12). That's fine: names come from step 7, not from the voice labels.

**Speaking turns** (`data/turns/<hearing_id>.jsonl`, written by A, read by C):

```json
{"hearing_id": "...", "start_s": 3725.4, "end_s": 3790.0, "speaker": "Ald. Example",
 "ward": 7, "confidence": "named", "evidence": "Chair: 'Alderperson Example, you're recognized.'"}
```

`confidence` is `named` (someone said their name just before), `continuing` (same voice kept talking), or `unconfirmed`. Only `named` and `continuing` turns can produce quotes.

**Statements** (`data/statements/<hearing_id>.jsonl`, written by C):

```json
{"hearing_id": "...", "ward": 7, "alder": "Ald. Example", "start_s": 3725.4,
 "topic": "police overtime", "department": "Police",
 "quote": "My question is about overtime.", "verified": true,
 "video_link": "https://vimeo.com/<id>#t=1h02m05s"}
```

**Run log** (`data/runs/runs.csv`): one row per task. Columns: run_id, step, slice, machine, model, batch_size, audio_seconds, start_time, end_time, seconds, errors. The speed charts are drawn from this file only.

**GPU log** (`data/runs/gpu.csv`): one row per machine every 15 seconds. Columns: time, machine, process (whisper / pyannote / vllm / none), GPU use %, memory used. The "model copies over time" chart is drawn from this file plus the language-model scripts' request log (`data/runs/requests.csv`).

---

## 4. Build steps

Roles (section 5.1): **A** = audio + speakers, **B** = cluster plumbing, **C** = quotes + page + story, **D** = measurement (accuracy and speed). With only three people, section 5.1 says who covers D's work. Effort is rough, for someone new to this.

### Step 0 — Setup (everyone, 1–2 hours)

- Install Python 3.11+, `ffmpeg`, Git, Docker, Ollama and `yt-dlp` (keep it updated: `brew upgrade yt-dlp`; Vimeo changes often and old versions get "403 Forbidden").
- Create the folder layout above, `pyproject.toml`, `.gitignore` (with `data/`), and a short `README.md`.
- Python packages: `transformers`, `torch`, `accelerate`, `soundfile`, `jiwer` (word error rate), `pandas`, `streamlit`, `openai` (talks to Ollama and the vLLM proxy alike), `shapely` (ward lookup), `matplotlib`, `webvtt-py` (reads caption files), `pyannote.audio` (voice split).
- Hugging Face account and token; accept the terms of `pyannote/speaker-diarization-3.1` on its model page (it won't download otherwise).
- **Done when:** `python -c "import transformers, torch, pyannote.audio"` works and `ollama run llama3.1:8b "hello"` answers. On a laptop with 8 GB of memory, use a ~3B model instead.

### Step 1 — Ingest: video to audio, plus Vimeo's captions (A, 3–4 hours)

- `ingest.py --video <file> --hearing-id <id>`:
  - measure the length: `ffprobe -v error -show_entries format=duration -of csv=p=0 <file>`
  - convert to 16 kHz mono FLAC (what Whisper expects, lossless): `ffmpeg -i <video> -vn -ac 1 -ar 16000 -c:a flac <audio>.flac`
  - fill in `duration_s` and `audio_file` in `manifest/hearings.csv`
- Download Vimeo's captions. **Always put links in quotes** (zsh treats `?` as a wildcard), and use the video's own link:
  ```
  yt-dlp --write-subs --write-auto-subs --sub-langs all --skip-download "https://vimeo.com/<video id>"
  ```
  If refused: update yt-dlp, then add `--referer "https://vimeo.com/showcase/8925576"`, then `--cookies-from-browser chrome`. Last resort for the step-2 test: copy the transcript panel's text for the chosen minutes by hand.
- `captions.py --hearing <id>`: convert the `.vtt` file into transcript segments (section 3) with `"source": "vimeo"`.
- Run both on the video already in this folder: `2026_october_13_-_committee_on_budget_and_government_operations (240p).mp4` (205 MB).
- **Done when:** that hearing has a FLAC file, its real length is in the manifest, and its Vimeo captions are in our segment format. **Report the length to the team:** it replaces the 2–4 hour average assumed in the design doc.

### Step 2 — Decision gate: Vimeo vs Whisper on 5 minutes (A + D, 1 day) **DO EARLY**

The point: find out whether our transcription is clearly better than what Vimeo already gives, before building the rest of the transcription pipeline.

- Pick **5 minutes** of the sample hearing with several speakers, alders' names, and numbers (dollar amounts, department names).
- Two people (D and A, or D and C) transcribe it by hand, word for word, marking every change of speaker and spelling names from the official roster. Save to `labels/check_a/`. (This is also the first passage for check A.)
- A runs three versions on the same 5 minutes:
  1. Vimeo's automatic captions (from step 1)
  2. Whisper `large-v3`, plain
  3. Whisper `large-v3` with the name hint (step 4)
  On a laptop, `large-v3` on 5 minutes is slow but fine; if it's too slow, use `large-v3-turbo` and note it.
- D writes the first version of `evaluate.py compare --minutes <start>-<end>`, which prints for each version:
  - **words wrong** (word error rate from `jiwer`, after lowercasing and removing punctuation)
  - **names right**: of the people named in the hand transcript, how many are spelled correctly
  - **numbers right**: of dollar amounts and counts in the hand transcript, how many are correct
- **Suggested rule** (the team can change it before seeing results, not after):
  - **Keep the Whisper plan** if version 3 is clearly better: about a quarter fewer words wrong than Vimeo, **or** noticeably more names right.
  - **Otherwise switch to the fallback** in section 8: Vimeo's text becomes the input, and the heavy work moves to the language model over the whole archive.
  - One 5-minute passage is a small sample: if results are close, add a second passage from a different hearing before deciding.
- **Done when:** the table of three versions exists and the team has written down which path it takes.

### Step 3 — Cut into balanced pieces (A, 3–4 hours)

- `cut.py --manifest <csv> --minutes 30 --overlap 5 --slices 16`:
  - cut every hearing in the given manifest into 30-minute pieces that overlap by 5 seconds (`ffmpeg -ss <start> -t <length>`); `--manifest` lets the same script cut a benchmark set or the full set
  - spread pieces across 16 slices so every slice has about the same total audio. Simple method: sort pieces longest first, then put each into whichever slice currently has the least audio.
  - `--whole-files` instead puts each whole hearing in one slice: needed for speed test 2 (whole files vs equal pieces)
  - write `data/pieces/slices.json`
- **Done when:** a test with made-up lengths (one 11-hour and many 1-hour hearings) gives slices within ~10% of each other, and the pieces cover every second of the source.

### Step 4 — Transcribe one slice, with the name hint (A, 1 day)

- `transcribe.py --slice N --model openai/whisper-large-v3 --batch-size 8 --hint ref/name_hint.txt`:
  - use the Hugging Face `pipeline("automatic-speech-recognition", ...)` with timestamps on; ask for **word-level timestamps** (`return_timestamps="word"`) so "Watch" links land on the quote, not 10 seconds before it
  - **name hint:** pass the text of `ref/name_hint.txt` as Whisper's prompt (`prompt_ids` from the processor's `get_prompt_ids`, given through `generate_kwargs`). Whisper reads it as "text that came before", which nudges spelling. Keep it short (Whisper only uses ~200 tokens of prompt): alders' last names, the Budget Director, and frequently named departments, e.g. "Budget hearing. Chair Ervin. Alderpersons Conway, …".
  - choose the device automatically: `cuda` (PGX), else `mps` (Apple Silicon), else `cpu`
  - add each piece's start time so timestamps are relative to the whole hearing
  - write one JSONL per piece; skip pieces that already have output, so a rerun only does what's missing
  - log one row per piece to `runs.csv` via `bench.py` (D's helper)
- Laptops: practice with `whisper-base` or `whisper-small` on a 10–20 minute sample. At the event: `whisper-large-v3` and `whisper-large-v3-turbo`.
- **Done when:** a 10-minute sample produces readable text with word timestamps, the hint is visibly used (names from the hint appear correctly), and rerunning does nothing.

### Step 5 — Voice split and join (A, 1–1.5 days)

- `voices.py --slice N`: run `pyannote/speaker-diarization-3.1` on each piece in the slice; write voice turns (section 3) with labels prefixed by the piece number; log to `runs.csv` like transcription. Same `--slice` scheme, so it runs as its own task array next to transcription.
- `join.py --hearing <id>`:
  - transcripts: put pieces in order; where two pieces overlap, keep segments from the earlier piece up to the overlap midpoint and from the later piece after it
  - voice turns: same rule at the seams
- **Done when:** a hearing cut into pieces and joined has no repeated sentences at the seams (check by eye at 3 seams), and on the step-2 passage most hand-marked speaker changes have a voice-turn boundary within ~2 seconds.

### Step 6 — Alder roster and name hint (C, 2–3 hours)

- govbot's elections seed (`govbot/actions/scrape-elections/elections_seed.json`) has all 50 ward races for the **February 23, 2027** ballot, but **not the names of current alders** (its candidate lists are empty).
- Build `ref/alders.csv` by hand from the City Council's official members list: ward, full name, and the forms used in hearings ("Alderperson X", "Alderman X", "Alderwoman X", "Ald. X", "Alder X", "Chairman X"). Add committee chairs and the Budget Director, who speak often.
- Write `ref/name_hint.txt` from it (see step 4).
- **Done when:** 50 rows, each checked against the official list by a second person (confirm the Budget chair's name and spelling, e.g. Ervin).

### Step 7 — Name the speakers (A, 1–2 days)

A owns this because it builds directly on A's voice split. Write it in two versions so C isn't blocked:
- **v1 (week −3):** transcript only (Whisper or Vimeo text), no voice turns, against Ollama. Enough for C to start step 8 and for the first end-to-end milestone.
- **v2 (week −2):** adds voice turns and `--parallel`.

`speakers.py --hearing <id> --server <address> --model <model> --parallel 32`:
- the address is Ollama on laptops (`http://localhost:11434/v1`) and the vLLM entry's built-in proxy on the PGX (B provides it, step 12); nothing else changes
- **`--parallel N`: send up to N requests at once** (a thread pool around the `openai` client). This keeps the GPU busy, and on the PGX it pushes the request rate past the autoscaler's trigger (30 requests a minute) so the second copy starts. On a laptop, use `--parallel 2`.
- log one row per request (time sent, time answered, tokens) to `data/runs/requests.csv` for D's "model copies over time" chart
- input is the transcript **plus voice turns** (v2): each voice turn becomes one block of text, so speaker boundaries come from the voice split, not from the model's guess
- send ~5-minute windows, with the roster in the instructions; ask the model to name each block using **only** names said aloud in the text (for example, the chair recognizing someone)
- ask for JSON only (structured output) so answers parse reliably
- rule in code, not in the prompt: no name said → `unconfirmed`; same voice label continuing in the same piece → `continuing`
- **Done when:** on one labelled hearing, every `named` turn is correct in a spot check of 20 turns, with no wrong names (wrong names are worse than `unconfirmed` ones), and `--parallel 4` against Ollama gives the same output as `--parallel 1`.

### Step 8 — Statements and the quote check (C, 1–2 days)

C owns this because it decides what appears on the page. Start in week −3 on step 7 v1 output, or on the hand-labelled turns from step 10, so there's no waiting on A.

- `statements.py --hearing <id> --server <address> --parallel 32`: for each `named`/`continuing` turn by an alder, ask the model for that alder's questions and statements: topic, department being questioned, the **exact quote**, and its start time. Same `--parallel` and request log as step 7 (copy A's thread-pool helper rather than writing a new one).
- `quotecheck.py --hearing <id>`: keep a statement only if its quote appears in that turn's transcript text after lowercasing and collapsing spaces. Mark it `verified: true`, and build the Vimeo link from the word-level start time (`#t=<h>h<m>m<s>s`). Count proposed vs kept for check C.
- **Done when:** on one hearing, 100% of kept quotes are found in the transcript (by construction), and a person reading 20 of them against the video agrees each is a fair statement of what was said.

### Step 9 — Ward lookup and the ward page (C, 2–3 days)

- `wards.py`: load Chicago's official ward boundaries (City of Chicago data portal dataset `p293-wvbd`, the same source govbot's `scrape-maps` uses; govbot's script also has point-in-ward code to reuse). Given a latitude and longitude, return the ward.
- **Address entry: be careful.** govbot's own AI-assistant server deliberately never sends a home address to a geocoding service. Version 1 of the page is a **ward picker** (1–50), with a link to the city's official "find my ward" page. Typing an address is an optional extra; if added, say on the page where the address is sent.
- `app/streamlit_app.py`: pick a ward → alder name → their 2027 race (from govbot's seed) → their verified statements, newest first, each with topic, department, quote and "Watch" link. Add a search box over all transcripts, and a footer: "Video: Office of the City Clerk, City of Chicago."
- The page reads whatever is in `data/statements/` when it loads, so it fills in as hearings finish during the event.
- **Done when:** the page runs on a laptop from the sample hearing's output, and every "Watch" link opens Vimeo at the right moment.

### Step 10 — Accuracy checks (D; everyone helps with labels; 1–2 days)

Every check has **Vimeo's automatic captions as the baseline column**.

- **Check A, transcription:** 10 five-minute passages across different hearings (quiet, noisy, fast talkers); the step-2 passage is the first. Two people transcribe each by hand into `labels/check_a/`. `evaluate.py check-a` reports words wrong, names right and numbers right for Vimeo, Whisper plain, and Whisper with the hint.
- **Check B, speakers:** label ~100 turns in `labels/check_b_turns.csv` (start time, true speaker). `evaluate.py check-b` reports (1) speaker changes found: share of hand-marked changes with a voice-turn boundary within 2 seconds (Vimeo has none, so 0% for the baseline), and (2) names: correct, wrong and `unconfirmed`. Hand these labels to C early: they're also C's test input for step 8.
- **Check C, quotes:** proposed vs kept, from `quotecheck.py`.
- **Check D, model size:** check A for `large-v3` vs `large-v3-turbo`.
- Pick the 10 check-A passages and the check-B turns **from the newest hearings in the full set**, so they're processed early in the production run (section 6.3 runs newest first) and can be scored on day 1.
- **Done when:** all four commands print a result on laptop outputs. The real numbers come from PGX runs at the event.

### Step 11 — Timing log and charts (D, 1 day)

- `bench.py` (**write it first, in week −4**: every other script logs through it): a small helper that appends one row per task to `runs.csv` (see section 3), including which machine it ran on.
- `gpu_log.sh`: run on each PGX during the event; every 15 seconds append what's on the GPU to `data/runs/gpu.csv`, e.g. from `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader` plus `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader`. This works whatever Fuzzball's own monitoring offers. At bring-up, ask B whether Fuzzball reports the number of replicas directly; if it does, log that too.
- `charts.py ladder`: bar chart of total time for each step of the optimization ladder (baseline → batching → skip silence → keep the GPU fed → equal pieces → second PGX), all on the **same benchmark set** (step 14). Transcription and voice split shown separately.
- `charts.py timeline`: one row per machine, one bar per task, so idle gaps are visible (speed test 2: whole files vs equal pieces). Phase 2 (the language model) appears on the same timeline after phase 1.
- `charts.py replicas`: **model copies over time**: one line for the number of PGX running vLLM (from `gpu.csv`), one for requests per minute (from `requests.csv`), with the moments of scale-up and scale-down marked.
- `charts.py accuracy`: the three-column table (Vimeo / Whisper / Whisper + hint + voice split) for checks A and B.
- `charts.py all`: draws every chart in one command, so they can be redrawn in minutes on day 2.
- **Done when:** all charts draw from laptop runs (the replicas chart from a made-up `gpu.csv`). At the event, only the numbers change.

### Step 12 — Containers, the language-model service, two Fuzzfiles and the runners (B, 2–3 days, plus slack)

This is B's main job. It has the most unknowns, so B keeps spare time for surprises instead of taking on other steps.

**Our two containers**
- `Dockerfile.tools`: Python + `ffmpeg` + this repo (CPU steps and the web page).
- `Dockerfile.whisper`: start from NVIDIA's PyTorch container for Arm (NGC catalog), add `transformers`, `pyannote.audio` and this repo. Bake the Whisper and pyannote model files into the image or a volume (pyannote needs the Hugging Face token only to download).
- Build for both chip types: `docker buildx build --platform linux/arm64,linux/amd64 ...`. Test the arm64 build on an Apple Silicon Mac if anyone has one (CPU only there; the GPU part is tested at the event).

**The language-model service (catalog `vllm` entry; record exact commands in `deploy/catalog.md`)**

One command, built-in proxy included:
```
fuzzball workflow catalog start vLLM --values Model=hf://openai/gpt-oss-20b,MinReplicas=1,MaxReplicas=2,GpuMemoryUtilization=0.7,Volume=<our model volume>
```
(or the ready-made `vllm_gpt_oss_20b` entry with the same extra values). Find the proxy's address with `fuzzball workflow endpoints list`; A's `speakers.py` and C's `statements.py` take it as `--server`. The `Volume` value loads the model from storage we filled in advance instead of downloading it over venue Wi-Fi.

How its autoscaling behaves (entry defaults, read Nov 3):

| Setting | Value we use | What it does |
| --- | --- | --- |
| `MinReplicas` | 1 | Copies always running. 0 would shut it down when idle, and restarting means reloading the model (minutes) |
| `MaxReplicas` | 2 | One copy per PGX |
| Scale-up trigger (with the built-in proxy) | default: ≥30 requests in a minute, or any failed request | Adds a copy; at most once per `ScaleUpCooldown` (default 120 s) |
| Scale-down trigger | default: no request running for 5 minutes | Removes a copy, after `DrainPeriod` (default 300 s) so answers in progress finish |
| `GpuMemoryUtilization` | 0.7 | Share of GPU memory vLLM takes; lowered from 0.9 because this GPU shares memory with the rest of the system |
| Each copy's resources | 1 whole GPU (`GpusPerNode`, default) | Can't share a GPU with a Whisper task (see below) |

**Two phases, because a GPU can't be shared.** Each vLLM copy and each Whisper or pyannote task claims a whole GPU, and each PGX has one. So:
1. **Phase 1, `fuzzfile_audio.yaml`:** cut → task arrays of 16 transcribe and 16 voice-split tasks (`concurrency: 2`, one per PGX; `concurrency: 1` for one-PGX speed tests) → join. Both GPUs busy; no vLLM running.
2. **Start vLLM** (the catalog command above) once phase 1 finishes; wait until the proxy answers a test request.
3. **Phase 2, `fuzzfile_text.yaml`:** speakers (A's script) → statements → quote check (C's scripts) → web page, all CPU jobs that send requests to the proxy with `--parallel 32`. The request rate passes 30 a minute and the autoscaler adds the second copy on the other PGX.
4. **Stop vLLM** when phase 2 is done (or let it scale down to one copy after 5 idle minutes, for the chart, then stop it).

`deploy/run_event.sh --manifest <csv>` runs these four steps in order and waits between them, so nobody has to watch the clock. `--phase 1` or `--phase 2` runs one phase only. If the organizers confirm Fuzzball can share one GPU between jobs, phases 1 and 2 can overlap; don't plan on it.

Check every value name against the catalog files before the event (`applications/vllm/values.yaml` in `ctrliq/ciq-fuzzball-catalog`), and every Fuzzfile key against the [Fuzzball syntax reference](https://docs.ciq.com/fuzzball/4.1/appendices/workflow-syntax).

**Local runner**
- `run_local.sh --manifest <csv>`: phase 1 (`cut.py`, then `transcribe.py` and `voices.py` for every slice in a loop, then `join.py`), then phase 2 (`speakers.py`, `statements.py`, `quotecheck.py` against Ollama with `--parallel 2`).
- Save our images as files too (`docker save`), in case the venue network is slow.

**Sharing the knowledge (with A)**
- **Week −2:** one paired session, B and A, on the Fuzzfiles and `run_event.sh`: A writes or edits one part while B explains.
- **Week −1:** in the dress rehearsal, A types the commands and B watches.
- **Done when:** `run_local.sh` produces the ward page's data from the sample hearing inside the containers, on a laptop, and A has run it without B's help.

### Step 13 — Check the catalog images, pack backups (B, 2–3 hours)

- Do the catalog's default images have arm64 versions? (The vLLM entry uses both: the model server and its built-in proxy.)
  ```
  docker manifest inspect vllm/vllm-openai:v0.28.0 | grep arm64
  docker manifest inspect ghcr.io/berriai/litellm:v1.97.0 | grep arm64
  ```
  An arm64 version being listed doesn't prove it supports the GB10's GPU; that's confirmed only on the PGX.
- Pull and `docker save` backup vLLM images onto the USB drive, in order of preference:
  1. NVIDIA's own vLLM container from the NGC catalog (made for this hardware family; tends to trail the newest vLLM)
  2. A community GB10 build with every version pinned, e.g. `ghcr.io/timothystewart6/vllm-gb10:v0.20.1-gb10.0` (maintained by one person)
- Write the swap command into `deploy/catalog.md`: same catalog entry, plus `VllmImage=<backup image>`.
- **Done when:** results of both checks are written down and both backups are on the USB drive.

### Step 14 — Download the rest, build benchmark sets, pack the USB drive (everyone, one evening + 1 hour; A leads)

- The 41 Budget committee videos from 2025 and 2026 ([archive](https://vimeo.com/showcase/8925576)), at the smallest quality; run `ingest.py` and download captions for each, then delete the video to save space. Split the list across the team.
- If time allows, the rest of the 173-video Budget archive as audio and captions, for an overnight bonus run if the organizers allow it. A bigger batch also makes the language model's autoscaling visible.
- **Benchmark sets:** three small manifests, `bench_3h.csv`, `bench_6h.csv` and `bench_12h.csv`, each about that many hours of audio and **each including one long hearing** (so "whole files vs equal pieces" shows the idle tail). The speed tests in section 6 use one of these, chosen at bring-up from the measured speed, so each test run takes about 15 minutes.
- **USB checklist** (two copies, on two drives; B checks it off):
  - [ ] `data/audio/`, `data/captions/`, all manifests (full + 3 benchmark sets)
  - [ ] our two container images (`docker save` files), arm64 builds
  - [ ] backup vLLM images (step 13)
  - [ ] model files: Whisper `large-v3` and `large-v3-turbo`, pyannote, `gpt-oss-20b`
  - [ ] the repo (a `git bundle`), `ref/`, `labels/`
  - [ ] laptop-run outputs for the sample hearing (so the ward page can be demoed even if the PGX runs fail)
- **Done when:** the manifest has 41 rows with lengths and caption files, total audio hours are known, the benchmark sets exist, and both USB drives are packed. Update the design doc's workload table with the real total.

### Optional, if there's time before the event

- **Skip silence** (ladder step 2, A): run a small voice-activity model (for example Silero VAD) in `cut.py`, so pieces contain only speech. Log seconds removed.
- **Batch-size test on a laptop** (A): run `transcribe.py` with batch sizes 1, 4 and 8 to make sure the setting works; the real curve comes from the PGX.
- **Alder-turn cascade** (A; see the Claude Doc "Alder-Turn Cascade"): the large Whisper model only on alder turns, a cheap transcript for the rest. Only after the base pipeline runs end to end.

---

## 5. Playbook, part 1: roles and the weeks before the event

### 5.1 Roles

The team is 3–4 people. Every role has a named backup who knows how to run that role's scripts, so one person being stuck or sick doesn't stop the run.

| Role | Owns | Effort before the event | At the event | Backup |
| --- | --- | --- | --- | --- |
| **A — Audio + speakers** | Steps 1–5, 7; leads step 14 | ~5–6 days (the most, but well-defined and testable on laptops) | Speed tests and the transcription run; then the speaker-naming job in phase 2; spot-checks transcripts | B (and A is B's backup for Fuzzball) |
| **B — Cluster plumbing** (technical lead) | Steps 12–13; USB checklist | ~2.5–4 days, plus deliberate slack for surprises | Bring-up, vLLM service, Fuzzball, go/no-go calls at each gate. **Nothing else** | A |
| **C — Quotes + page + story** (presenter) | Steps 6, 8, 9; demo script | ~4–5 days | The statements and quote-check jobs in phase 2; fills the page; rehearsals; presents; owns the demo cut | D (or A) |
| **D — Measurement** (if there's a 4th person) | Steps 10, 11 (`bench.py`, `gpu_log.sh`, `evaluate.py`, all charts) | ~3 days | Chooses benchmark set and production size with B (6.3); all charts; accuracy checks A–D; hand-checks every demo quote against the video; backup video; timekeeping | C |

**With only 3 people:**
- step 11 (charts and logs) goes to **C**, since the charts feed the demo
- `evaluate.py` (step 10 scripts) goes to **B**, who has the most slack before the event
- the hand labels are split among everyone
- at the event: demo-quote checks go to **A** once phase 2 is done; the backup video and timekeeping go to **C**

**Decisions:** B calls technical go/no-go at each gate (section 6.4). C decides what goes in the demo. If they disagree, take the safer option.

### 5.2 Week by week (counted back from the event)

Assumes four weeks. With fewer, see "If time is short" below.

| Week | A — audio + speakers | B — cluster plumbing | C — quotes + page + story | D — measurement | Milestone at the end of the week |
| --- | --- | --- | --- | --- | --- |
| **−4** | Step 0; step 1 on the sample video; step 2 decision gate (runs the three versions) | Step 0; step 13 image checks; **send the organizer questions** (section 7); start `Dockerfile.whisper` | Step 0; step 6 roster + name hint | Step 0; **`bench.py` first**; step-2 hand transcript; first `evaluate.py compare` | **Decision gate result written down** (Whisper plan or fallback, section 8) |
| **−3** | Steps 3–4: cut and transcribe; **step 7 v1** (transcript only, Ollama) | Step 12: both containers; catalog command and `deploy/catalog.md` | Step 9: ward picker + page skeleton; **step 8** on step 7 v1 output | Check B labels (~100 turns), handed to C; `gpu_log.sh`; `charts.py` skeleton | **First milestone:** sample hearing end to end on one laptop, quotes on the page |
| **−2** | Step 5: voice split + join; **step 7 v2** (voice turns, `--parallel`); **paired session with B** | Step 12: both Fuzzfiles, `run_event.sh`, `run_local.sh`; **paired session with A** | Step 8 quote check; page on real output; draft `demo/script.md` | Check A labels (10 passages from the newest hearings); `evaluate.py` checks A–D; `charts.py all` | **`run_local.sh` runs inside the containers;** `charts.py all` draws every chart from laptop runs |
| **−1** | Step 14 downloads; benchmark sets; **runs the dress rehearsal's commands** | Backups packed (step 13); USB checklist; final Fuzzfile check against the syntax reference; watches A run the rehearsal | Demo dry run with laptop data | Scores laptop output with checks A–C; demo-quote checklist | **Dress rehearsal** (below) done; **USB drives packed**; code frozen (bug fixes only) |

**Dress rehearsal (week −1, ~3 hours, everyone):**
1. A runs `run_local.sh --manifest manifest/bench_3h.csv` (or a smaller sample if laptops are too slow) start to finish, from a clean folder, timing each step. B watches and only helps if A is stuck.
2. D runs `charts.py all` and the accuracy checks on its output.
3. C gives the 4-minute demo from `demo/script.md` to someone outside the team; D times it.
4. Walk through day 2 morning (section 6.2) once: with only 3 hours, every step must be practiced, not improvised.
5. Write down every problem; fix only what would break the event.

**If time is short (two weeks):** keep steps 0–9, 12–14 and checks A–C. Drop skip silence, the cascade, check D, address entry on the page and chart polish. Keep the decision gate: it decides which path you build. Keep the paired session: it's what protects the event if B gets stuck.

---

## 6. Playbook, part 2: the two event days

**Assumed schedule:** day 1, 9:00–18:00; day 2, 9:00 until the hackathon ends at **12:00**, with presentations before then. Ask the organizers when exactly presentations happen and how long each slot is; if presentations come *after* a 12:00 submission, the extra time is buffer, not new work.

**What the short day 2 means:**
- **Everything the demo depends on is computed on day 1.** Transcription finishes by 16:00 and the language-model phase by 18:00. Day 2 has no GPU work on the critical path.
- **Day 1 evening holds the first rehearsal and a first backup video,** made from whatever output exists by then.
- **Day 2 morning is only:** final checks, charts, freeze at 10:00, rehearsal, presentation.
- **Overnight runs, if allowed, are a bonus only:** extra hearings that join the page in the morning if they finished and pass a spot check. The demo never depends on them.

**Who does what:** each person runs and watches their own jobs, so B stays free to fix the cluster when it breaks.

### 6.1 Day 1

| Time | A — audio + speakers | B — cluster plumbing | C — quotes + page + story | D — measurement | Gate |
| --- | --- | --- | --- | --- | --- |
| 9:00–10:00 | Copies audio and models onto both PGX; helps B with bring-up | **Bring-up** (6.6): cables, hello world, 2-task array, one piece on one GPU | Page running on a laptop with laptop-run data | Starts `gpu_log.sh` on both PGX once they're up; starts the run notes (what was done, when, what broke) | **Gate 1 at 10:00** |
| 10:00–10:30 | Measures speed: one benchmark piece with large-v3, then turbo | vLLM test (15 min max, image swaps if needed), cold-start time noted, **then stops it**; optional GPU-sharing test | Prepares the page to load event output | Works out the benchmark set and production size from A's measured speed (6.3), with B | — |
| 10:30–12:00 | **Runs the speed tests on one benchmark set** (about 15 min each): baseline → batching → (skip silence, if built) → whole files vs equal pieces → second PGX | Watches Fuzzball during the tests; fixes cluster problems only | Practices phase 2 on benchmark output with Ollama on a laptop: statements + quote check | Ladder and timeline charts from `runs.csv` as tests finish; check D (large-v3 vs turbo) | **Gate 2 at 12:00** |
| 12:00 | **Starts the production run:** phase 1 on the chosen set, newest hearings first, best settings, both PGX (`run_event.sh --phase 1`) | Watches Fuzzball; lunch in shifts with A | Lunch | Lunch | — |
| 12:30–16:00 | Spot-checks transcripts of finished hearings; re-runs any failed slice; dry-runs `speakers.py` on finished hearings against Ollama on a laptop | Gets phase 2 ready: vLLM command, proxy address into `run_event.sh` | Fills the page as hearings finish; picks 3 example wards | Check A on the newest hearings as they finish; watches `gpu.csv` | **Gate 3 at 16:00** |
| 16:00–17:15 | Owns the **speaker-naming job** in phase 2: watches its output and `requests.csv` | **Starts vLLM**, watches it scale to the second PGX, stops it after phase 2 (or after it scales back down, for the chart) | Owns the **statements and quote-check jobs** in phase 2; page on phase-2 output; chooses demo wards | Check B on phase-2 output; begins hand-checking demo quotes | — |
| 17:15–18:00 | Starts the overnight bonus manifest, if allowed; projection: hours for the full Budget archive and all committees | Stands by; writes the day's cluster notes | **Rehearsal 1** with today's output | `charts.py all` on the day's logs; records **backup video v1**; lists fixes for tomorrow | **Gate 4 at 18:00** |

### 6.2 Day 2 (ends at 12:00)

| Time | A — audio + speakers | B — cluster plumbing | C — quotes + page + story | D — measurement | Gate |
| --- | --- | --- | --- | --- | --- |
| 9:00–9:15 | Overnight bonus: finished? Spot-checks 3 quotes; if anything looks wrong, it stays out | Saves overnight logs; stops `gpu_log.sh` | Team stand-up: the fix list from last night, in order | Timekeeper from here on | — |
| 9:15–10:00 | Helps D hand-check demo quotes against the video | Stands by for cluster questions; exports nothing new to the PGX | Fixes from rehearsal 1 only (no new features) | `charts.py all` final; charts exported as images; final numbers for checks A–C; **every demo quote hand-checked** | **Gate 5 at 10:00: hard freeze** |
| 10:00–10:45 | Rehearsal 2 (as audience) | Rehearsal 2 (presents the cluster part) | **Rehearsal 2**, timed | Times it; re-records the **backup video** from the frozen version | — |
| 10:45 to the presentation | Ready to answer questions on audio and speakers | Ready to answer questions on Fuzzball and scaling | Presents | Holds the backup video ready; answers questions on accuracy | — |

### 6.3 Choosing the benchmark set and the production size (at 10:30 on day 1; D with B)

Measured speed decides both. Let **R** = hours of audio one PGX transcribes per hour, from A's 10:00 test (e.g. R = 20 means 20× real time).

- **Benchmark set for the speed tests:** pick the one where a one-PGX baseline run takes about 15 minutes, so 4–5 runs fit in the 90-minute slot: R below ~15 → `bench_3h`; R about 15–35 → `bench_6h`; R above ~35 → `bench_12h`.
- **Production run:** phase 1 has about **4 hours** (12:00–16:00) on two PGX, and voice split and joining add roughly a third. So it fits about **6 × R hours of audio** (2 PGX × 4 hours × R, minus the third):
  - **6 × R is more than the 41 hearings' total:** run all 41.
  - **Less:** run **newest first**: the 12 hearings from 2026, then 2025 hearings in date order up to 6 × R hours. Put the rest in the overnight bonus manifest, if overnight runs are allowed.
  - **Much more (phase 1 would end before ~14:00):** add older Budget hearings to the production set up to 6 × R hours. The bigger batch also makes the language model's scale-up visible.
- Write the decision and the numbers in the run notes; they go on a demo slide.

### 6.4 Gates and fallbacks

B makes the call at each gate.

| Gate | When | Pass means | If missed |
| --- | --- | --- | --- |
| **1. Bring-up** | Day 1, 10:00 | A task array lands one task on each PGX; Whisper uses the GPU in our container | Keep trying until 10:45, then A runs the scripts **by hand** on each PGX (no Fuzzball) while B keeps working on Fuzzball for the one-vs-two PGX test only. If Whisper won't use the GPU in our container, try the NVIDIA base image by hand. If the PGX won't run at all, demo on laptop output |
| **vLLM works** (part of bring-up) | Day 1, 10:30 | The proxy answers a test request | Swap images (step 13); then **Ollama on a PGX** for phase 2. We lose the autoscaling chart, not the project |
| **2. Settings frozen** | Day 1, 12:00 | Speed tests done on the benchmark set; production settings and size chosen (6.3) | Stop testing at 12:00 anyway; use batching + equal pieces + both PGX. Missing ladder bars stay missing: no GPU time for them later |
| **3. Phase 1 done** | Day 1, 16:00 | The chosen hearings transcribed, voice-split and joined | At 16:00, stop phase 1 and run phase 2 on the hearings that finished (newest first means the most relevant ones are done). Unfinished hearings go to the overnight bonus, if allowed |
| **4. Phase 2 done** | Day 1, 18:00 | Quotes on the page; charts drawn; rehearsal 1 and backup video v1 done | Phase 2 on fewer hearings, or Ollama on one PGX; if still nothing, show transcripts with search and "Watch" links **without speaker names** |
| **5. Hard freeze** | Day 2, 10:00 | Every demo quote hand-checked; final charts exported; nothing changes after this | Drop any quote not yet checked; never show an unchecked quote |
| **Live demo fails** | Presentation | — | Play the backup video; charts are also saved as images |

**Never fake load or numbers.** If the second vLLM copy never starts because phase 2 is too short, say so on the slide and show the projection for a bigger archive instead.

### 6.5 Demo script (about 4 minutes; full text in `demo/script.md`, owned by C)

| Time | What | Who | On screen |
| --- | --- | --- | --- |
| 0:00–0:30 | The problem: election Feb 23, 2027; votes are public, hearing words aren't; an 11-hour hearing; the automatic captions with no speakers and "Chair Irvin" | C | One slide: caption screenshot |
| 0:30–1:30 | A resident's view, live: pick a ward → alder → a statement → "Watch" opens the official video at that moment | C | The ward page |
| 1:30–2:30 | How it ran: both PGX busy in phase 1 (timeline), what each optimization bought (ladder), the language model scaling from one PGX to two (replicas chart) | B | Three charts (drawn by D) |
| 2:30–3:15 | Can you trust it: accuracy against hand transcripts with the captions as baseline; every quote checked word for word; unsure speakers left out | D (or C) | Accuracy table |
| 3:15–4:00 | Limits and next: incumbents only; nothing published without the City Clerk's OK; projection for the full archive | C | One slide |

Trim to the organizers' slot length once known; cut from the last row first.

### 6.6 Bring-up checklist (day 1, 9:00–10:30; B leads, A assists)

1. Cable the two PGX computers; copy audio, captions, manifests, models and container images onto both (A).
2. Fuzzball "hello world", then a 2-task array; check one task lands on each machine (B).
3. `transcribe.py` and `voices.py` on one piece on one PGX; confirm both use the GPU (`nvidia-smi`) (A). D starts `gpu_log.sh` on both PGX.
4. Catalog vLLM with the default image: **15 minutes at most**. If it fails, restart the same entry with `VllmImage=<NVIDIA image>`, then the community image. Send one test request through its built-in proxy. Note how long the model takes to load (the cold start) (B).
5. **Optional, 10 minutes:** with vLLM still running on one PGX, start one Whisper task and see whether Fuzzball places it on the *same* GPU or waits. That answers whether a GPU can be shared (B).
6. **Stop vLLM** before any transcription run. Left running, it holds one GPU and halves transcription speed (B).
7. Fill in both Fuzzfiles' storage details and the proxy address handling in `run_event.sh` (B).

---

## 7. Things to settle early

- **Ask the organizers now (B):** the hackathon date and **exact hours of both days** (day 2 ends at 12:00: when are presentations, and how long is each slot?); **whether jobs can run overnight**; which catalog entries have been tested on the PGX (does vLLM run as-is?); is the catalog installed on the event's Fuzzball; which Fuzzball version; **can two jobs share one GPU**; GPU driver / CUDA version; venue internet (for model downloads).
- **Decision gate (step 2):** run it in week −4; everything A builds after it depends on the result.
- **Hearing length:** step 1 on the sample video gives the first real number.
- **Laptop models:** at least one team laptop should have 16 GB of memory for an 8B Ollama model; otherwise use a 3B model for practice.
- **Roster accuracy:** a second person checks `ref/alders.csv`, including the Budget chair's spelling.
- **pyannote on arm64:** it's plain PyTorch, so it should run in NVIDIA's container, but confirm the arm64 image builds and starts on an Apple Silicon Mac.
- **City Clerk:** ask what's needed before publishing transcripts after the event, and whether the office has human-made captions for any meetings (these would be a free answer key). Phone: 312-742-5375.
- **Publishing:** nothing goes public before human review and the Clerk's OK. The hackathon demo runs locally.

---

## 8. Fallback if step 2 shows Vimeo's text is as good as Whisper

- **Input:** Vimeo's automatic captions for every video, converted by `captions.py`. Whisper is dropped (or kept only for videos with no captions).
- **Still needed:** the voice split (step 5), because Vimeo's text has no speaker changes; and word timing, by matching each quote to Vimeo's ~10–15 second caption blocks (less precise "Watch" links; say so on the page).
- **Heavy work moves to the language model, at archive scale:** speakers + statements for the **whole archive** (full council 2011–2026 plus 23 committees), not just 41 Budget hearings, using a larger model through the catalog vLLM autoscaled across both PGX. Rough guess, not yet measured: a few thousand hours of meetings is tens of millions of words to read, which could plausibly fill most of a day on one PGX. Measure tokens per second on the PGX before promising a size.
- **Same two-phase order:** the voice split runs first as a task array on both GPUs; then vLLM starts and scales to both PGX. Here phase 2 is the long one, so the scale-up from one copy to two is clearly visible on the replicas chart.
- **Roles:** A's Whisper work (step 4) shrinks, so A puts that time into step 7 at archive scale and into downloading the other committees' captions and audio. B, C and D are unchanged.
- **Playbook changes:** the day-1 speed tests measure the voice split and the language model's throughput (tokens per second, one copy vs two) instead of Whisper's; the production size in 6.3 is chosen so the voice split ends by about 14:00 and the language model by 18:00 on day 1; downloads in step 14 cover the other committees' captions and audio.
- **What we show the judges:** "inference at scale": autoscaled model copies on both machines working through the archive, the voice split as a batch task array before it, and the same accuracy checks with Vimeo as the baseline.
