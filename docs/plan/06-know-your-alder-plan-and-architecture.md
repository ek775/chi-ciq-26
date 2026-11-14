> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Know Your Alder - Plan & Architecture" (created 2026-10-27;
> snapshot of a Claude doc as of Oct 26, 2026).
> NOTE: predates the mid-November pivot (docs 01-04) AND is flagged out of date on
> two points by the Application Drafting Handoff: it says the Clerk's videos have
> no captions (they DO have Vimeo auto-captions), and it treats diarization as a
> stretch (it is now core). Read as background on the original product design.

# Know Your Alder — Plan & Architecture

Snapshot of the Claude doc "Know Your Alder — Plan & Architecture" (https://claude.ai/artifact/GdTSZAbatwM8FWAQUid3wB), as of Oct 26, 2026. The doc is the live version; this copy does not update when the doc changes. Diagrams are described in text here. This plan replaces the earlier "Bill Fact Sheets", "Copycat Bill Finder" and "Ask All 56 Legislatures" plans.

## Summary

We build **Know Your Alder**: Chicago residents enter their address and see what their alderperson actually said in City Council budget hearings, in their own words, with a link to the exact moment in the official video. Two ThinkStation PGX computers turn hours of hearing video into searchable text using speech-recognition AI, then a language model pulls out each alder's statements. CIQ Fuzzball runs the workflow. This fits the hackathon's **Inference at scale** track.

In the two days we process the **2025 and 2026 Budget committee hearings: 41 videos, roughly 80–160 hours of audio**. The full archive and weekly updates are future work (see the last section).

**What the judges see:**

1. A resident's view: type an address, see the ward and alder, then that alder's questions and statements from the budget hearings, each one a quote with a link to the moment in the video.
2. Accuracy checks: transcription errors measured on hand-checked passages, speaker names checked by hand, and every quote automatically confirmed to appear word for word in the transcript.
3. Speed charts: hours of audio processed per hour on one PGX vs two, and why cutting long videos into equal pieces matters.
4. A projection: how long the full 173-video Budget archive, and all committees, would take on this hardware.

**Honest claim:** two computers can at best double the speed, and we show near-2×. The real story is a heavy AI job that nobody can do on demand, run on a small local cluster, producing something residents can act on before they vote.

The team is new to AI and cluster computing, and we can't use the PGX or Fuzzball before the event. So all code is practiced on laptops, the videos are downloaded beforehand, and day 1 starts with a one-hour bring-up of the real setup.

## Why it matters

**Local decisions shape daily life most.** Chicago's City Council decides the city budget, zoning, policing, streets and services. Most of the debate happens in committee hearings, and the City Clerk posts them as official video on Vimeo: full council meetings from 2011 and archives for 23 committees ([City Clerk](https://chicityclerk.com/about-city-government-chicago-city-council/committee-meeting-videos)).

**The gap: nobody can search what was said.**

| Who | What they cover | What's missing |
| --- | --- | --- |
| [Chicago Councilmatic](https://chicago.councilmatic.org/about/) (DataMade) | Legislation, actions and votes since 2011, free, updated nightly | No transcripts; video embedded but not searchable |
| [City Bureau's Documenters](https://www.citybureau.org/notebook/chicago-documenters-100-assignments) | Trained, paid residents take notes at meetings | Done by hand; only the meetings they can staff |
| City Clerk | Official video, with downloads enabled | No transcripts or captions mentioned |

**Who acts, and when.** The output is built around actions people can take:

- **Voters**, before Chicago's 2027 municipal elections. govbot already tracks every alderperson race and has the ward maps.
- **Residents**, contacting their alder before a vote, such as the annual budget vote.
- **Reporters and Documenters**, using transcripts as a searchable first draft to check against the video.

The window to act on a single upcoming item is short in Chicago: items need only be posted 48 hours ahead, and speaking remotely requires registering by the morning of the day before ([BGA, Civic Federation and League of Women Voters letter](https://civicfed.org/sites/default/files/2024-02/Transparency_Letter_BGA_CF_LWV.pdf)). That's why the hackathon focuses on the record ahead of elections, and leaves alerts on upcoming items to future work.

**How we stay neutral:**

- The same format for all 50 alders: their own words, with timestamps and video links.
- No scores, ratings or labels, and no summaries that could change meaning. Every quote is checked automatically against the transcript.
- When the speaker isn't certain, the quote is marked "speaker unconfirmed" and not attributed to anyone.

**Why local hardware fits.** Transcribing years of meetings is a big, repeatable job: a full backfill first, then new meetings every week, then reruns as speech models improve. A small owned cluster with no per-hour bill suits a volunteer civic project. This matches how the sponsors describe their products: Lenovo pitches the PGX for "local prototyping, fine-tuning, and inferencing" ([Lenovo Press](https://lenovopress.lenovo.com/lp2321-thinkstation-pgx)), and CIQ says "one workflow definition runs across every cloud and on-premises environment" ([Fuzzball](https://ciq.com/products/fuzzball)).

## How it works

1. **Collect** (before the event, by hand): download the 41 Budget committee videos from the City Clerk's Vimeo archive at the smallest quality, convert each to compressed audio with `ffmpeg`, and keep a list: title, date, committee, Vimeo link.
2. **Cut into equal pieces** (one computer, CPU): split each recording into about 30-minute pieces, with a few seconds of overlap. Videos run from 1 to 11 hours; without this, one long video would keep one computer busy while the other sits idle.
3. **Transcribe** (both computers, GPU): a speech-recognition model (Whisper) turns each piece into text with timestamps. This is the heavy step.
4. **Join and name speakers** (both computers, GPU): stitch pieces back into one transcript per meeting. Then a language model works out who is speaking from what's said aloud: chairs call on members by name ("Alderperson X, you're recognized"), and the 50 alders are a known list. Uncertain turns stay "speaker unconfirmed".
5. **Pull out each alder's statements** (both computers, GPU): the language model lists each alder's questions and statements per hearing, with the topic and the department being questioned, quoting the exact words and the time in the video. A simple check then confirms every quote appears word for word in the transcript; any that don't are dropped.
6. **Show** (web page): enter an address, find the ward using govbot's Chicago ward map, then see that alder's statements, each linked to the official video at the right moment (Vimeo links can start at a time, e.g. `#t=1h02m10s`). Add how to contact the alder, and their 2027 race from govbot's elections data.

**Two kinds of AI, both on the GPUs:**

- **Speech recognition** (Whisper) in step 3. It needs no internet and runs the same on a laptop and on the PGX, just slower on a laptop.
- **A language model** in steps 4–5, run through the standard "OpenAI-compatible" interface: Ollama on a laptop, a model server on the PGX. Only the server address changes.

## Architecture

*Diagram (described): "Both PGX transcribe first, then name speakers and pull out quotes."*

- **41 Budget hearings, as audio** (downloaded from the City Clerk beforehand) → **1 Cut into 30-minute pieces (CPU)**, 16 slices so both machines stay busy.
- **Phase 1 · transcription (heavy):** **2 Whisper on PGX A (GPU)** takes even slices; **Whisper on PGX B (GPU)** takes odd slices; audio to timed text.
- → **3 Join pieces (CPU)**: one timed transcript per hearing.
- **Phase 2 · language model (light):** **4 Name speakers, pull quotes**, with a model server on PGX A and one on PGX B.
- → **5 Quote check (CPU)**: keep only quotes found word for word → **6 Ward page (Streamlit)**: address, ward, alder, quotes, video links.

Each GPU does one big job at a time, so the work runs in two phases: first both PGX transcribe, then both run a language model. Almost all the time goes into phase 1; phase 2 takes minutes.

## The workload

Transcription is the heavy part; the language-model steps are light. Counts are from the team's look at the Budget committee archive (173 videos; 12 in 2026 so far, 29 in 2025; each at least 1 hour, some up to 11). Hours assume an average of 2–4 hours per video and are estimates until we measure.

| Set | Videos | Hours of audio |
| --- | --- | --- |
| 2026 so far | 12 | ~25–50 |
| 2025 | 29 | ~60–120 |
| **Hackathon set: 2025 + 2026** | **41** | **~80–160** |
| Whole Budget archive (stretch) | 173 | ~350–700 |
| All 23 committees plus full council | not counted | probably thousands |

**Expected run times.** Whisper-style speech recognition runs at about 15–25× real time per stream on common GPUs ([RunPod](https://www.runpod.io/articles/guides/best-gpu-for-whisper)): 15–25 hours of audio per hour of computing. There are no published numbers for the PGX's chip, so we assume 10–25× per PGX and measure it on day 1.

| Set | One PGX | Two PGX |
| --- | --- | --- |
| **Hackathon set (41 videos)** | ~3–16 hours | **~1.6–8 hours** |
| Whole Budget archive (173) | ~14–70 hours | ~7–35 hours |

Add about a third for joining pieces, naming speakers and pulling out statements. Those steps read roughly 12,000 tokens of transcript per hour of speech. At the published reading speed of a mid-size model on this chip (about 8,000 tokens per second, [LMSYS](https://www.lmsys.org/blog/2025-10-13-nvidia-dgx-spark)), the whole hackathon set takes minutes, not hours.

**Why two machines matter here:** even at the slow end, the hackathon set fits within day 1 on two PGX, but could take up to two working days on one. The whole Budget archive only fits overnight with both.

## Hardware and tools

Each PGX is a small but strong AI computer, and the two are linked by a fast direct cable ([Lenovo spec sheet](https://psref.lenovo.com/syspool/Sys/PDF/ThinkStation/ThinkStation_PGX/ThinkStation_PGX_Spec.pdf)).

| Per computer | Spec | What it means for us |
| --- | --- | --- |
| Processor | NVIDIA GB10 chip: 20 Arm CPU cores and one Blackwell GPU | One GPU per computer runs transcription, then the language model. **Software must be the Arm (arm64) version** |
| Memory | 128 GB, shared by CPU and GPU | Plenty for a speech model and a mid-size language model |
| Storage | Up to 4 TB NVMe | The hackathon audio is only a few GB |
| Link between the two | ConnectX-7, 200 Gb/s | Plug the two together directly; little data crosses it |
| Operating system | NVIDIA DGX OS (Linux) | Use NVIDIA's ready-made Arm containers where possible |

**Software** (all free; check each runs on arm64):

| Job | Tool | Why this one |
| --- | --- | --- |
| Convert and cut audio | `ffmpeg` | One command per file |
| Speech recognition | Whisper (`large-v3` or the faster `large-v3-turbo`) through Hugging Face `transformers`, inside NVIDIA's PyTorch container | Well documented; runs on laptops and the PGX. Popular faster versions (`faster-whisper`) may lack Arm GPU builds, so test before relying on them |
| Language model on a laptop | Ollama with a small model | Practice only; same interface as the real server |
| Language model on the PGX | vLLM or SGLang from NVIDIA's containers, or Fuzzball's model serving if offered | Handles many requests at once |
| Ward lookup | govbot's Chicago ward map | Already built for govbot's elections page |
| Alder list, races | govbot's Chicago elections data; City Council membership | Names for speaker matching and the 2027 races |
| Web page | Streamlit | A web page written in plain Python |
| Workflow on both computers | CIQ Fuzzball | Next section |

## Running it with Fuzzball

Fuzzball runs a list of steps written in one YAML file, a Fuzzfile ([syntax reference](https://docs.ciq.com/fuzzball/4.1/appendices/workflow-syntax)). The work runs in **two phases on the GPUs**, because each GPU does one big job at a time:

1. **Transcription phase:** the audio pieces are split into 16 slices. A task array runs one transcription task per slice, and each task claims a whole GPU, so with `concurrency: 2` exactly one task runs on each PGX. When a task finishes, the next slice starts on that machine, which keeps both busy until the end.
2. **Language-model phase:** once transcription is done, a model server starts on each PGX, and light CPU workers send transcripts to them for speaker naming and statement extraction.

**The one switch for the speed test:** `concurrency: 1` uses one PGX, `concurrency: 2` uses both. Nothing else changes.

```yaml
version: v4
volumes:
  data: { use: <provisioner>, name: alder-data }        # audio + list, copied in at bring-up
jobs:
  cut:                           # step 2: cut audio into ~30-minute pieces, make 16 slices
    image: { uri: docker://<registry>/alder-tools:arm64 }
    command: [python, cut.py, --minutes, "30", --slices, "16"]
    mounts: { /data: data }
    resource: { cpu: { cores: 8 }, memory: { size: 16GB } }
  transcribe:                    # step 3: one GPU task per slice
    image: { uri: docker://<registry>/alder-whisper:arm64 }
    script: |
      python transcribe.py --slice ${FB_TASK_ID}
    mounts: { /data: data }
    depends-on: [{ name: cut, status: FINISHED }]
    task-array: { start: 0, end: 15, concurrency: 2 }   # 1 = one-PGX test
    resource: { cpu: { cores: 8 }, memory: { size: 64GB }, devices: { nvidia.com/gpu: 1 } }
  # join (step 4a): one CPU job after transcribe
  # speakers + statements (steps 4b-5): CPU workers sending requests to model-a / model-b
services:
  # model-a, model-b: one language-model server per PGX, each claiming the GPU,
  #   started after transcription finishes
  # webpage: Streamlit on port 8501, with network.endpoints scope: group
```

This is a sketch written from the docs, not tested. At bring-up, check how jobs find a service's address and how storage is shared, then fix key names against the reference. Every script also runs on its own (`python transcribe.py --slice 3`), so running by hand is always a fallback.

(Note: later work found task arrays must start at **1**, not 0 — see doc 10 "Findings that change the plan".)

## What we measure

No numbers exist yet: everything is measured at the event. The "expected" column is what we check against.

**Accuracy (the AI result)**

| Check | How | Why it matters |
| --- | --- | --- |
| **A. Transcription errors** | The team transcribes 10 five-minute passages by hand before the event (varied speakers, noisy moments). Compare with Whisper's text and report the share of words wrong | Shows how far to trust a quote |
| **B. Speaker names** | The team labels about 100 speaking turns by hand. Compare with the AI's names, and count how often it says "unconfirmed" | A quote pinned on the wrong alder is the worst possible error |
| **C. Quotes are real** | Automatic: every extracted quote must appear word for word in the transcript; report how many the AI proposed and how many were dropped | Stops the language model from inventing quotes |
| **D. Model size** | Repeat check A with a smaller, faster Whisper model | Whether speed is worth the extra errors |

If the City Clerk already transcribes public comment, as the 2024 BGA/Civic Federation/LWV letter suggests, those transcripts make a free extra answer key for check A.

**Speed (the cluster result)**

| Test | What we change | What we measure | Expected | Chart for the demo |
| --- | --- | --- | --- | --- |
| **1. One PGX vs two** | `concurrency` 1 → 2 | Hours of audio per hour | Close to 2× | Two bars, speedup printed |
| **2. Equal pieces vs whole files** | Transcribe whole videos vs 30-minute pieces, on two PGX | Total time, and idle time on each machine | Whole files: one 11-hour video leaves a machine working alone at the end | A timeline of each machine's work, showing the idle gap |
| **3. Model size** | `large-v3` vs `large-v3-turbo` | Hours of audio per hour, and check A's errors | Turbo several times faster, slightly less accurate | Speed vs accuracy, one point per model |
| **4. Projection** | Nothing; uses tests 1–3 | Hours for the whole Budget archive and all committees | See "The workload" | Estimate vs measured |

**The optimization ladder (the main speed chart).** Start from the naive version and add one technique at a time, measuring the time to transcribe the 41 hearings at each step. Each bar is one change and one number, so the chart explains itself. It also answers "isn't this just 2×?": most of the speedup comes from using each GPU well, and the second machine multiplies that.

| Step | Technique | What it fixes | Effect (to measure) |
| --- | --- | --- | --- |
| 0 | **Baseline:** one PGX, whole files, one piece at a time | — | The starting time |
| 1 | **Batching:** send 8–16 audio pieces to the GPU at once | A GPU working on one piece at a time is mostly idle | Often the biggest single gain |
| 2 | **Skip silence:** a small voice-activity model drops recesses, breaks and dead air before transcribing | Hearings have long pauses that still cost GPU time | Less audio to process; depends on how much silence there is |
| 3 | **Keep the GPU fed:** prepare the next pieces on the CPU while the GPU works | The GPU waits while audio is loaded and converted | Higher GPU use |
| 4 | **Equal pieces:** 30-minute pieces instead of whole files | One 11-hour video leaves the other machine idle at the end | Removes the long tail |
| 5 | **Second PGX** | — | Close to 2× |

**Priorities for a beginner team:**

1. **Must have:** batching and the second PGX (easy, big effects); equal pieces (easy, the clearest cluster lesson); model size (easy, speed test 3).
2. **Nice to have:** skip silence (moderate effort; real savings on meeting audio).
3. **Stretch:** keeping the GPU fed; lower-precision math (FP16 vs FP8, which this chip supports, measured with check A); and unplugging one PGX mid-run to show that Fuzzball reruns only the lost pieces and the job still completes.

**Don't add techniques that change nothing measurable here.** For example, splitting Whisper across both machines would be a stunt: it fits easily on one GPU.

**Decide the data size after the day-1 speed measurement.** If transcription is slow, stick with the 41 hearings. If it's much faster than assumed (batched speech models can run far above single-stream speeds), move to the whole Budget archive (173 videos) so both machines stay meaningfully busy. Download the extra audio beforehand just in case.

**Write down for every run:** time, model, which videos, total audio hours, one or two PGX, piece size, total time, errors. Keep it all in one spreadsheet, and draw every chart from it.

**Stretch, only if all gates passed:** a separate speaker-separation model ("diarization") to compare against naming speakers from the transcript alone, and the rest of the Budget archive overnight.

## 2-day build plan

*Diagram (described): "All 41 hearings transcribed by end of day 1; freeze at midday on day 2." Three workstreams for a beginner team; each gate must pass before moving on.*

| Workstream | Day 1 · morning | Day 1 · afternoon | Day 2 · morning | Day 2 · afternoon |
| --- | --- | --- | --- | --- |
| **Audio + transcripts** (1–2 people) | Transcribe one hearing on one PGX; check the text | All 41 hearings across both PGX; join transcripts | Accuracy checks A and D against hand transcripts | Check demo quotes against the video; note errors openly |
| **Fuzzball + models** (1 person) | Bring-up: cable, containers, Whisper on one PGX | Speed tests 1 and 2 (one vs two PGX; pieces vs files) | Model servers; speakers + quotes; checks B and C | Two rehearsals; backup video; stretch only if OK |
| **Ward page + story** (1 person) | Address to ward to alder, using laptop practice output | Statements list, video links at the right moment | Speed charts page; pick 3 example wards | Demo script: problem, one ward, accuracy, limits |

**Gates:**

- **Gate 1** (end of day 1 morning): one hearing transcribed on PGX.
- **Gate 2** (end of day 1): all 41 hearings transcribed.
- **Gate 3** (midday day 2): checked quotes on the page; feature freeze.

Gate 2 matters most: once all 41 hearings are transcribed, the rest is light work on top. Each missed gate has a fallback:

- **Gate 1 missed:** if Whisper won't use the GPU in the container, try another build (for example `whisper.cpp`) or run it by hand outside Fuzzball. If the PGX won't run at all, transcribe a few hearings on laptops and keep going.
- **Gate 2 missed:** demo on the hearings that finished; speed tests 1 and 2 still work on fewer hours.
- **Gate 3 missed:** show transcripts with search and video links, without speaker names; quotes are only shown once names are checked.
- **Live demo fails on stage:** play the backup video.

## Learn before the event

We can't use the PGX or Fuzzball before the event, so practice happens on laptops, set up so the same code moves to the PGX unchanged:

- **Same scripts everywhere.** Each step is a Python script that takes `--slice N`. On a laptop, a simple loop runs the slices one by one; at the event, Fuzzball's task array runs the same command.
- **Same container everywhere.** Build one Docker container for both chip types (`docker buildx build --platform linux/arm64,linux/amd64`). An Apple Silicon Mac is also arm64, so it's the closest stand-in for the PGX.
- **Same interface for the language model.** Ollama on a laptop, a model server on the PGX; only the address changes.
- **Small samples.** Whisper is slow on a laptop, so practice on 10–30 minutes of audio, not whole hearings.

**Everyone: get the videos (one evening, split across the team)**

- [ ] Download the 41 Budget committee videos from 2025 and 2026 ([archive](https://vimeo.com/showcase/8925576)), choosing the smallest quality
- [ ] Convert each to compressed audio with `ffmpeg`, then delete the video file
- [ ] Add each to the shared list: title, date, committee, Vimeo link, length
- [ ] Copy all audio plus the list onto a USB drive and a second laptop

**Audio and transcripts person:**

- [ ] Write `cut.py` (cut audio into pieces) and `transcribe.py` (Whisper on one slice, with timestamps); test on a 20-minute sample
- [ ] Hand-transcribe the 10 five-minute passages for accuracy check A
- [ ] Write the step that joins pieces back into one transcript per meeting

**Fuzzball and models person:**

- [ ] Write the language-model steps: name speakers, pull out statements as JSON with exact quotes and times, and the quote check; test with Ollama
- [ ] Label about 100 speaking turns by hand for accuracy check B
- [ ] Write the full Fuzzfile in advance from the syntax reference, plus `run_local.sh` that runs every slice on a laptop
- [ ] Build the containers for arm64; find NVIDIA's arm64 PyTorch and vLLM/SGLang containers; ask the organizers whether their Fuzzball serves models

**Ward page and story person:**

- [ ] Build a Streamlit page: address → ward (govbot's map) → alder → statements with video links at the right time
- [ ] Collect the 50 alders' names and wards, and their 2027 races from govbot's elections data
- [ ] Draft the 3-minute demo story: problem, one resident's view, accuracy, speed, limits

## Risks

The two most serious risks are a quote pinned on the wrong person and software that won't run on new hardware.

| Risk | What happens | What we do |
| --- | --- | --- |
| A quote is attributed to the wrong alder | Unfair to a real person; destroys trust | Attribute only when the speaker is clearly named in the transcript; otherwise "speaker unconfirmed". Hand-check every demo example. Nothing goes public without human review |
| A misheard word changes a quote's meaning | Misleading quote | Every quote links to the video moment; show accuracy check A on the page; flag low-confidence passages |
| The language model invents or rewrites a quote | False statements | Automatic check C drops any quote not found word for word in the transcript |
| Speech software lacks Arm GPU support | No transcripts on day 1 | Default to Whisper through `transformers` in NVIDIA's PyTorch container; test arm64 builds on a Mac beforehand |
| No chance to try Fuzzball or the PGX before the event | Slow start | Laptop practice with identical scripts; first hour of day 1 is bring-up; by lunch, run by hand if needed |
| Downloads take longer than expected | Missing videos | Smallest quality, split across the team, start a week early |
| Publishing transcripts may need the City Clerk's OK | Can't go public after the event | The demo credits the Office of the City Clerk; before any public release, confirm with the Clerk's office (312-742-5375) |
| Seen as taking sides before an election | Loses credibility | The same format for all 50 alders, their own words only, no scores, ratings or labels |
| Poor audio (crosstalk, off-mic speakers) | Errors | Mark those passages; don't extract quotes from them |

**Open questions (now answered):**

- Is Fuzzball already installed on the PGX pair, and which storage provisioner does it offer for persistent volumes? Local disk on each node, or a shared NFS mount? Answer: neither is available before the event, so we check storage during day-1 bring-up.
- Can a Fuzzball service be reached from a laptop at the venue, i.e. do endpoints work on the venue network? Answer: probably yes. Fallback: copy the results file to a laptop and run the web page there.
- How many people are on the team? The build plan assumes 3–4 people, all new to AI and cluster computing. Answer: confirmed.

**Before the event:**

- [ ] All 41 videos downloaded, converted to audio, listed, and copied to a USB drive; if time allows, the rest of the Budget archive as audio too
- [ ] Hand labels done: 10 transcribed passages (check A) and about 100 speaking turns (check B)
- [ ] Containers built for arm64 and tested on a laptop; pushed to a registry and also saved as files
- [ ] Whisper and language-model files downloaded ahead of time, in case the venue network is slow
- [ ] Fuzzfile written and checked against the syntax reference
- [ ] Ask the City Clerk's office whether it already transcribes public comment (a free answer key)
- [ ] Credit on the web page: "Video: Office of the City Clerk, City of Chicago"

**First hour at the event (bring-up):**

- [ ] Connect the two PGX computers with the ConnectX-7 cable and check they can reach each other
- [ ] Copy audio, list, containers and model files onto both PGX computers
- [ ] Run a "hello world" Fuzzfile, then a 2-task array, and check that one task lands on each computer
- [ ] Transcribe one 30-minute piece on one PGX and check the GPU is used
- [ ] Check what storage Fuzzball offers and how jobs find a service's address; adjust the Fuzzfile

If bring-up isn't done by lunch, run the scripts by hand on the PGX, and keep Fuzzball for speed test 1 only.

## After the hackathon

Not part of the hackathon. This is where the work goes next, and the hackathon's measurements will confirm or correct the estimates.

**Stage 1: the full archive, then weekly updates.**

- **Backfill:** all 23 committee archives plus full council meetings since 2011. Count the videos first; it's likely thousands of hours, which at our assumed speeds means days of nonstop work on two PGX. For bulk downloads, use Vimeo's official developer API (check its terms) rather than scraping the website, and go slowly.
- **Weekly updates:** new meetings each week are a light load, likely well within one PGX. Two machines matter for the backfill and for reruns when better speech models appear.
- **Get the City Clerk's OK** before publishing transcripts.

**Stage 2: "Before the vote" alerts.**

- For each upcoming committee item in the City Clerk's legislation system: what it does in plain words, where it is on the ward map, what was already said about it in earlier hearings, and exactly how and by when to register to speak or submit testimony.
- **Limit:** items need only be posted 48 hours ahead, and some arrive later, so alerts can't always come in time ([BGA, Civic Federation and League of Women Voters letter](https://civicfed.org/sites/default/files/2024-02/Transparency_Letter_BGA_CF_LWV.pdf)).

**Stage 3: partners and reach.**

- **City Bureau's Documenters:** transcripts as a first draft their note-takers check, so they can cover more meetings.
- **Chicago Councilmatic (DataMade):** link transcripts and video moments to legislation pages.
- **Other cities:** the open-source Council Data Project already transcribes meetings for some cities; compare approaches and share tools ([University of Washington](https://ischool.uw.edu/news/2023/01/researchers-shed-light-city-governments-data)).
- **Connect levels:** show Illinois bills from govbot next to city decisions on the same issue, so residents see everything decided about, say, housing in one place.

**Suggested order:** (1) the full Budget archive and weekly updates; (2) the City Clerk's OK and a public ward page before the 2027 elections; (3) other committees; (4) Before the vote alerts; (5) partners and other cities.

## Word list

| Term | Plain meaning |
| --- | --- |
| Alder / alderperson | One of Chicago's 50 City Council members, each elected by a ward |
| Ward | One of Chicago's 50 voting districts |
| Committee hearing | A council committee meeting where items are debated before the full council votes |
| Speech recognition / Whisper | AI that turns spoken audio into written text; Whisper is a free model for this |
| Language model (LLM) | AI that reads and writes text; here it names speakers and pulls out statements |
| Real-time factor | Hours of audio processed per hour of computing; 20× means 20 hours of audio per hour |
| Timestamp | The point in a recording where something was said |
| Speaker naming / diarization | Working out who is speaking when; diarization does it from the voices themselves |
| Model server | A program that keeps a model loaded and answers requests from other programs |
| Cluster / node | Several computers working as one team / one of those computers (here, one PGX) |
| GPU | A chip that does huge amounts of simple math at once; used to run AI models |
| Slice / piece | A share of the work, so different computers can handle different parts |
| Task array | Fuzzball running the same step once per slice |
| Container (arm64) | A packaged copy of our software that runs the same everywhere; arm64 is the chip type the PGX uses |
| Speedup | Time on one computer divided by time on two |
