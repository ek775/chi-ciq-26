> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Fuzzball Crash Course Handoff" (created 2026-11-13).

# Fuzzball Crash Course — Handoff

Written Nov 13, 2026. **For a fresh chat whose job is to teach the user Fuzzball from scratch.** This packs everything the govbotgo project already knows about Fuzzball into one place, so the new chat doesn't re-derive it. The user is a capable app developer but a **beginner in cluster computing** — teach in plain words, define every term by what the machine actually does, and say plainly when something is unverified rather than sounding confident.

There is already a broader course — the Claude doc "Know Your Alder — Cluster & AI Crash Course" (part 8 is Fuzzball) — and a "Cluster & AI Crash Course - pointer" doc in this project. This handoff is the Fuzzball-focused extract; use the full course for the surrounding concepts (containers, GPUs, schedulers).

---

## 0. How the user likes to learn

- Plain words; replace jargon with simple terms and define anything technical.
- Honesty over enthusiasm — flag what's a guess or untested.
- Relate each concept back to what we're actually building (below), not abstract examples.
- The user asks good "but why" questions; welcome them.

---

## 1. What Fuzzball is

Fuzzball is CIQ's system for running a **workflow** — a list of steps with dependencies — across a cluster of machines. You write the workflow in one YAML file (a **Fuzzfile**) and submit it; Fuzzball figures out which machine runs each step, in what order, and with which resources (CPU, memory, GPU). It is the cluster equivalent of a Makefile + a scheduler.

It has two parts:
- **Orchestrate** — the control plane (the brain that schedules and tracks workflows). It itself runs on Kubernetes, but the user's jobs run *outside* Kubernetes.
- **Substrate** — CIQ's own container runtime that actually runs each step on a node. It runs SIF images natively and **converts Docker/OCI images on first use**. Consequence for us: Docker does **not** need to be installed on the PGX machines, and the *first* use of a large image on a node includes fetch + conversion time — so images are pre-loaded during bring-up.

Where it sits among tools the user may have heard of: Slurm (the HPC default, ~60% of TOP500) and Kubernetes are the big schedulers; Fuzzball is a newer, workflow-file-driven alternative in the same role. The mental model "it's like Slurm but you describe the whole pipeline in one YAML file" is close enough to start.

**Authoritative references (always check these over memory):**
- Fuzzfile syntax: https://docs.ciq.com/fuzzball/4.1/appendices/workflow-syntax (we target **v4.1 / version `v4`**)
- Catalog apps (incl. vLLM): the `ctrliq/ciq-fuzzball-catalog` repo — `applications/vllm/values.yaml` and `template.yaml`

---

## 2. The Fuzzfile, anatomy

A Fuzzfile has roughly these top-level sections. **Every key below is sketched from the docs and must be verified against the v4.1 syntax reference** — treat the shapes as "approximately right," not gospel.

```yaml
version: v4

volumes:                 # shared storage the jobs read/write
  data:
    reference: alder-data   # a PERSISTENT volume must be created BEFORE the workflow runs

jobs:
  <job-name>:
    image: { uri: docker://<registry>/<image>:arm64 }   # OCI image; Substrate converts it
    command: [python, script.py, --flag, "value"]       # OR:
    script: |                                            # a multi-line shell script
      python a.py
      python b.py
    mounts: { /data: { location: data } }               # mount the volume into the job
    depends-on: [{ name: <other-job>, status: FINISHED }]  # ordering
    task-array: { start: 1, end: 16, concurrency: 2 }   # run the job N times in parallel
    resource:
      cpu: { cores: 8 }
      memory: { size: 64GB }                            # resource MUST have cpu AND memory
      devices: { nvidia.com/gpu: 1 }                    # ask for a whole GPU

services:                # long-running processes (e.g. a web server), reached by name
  # ...

```

Concepts to teach, each tied to why we use it:

- **Job** — one step. Has an image, a command (or script), the storage it mounts, and the resources it needs.
- **`image`** — the container the step runs inside. Must be the **arm64** build, because the PGX uses an Arm (GB10) chip.
- **`resource`** — what the step asks the scheduler for. Key lesson: **requested ≠ used**. Asking for 1 GPU reserves a whole GPU whether or not the code saturates it. Each PGX has exactly **one GPU**, so one GPU-requesting task fully occupies one machine.
- **`task-array`** — the heart of the parallelism. One job definition run many times, once per index. Fuzzball sets an environment variable (our docs use `${FB_TASK_ID}` — **verify the exact name**) to the index, and the script uses it to pick its slice of work (`--slice ${FB_TASK_ID}`). `concurrency` caps how many run at once: **`concurrency: 2` = both PGX busy, `concurrency: 1` = one-machine run.** That single switch is how we do the "1 node vs 2 nodes" speed comparison — nothing else changes.
- **`depends-on`** — ordering. A job with `status: FINISHED` on another job waits for it. This is how `join` waits for all transcription tasks.
- **`volumes` / `mounts`** — shared storage. All nodes see the same `/data`. Persistent volumes must exist *before* the workflow starts.
- **`services` and endpoints** — a long-running process (a model server, a web page). Reached **by name within one workflow**; service **ports may be remapped, so never hard-code `node:port`** — ask Fuzzball for the endpoint address.

