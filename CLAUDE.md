# Project context for Claude Code

**Read `HANDOFF.md` first.** It is the current context pack: what this project is,
the plan that was decided, what the code actually does today, how the code has
drifted from the plan, and the recommended path forward.

Quick orientation:
- This is an app for Chicago City Council meetings, built for a 1-day
  HPC/AI hackathon judged on **real cluster scaling** across **two ThinkStation
  PGX machines (one Arm/GB10 GPU each; GPUs can't be shared; images must be
  arm64)**, run with **CIQ Fuzzball**.
- The code today is a per-representative **policy-stance + sentiment dashboard**
  (3 Postgres DBs, a transcription/diarization pipeline, an analytics batch job,
  a FastAPI web app). It is solid engineering but a **different product** than the
  plan, and it attributes speakers by **voiceprint**, which the plan warned
  against.
- The planned product is a **promise/commitment tracker + five-minute meeting
  digests**, built on a verified-quote `statements.jsonl` knowledge base. See
  HANDOFF.md §2 for the exact data contract and §5 for the path forward.

Non-negotiables to respect while coding:
- **Never attribute a quote to an unconfirmed speaker** — leave it unattributed.
  A wrong name is the one error that sinks trust.
- **Every public quote must be verified word-for-word** against the transcript.
- **Size any GPU model to fit one GPU per machine** (~20B, e.g. gpt-oss-20b), not
  the ~306 GB default currently in the analytics config.

The fuller originals of the plan live in the claude.ai project "govbotgo"
(not on disk); HANDOFF.md copies the parts that matter for coding.
