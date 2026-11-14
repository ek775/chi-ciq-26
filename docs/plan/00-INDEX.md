# Plan docs (copied from the claude.ai project "govbotgo")

Copied into the repo on 2026-11-14 so the full plan travels with the code.
These are point-in-time copies; the living versions are in the claude.ai
project. Start with `../../HANDOFF.md` for the short, current summary — these
are the fuller originals it draws on.

**Important:** the project **pivoted** in mid-November. Docs 01-04 are the
current direction; docs 05-10 are the earlier "Know Your Alder" framing and
build/hardware detail that the pivot kept or replaced. Read in this order:

Current direction (Nov 12-14):
1. `01-product-direction-and-scale-re-evaluation.md` — the pivot; most important.
2. `02-implementation-handoff.md` — decisions + data contract for building.
3. `03-layer-2-output-contract.md` — exact `statements.jsonl` schema + invariants.
4. `04-fuzzball-crash-course-handoff.md` — Fuzzball explained from scratch.

Still-relevant earlier docs:
5. `05-know-your-alder-implementation-plan.md` — **source of truth for hardware,
   Fuzzball, containers, arm64, the vLLM catalog, the 2-day playbook.** The pivot
   reused all of this; only the application on top changed.
6. `06-know-your-alder-plan-and-architecture.md` — the original product design.
7. `07-brainstorm-handoff.md` — idea history: every option considered and why.
8. `08-application-drafting-handoff.md` — the hackathon application text + constraints.
9. `09-background-and-purpose.md` — the one-paragraph hackathon framing.
10. `10-cluster-ai-crash-course-pointer.md` — pointer to the full cluster/AI course,
    plus Fuzzball findings that change the plan.