---

## 3. The hard-won rules (project findings — these bit us in simulation)

1. **Task arrays must start at 1**, not 0 (v4 reference). Number slices 1–16 everywhere.
2. **Task arrays can't be combined with `network` or `multinode`.**
3. **A `resource` block needs both `cpu` and `memory`** or it's rejected.
4. **Persistent volumes must be created before the workflow runs.**
5. **Reach services by their endpoint address, not `node:port`** (ports get remapped). Within one workflow, jobs find a service by name; across workflows, use `fuzzball workflow endpoints list`.
6. **Idempotency matters** — every step should skip work already done, so a re-run or a killed task only does what's missing. (This is a cluster habit, not a Fuzzball feature: tasks can be retried or land anywhere.)
7. **Pre-load big images at bring-up** — first use on a node pays fetch + conversion cost.

---

## 4. The catalog and the model server (vLLM)

Fuzzball ships an **app catalog** — ready-made workflows you start with one command instead of writing YAML. We use it for the LLM server so we don't hand-build model serving:

```
fuzzball workflow catalog start vLLM --values \
  Model=hf://openai/gpt-oss-20b,MinReplicas=1,MaxReplicas=2,GpuMemoryUtilization=0.7,Volume=<model volume>
```

Teach these points:
- The vLLM catalog entry has a **built-in proxy** (one address in front of every copy of the model), so we don't need a separate gateway.
- **Autoscaling** is the money demo — it adds a second copy of the model on the second PGX when load is high, and removes it when idle:

| Setting | Our value | Meaning |
| --- | --- | --- |
| `MinReplicas` | 1 | always one copy running (0 would unload the model → slow cold restart) |
| `MaxReplicas` | 2 | one copy per PGX |
| scale-up trigger | ≥30 requests/min, or any failed request | adds a copy (at most once per `ScaleUpCooldown`, default 120s) |
| scale-down trigger | no request for 5 min | removes a copy (after `DrainPeriod`, default 300s) |
| `GpuMemoryUtilization` | 0.7 | lowered from 0.9 because the GB10's GPU shares memory with the system |
| per copy | 1 whole GPU | can't share a GPU with a Whisper task |

- Find the proxy address with `fuzzball workflow endpoints list`; the Python clients take it as `--server`.
- **`--parallel` on the client must exceed what one replica can absorb**, or the proxy never has a reason to start the second replica. Measure one replica's saturation at bring-up, set `--parallel` to ~2× that.
- **Honest caveat:** on a small batch the LLM phase can finish before the second replica's cold start completes, so the scale-up may not show. A bigger batch (more hearings / the full archive) is what makes it visible.

---

## 5. How we actually use Fuzzball (the concrete thing to teach against)

Two PGX machines, one GPU each, and **a GPU can't be shared between jobs**. So the run is **two phases**, each its own Fuzzfile, with the vLLM catalog command started between them:

- **Phase 1 — `fuzzfile_audio.yaml`:** `cut` (CPU) → a GPU task array that transcribes + voice-splits each slice (`concurrency: 2`) → `join` (CPU). Both GPUs busy; no model server running.
- **Between phases:** start vLLM via the catalog command; wait until its proxy answers a test request.
- **Phase 2 — `fuzzfile_text.yaml`:** CPU jobs (`speakers` → `statements` → `quotecheck`) that send requests to the vLLM proxy with `--parallel 32`. The request rate crosses the scale-up trigger and the second replica starts on the other PGX.
- A script `run_event.sh` runs the four steps in order; `--phase 1` / `--phase 2` run one at a time.

Draft Fuzzfiles (untested, placeholders in `<...>`) exist in the project discussion — the new chat can reconstruct them from §2–§5 or ask for them. The key open design choice: transcribe + voice-split as **one combined GPU task per slice** (avoids two GPU jobs contending for two GPUs) vs. two separate task arrays.

---

## 6. What's verified vs. still unknown

**Known from docs / simulation:** the two-phase shape, the autoscaling defaults, the seven rules in §3, the catalog command.

**Must be confirmed on the actual cluster at bring-up (can't be known from docs):**
- Exact Fuzzfile key names and nesting against the v4.1 reference (`volumes`, `mounts`, `depends-on`, the task-index env var name).
- The exact CLI to submit a Fuzzfile and to watch a workflow (we know `fuzzball workflow catalog start ...` and `fuzzball workflow endpoints list`; confirm the `workflow start` / status / logs commands from `fuzzball --help`).
- How a phase-2 job reaches the vLLM proxy that lives in a **separate** catalog workflow.
- Whether two jobs can share one GPU (if yes, the two phases could overlap — don't plan on it).
- Whether the catalog's default vLLM images have working **arm64 + GB10-GPU** builds (arm64 being *listed* doesn't prove GPU support); backup images are packed for this.
- Whether Docker is installed on the PGX for by-hand fallback runs.

**Good first lesson for the new chat:** walk one task (transcribing slice 7) down through the layers — our Python → the Fuzzball scheduler → Substrate → the container → the OS → the GPU — so the user sees where each term lives and where things can break. Then teach the Fuzzfile line by line against §2.
