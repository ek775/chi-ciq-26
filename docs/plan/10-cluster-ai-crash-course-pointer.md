> Copied from the claude.ai project "govbotgo" on 2026-11-14.
> Original doc: "Cluster & AI Crash Course - pointer" (created 2026-11-06).

# Know Your Alder — Cluster & AI Crash Course (pointer)

Written Nov 5, 2026; **rewritten Nov 7, 2026** after feedback (explain from the whole system down to hardware, define every term by what the machine does, cover Python/Docker/AI basics for app developers, relate the toy samples to real HPC practice, focus on on-site troubleshooting). The course itself is the Claude doc "Know Your Alder — Cluster & AI Crash Course" (https://claude.ai/artifact/FsEjQNMeQsHNwwa5Z6r4LT), about 90 minutes of reading, with a sample folder `kya-samples.zip` (Mac, offline).

## Parts (Nov 7 version)
0 About (goal, order, the 4-step troubleshooting method) · 1 The whole system: six layers (our code, scheduler, container runtime, container software, OS, hardware), a drawn diagram, one task followed through every layer, the two phases · 2 OS basics (paths, processes, shell + PATH, environment variables, exit codes, logs, permissions/root, ports and sockets, disk) · 3 Python for app developers (interpreter, packages, pip, venvs, tracebacks) · 4 Hardware + GPU software chain (CPU vs GPU, kernels, shared memory, bandwidth, arm64/amd64, driver → CUDA → PyTorch, three compatibility rules) · 5 Containers (namespaces/cgroups, images/layers/tags/digests, Docker's parts, GPUs in containers, Docker vs Podman vs Apptainer vs Fuzzball Substrate, OCI) · 6 AI models (weights, inference, batching, precision, Whisper/pyannote/LLM, KV cache, model servers, OpenAI API, Hugging Face, industry engines) · 7 Clusters and schedulers (scheduler loop, requested vs used, independent vs tightly coupled work, MPI/NCCL, speedup/Amdahl, idempotency, shared storage, Slurm/Kubernetes/PBS/Ray/Fuzzball mapping) · 8 Fuzzball (Orchestrate, Substrate, CLI, Fuzzfile line by line, services/endpoints, catalog vLLM + autoscaling) · 9 Monitoring and measuring (DCGM/Prometheus/Grafana in industry) · 10 Troubleshooting (who printed it, one-layer test ladder, bring-up checklist, symptom table, six worked examples incl. the two Docker errors) · 11 Samples vs real world · 12 Cheat sheet, glossary, sources.

## Industry facts used (with sources in the doc)
- Slurm runs on about 60% of TOP500 systems (HPCwire, May 2026); NVIDIA acquired SchedMD in Dec 2025 and says Slurm stays open source.
- Kubernetes for AI batch work adds Kueue / Volcano / KAI; Dynamic Resource Allocation GA since 1.34.
- HPC centres use Apptainer rather than Docker (no root daemon). Fuzzball Substrate is CIQ's own runtime: SIF natively, Docker/OCI images converted on use; Orchestrate runs on Kubernetes, jobs run outside it.
- LLM serving: vLLM, SGLang, TensorRT-LLM; Triton and Dynamo sit above engines. GPU monitoring: DCGM + dcgm-exporter + Prometheus + Grafana.

## Samples (kya-samples.zip)
- 00_check_mac.sh; 01_containers (Docker walkthrough); 02_task_array (mini_fuzz.py toy scheduler, cut.py greedy balancing, idempotent fake_transcribe.py, timeline.py); 03_llm_server (mock OpenAI-compatible server, parallel_requests.py, name_speakers_demo.py); 04_autoscaler (autoscale_sim.py replaying the plan's reading of vLLM autoscaling); 05_whisper (offline fake hearing via macOS `say`, batch-size RTF test); pgx_reference (hello.fz, array2.fz, gpu_log.sh).
- Tested before sending: 02, 03 (mock), 04. Not runnable in the build environment: 01, 05, Ollama runs, pgx_reference.
- Setup notes learned with the user: run `sh 01_containers/run.sh` once online instead of separate `docker pull --platform` commands (on Docker's older image store the second pull replaces the first under the same tag). Common Mac Docker errors: `docker.sock` missing (Docker Desktop not running or context wrong → `docker context use desktop-linux`); `docker-credential-desktop` not in PATH (reopen Terminal, remove Homebrew docker, or add `/Applications/Docker.app/Contents/Resources/bin` to PATH).

## Findings that change the plan
1. **Task arrays must start at 1** (Fuzzfile v4 reference). The design doc and plan sketched `start: 0, end: 15`; use 1–16 and number slices 1–16 in cut.py.
2. Task arrays can't be combined with `network` or `multinode`; a `resource` block needs both cpu and memory; persistent volumes must be created before the workflow.
3. Jobs reach services by name inside one workflow; service ports may be remapped, so never connect to `<node>:<port>`; use the endpoint address.
4. **Speed test 2 (whole files vs pieces) may show almost no difference on the full 41 hearings with 2 machines**: in the toy run, 4 hearings incl. one 11 h gave 1.61× vs 1.81×, but 40 hearings gave 1.89× vs 1.99×. The long tail shows when the longest hearing is a large share of the set (the benchmark sets) and would grow with more machines.
5. **The client's `--parallel` must exceed what one vLLM replica can absorb**, or the proxy gives the second replica nothing. Measure one replica's saturation at bring-up with parallel_requests.py; set `--parallel` to at least twice that.
6. Small phase-2 batches finish before a second replica's cold start ends (simulation); consistent with the plan's honest caveat.
7. Fuzzball doesn't need Docker on the PGX; whether Docker is installed there for by-hand fallback runs must be checked at bring-up. First use of a large image on a node includes fetch + conversion time, so pre-load images during bring-up.
