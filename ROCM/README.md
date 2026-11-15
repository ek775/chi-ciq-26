# Isolated ROCm Docker smoke pipeline

This directory is a local AMD/Linux alternative, **not a change to tomorrow's
Fuzzball deployment**. All ten original Dockerfiles have copies at the same
relative paths here. Build contexts remain the repository root. The original
Dockerfiles, root Compose, Python pipelines, image publishing workflows, and
Fuzzfiles are unchanged.

The [standalone Compose file](compose.yaml) uses its own project, databases,
volumes, image tags, and loopback web port. Do not combine it with the root
Compose file. PostgreSQL, downloads, documents, analytics classifiers, tracker,
and web remain CPU-only; only the development image, transcription, and the
separate vLLM server need ROCm. GPU ownership is sequential.

## Requirements

- A supported AMD GPU on a Linux x86-64 host with the AMD kernel driver and
  working ROCm access. This is not for the NVIDIA/Arm GB10 Fuzzball machines.
- Docker Engine and Compose with `up --wait`, and permission to use the Docker
  daemon. Verify `docker info` succeeds in the same shell that will run the test.
- `/dev/kfd` and the selected `/dev/dri/renderD*` device must exist and be
  accessible. `rocminfo` must enumerate the selected GPU.
- A Hugging Face read token with the
  [pyannote model terms](https://huggingface.co/pyannote/speaker-diarization-community-1)
  accepted. Network access is needed for Vimeo/CDN and model/image downloads.
- Substantial free disk space for ROCm images, raw audio, caches, and backups.
  First builds/downloads can dominate runtime; the smaller input batch does not
  eliminate these costs.

Manual passthrough maps `/dev/kfd` and **one render device**, adds its numeric
host group ID, and sets `HIP_VISIBLE_DEVICES`. There is no `gpus: all`, NVIDIA
runtime, privileged mode, host IPC, or blanket `/dev/dri` exposure. Select the
render node and ROCm ordinal for the **same** discrete GPU; do not assume an
integrated GPU is supported by the model server. No `HSA_OVERRIDE_GFX_VERSION`
workaround is applied to unsupported hardware.

On the host inspected during development, the discrete RX 7900 XTX was
`gfx1100`, `/dev/dri/renderD128`, ordinal 0, group 987; the other render device
was an integrated Radeon 780M. These values are host-specific, not universal.

### Image choices

- [Development](Dockerfile) and
  [transcription](src/pipelines/transcription/Dockerfile) retain Python 3.12
  and FFmpeg, explicitly selecting PyTorch/TorchAudio 2.8.0 ROCm 6.4,
  TorchCodec 0.7.0, pyannote.audio 4.0.4, and Whisper 20250625.
  Whisper depends on the `triton` distribution, whose files overlap ROCm
  Triton. The build restores `pytorch-triton-rocm` 3.4.0 last and verifies its
  installed file hashes, package dependencies, audio-decoder imports, and HIP.
  Do not subsequently replace Torch/Triton inside these images.
- [vLLM](src/vllm/Dockerfile) uses AMD's Radeon/Navi image
  `rocm/vllm-dev:rocm7.2.1_navi_ubuntu24.04_py3.12_pytorch_2.9_vllm_0.16.0`,
  documented in AMD's
  [Radeon container build guide](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/advanced/advancedrad/linux/llm/build-docker-image.html).
  This is a **gfx1100-targeted** image, not a universal AMD GPU image.
  Its explicit entrypoint starts `vllm serve`; the Compose command selects
  the small test model and eager single-GPU execution.
- The other eight Dockerfiles are equivalent CPU-only copies with an
  explanatory comment. CPU documents/classifiers still install CPU Torch;
  database clients, downloader, tracker, and web do not need a ROCm runtime.

The two GPU image families have different userspace ROCm versions. The host
kernel driver must support both; having `rocminfo` installed does not prove
that compatibility. PyTorch.org ROCm wheels are not the same as AMD-validated
Radeon containers. Build-time guards do not allocate a GPU; the runner's
runtime GPU checks and real model execution remain essential.

## Run

From the repository root:

```sh
cp ROCM/.env.example ROCM/.env
stat -c '%n %g' /dev/dri/renderD*
rocminfo
docker info
```

Edit `ROCM/.env`: set `HF_TOKEN`, a URL-safe `ROCM_POSTGRES_PASSWORD`, and the
numeric `ROCM_RENDER_GID`; select `ROCM_RENDER_DEVICE` and
`ROCM_VISIBLE_DEVICE`. The local database owner and reader logins share this
development password, but have different permissions. Do not reuse production
credentials or publish this local dashboard.

```sh
sh ROCM/run.sh
```

No host Python installation is required. All downloading, model
inference, migrations, backups, tracker work, and API verification execute in
containers; the host shell only sequences Docker operations.

The runner:

1. Checks Docker/configuration, stops a previous local vLLM/web instance, and
   builds all ten copied images plus the ROCm vLLM image.
2. Starts three isolated pgvector databases, provisions SELECT-only source and
   web readers, migrates schemas, and makes pre-run backups.
3. Calls the existing downloads implementation with the **first ten unique
   IDs from the established packaged Vimeo list**, audio-only, two workers.
   It retains complete audio MP4s and their atomic publication receipts.
4. Records full-recording hashes and source URLs, and feeds the **complete
   downloaded audio MP4s** directly to transcription. No clipping, duration cap,
   or additional audio reencoding is performed.
5. Stages the first three sorted reference transcript TXT files, capped at
   12,000 characters each, for the independent documents pipeline. The archive
   itself is mounted read-only.
6. Runs ROCm GPU computation checks in both GPU Python images, refusing CPU
   fallback, then the unchanged transcription/diarization/embedding pipeline.
7. Runs CPU document ingestion, starts a single-GPU vLLM server, and runs
   **all analytics stages** with a snapshot roster and at most 20 stance
   requests per run. vLLM stops before the next phase.
8. Builds, publishes, and queries a tracker integration sample using exactly
   one real transcript segment per recording. Quotes must match byte-for-byte
   and speakers remain unconfirmed/null. This is a plumbing test, not automatic
   commitment extraction, reviewed identity evidence, or a generated digest.
9. Checks schemas, makes post-run backups, starts web, and checks the API,
   pages, exact source coverage/hashes, embeddings, classifier/topic/stance
   outputs, absence of unreviewed identity attribution, and tracker publication.
   Only then does it write `/work/report.json` and print `PASSED`.

Before web verification, the runner also exports the **entire analytics
database**, including its `tracker` schema, to the host directory
`data/rocm/analytics-backups/`. Each run creates a timestamped, uniquely named
`analytics-*.dump` PostgreSQL custom-format archive; earlier copies are never
overwritten. The existing backup helper publishes atomically after `pg_dump`
succeeds, and the runner checks the dump header and `pg_restore --list` before
reporting success. Export failure fails the pipeline.

The export runs as your host UID/GID. Dumps are private, owned by you, gitignored,
and survive Docker container/volume removal. This host directory is shared
across local project names; unique archive names prevent collisions.
Inspect or restore a selected archive using PostgreSQL 17 client tools:

```sh
ls -lh data/rocm/analytics-backups/
pg_restore --list data/rocm/analytics-backups/analytics-<timestamp>-<id>.dump
# Restore only into a separately created disposable pgvector-enabled database.
pg_restore --no-owner --no-acl --dbname="$RESTORE_DATABASE_URL" \
  data/rocm/analytics-backups/analytics-<timestamp>-<id>.dump
```

Archives omit database roles/ownership grants; provision appropriate reader
roles separately when deploying a restored copy. This is an analytics snapshot,
not a substitute for the separate meetings/documents backups.

The tiny `Qwen/Qwen2.5-0.5B-Instruct` model is for integration throughput,
**not policy-analysis quality**. Stances include unattributed text so the API
path is actually exercised without inventing speaker labels. Topic relevance
is deliberately permissive in this test. No manually labeled speakers or
voiceprint centroids are seeded. Production thresholds are untouched.

All ten complete audio recordings are transcribed. Long council meetings can
take substantially longer than a short smoke sample; there is no audio duration
limit. The reference document sample and per-run stance request limit remain
bounded. Missing nonempty transcripts fail visibly.

## Inspect, retry, and stop

After success, the web application remains at **http://127.0.0.1:18080/**,
with its dashboard at **http://127.0.0.1:18080/dashboard**. No database or vLLM
host ports are published.

```sh
docker compose --env-file ROCM/.env -f ROCM/compose.yaml --profile web ps
docker compose --env-file ROCM/.env -f ROCM/compose.yaml --profile ai --profile web logs
# Display the persistent completion report:
docker compose --env-file ROCM/.env -f ROCM/compose.yaml --profile jobs \
  run --rm --no-deps tracker python -m json.tool /work/report.json
# Stop this project without removing any data or caches:
docker compose --env-file ROCM/.env -f ROCM/compose.yaml \
  --profile jobs --profile ai --profile web down
```

The project work volume holds `raw/`, `documents/`, `manifest.json`,
`tracker-inputs/`, `tracker/`, `backups/`, and the success-only `report.json`.
Failures stop the local model server and web service, propagate a nonzero exit, and retain
logs/data for diagnosis. Completed unchanged acquisitions and ingestion are
idempotent. Re-running analytics may process another batch of pending stances;
the stance limit applies per run, not to the accumulated database.

Changing the sample count or reference input after preparing the work
volume fails rather than mixing test coverage. Use a fresh isolated project
for different settings:

```sh
COMPOSE_PROJECT_NAME=chi-ciq-rocm-longer sh ROCM/run.sh
```

Use that same `COMPOSE_PROJECT_NAME` with subsequent inspect/down commands.
Changing project names reuses image tags but creates fresh databases and
caches. The default audio count is 10 (allowed 1-20); document count is 3
(allowed 1-10); download workers are 2 (allowed 1-4).

## Validation status

Existing work volumes from the former clipped-audio pipeline must also use a
fresh project name; their manifests are rejected instead of mixing partial
transcripts with full recordings. `ROCM_CLIP_SECONDS` is no longer used.

The full-recording preparation, passthrough configuration, exact-quote checks,
and runner ordering/failure handling have automated tests in
[`tests/test_rocm.py`](../tests/test_rocm.py). Compose and shell syntax can be
checked without the daemon:

```sh
HF_TOKEN=validation ROCM_POSTGRES_PASSWORD=validation ROCM_RENDER_GID=0 \
  docker compose --env-file /dev/null -f ROCM/compose.yaml config --quiet
sh -n ROCM/run.sh
pytest -q tests/test_rocm.py
```

**Live end-to-end execution was blocked in the authoring session:** this user
could not access `/var/run/docker.sock`, and noninteractive sudo required a
password. Consequently images, public downloads, ROCm inference, and database
publication have not been exercised together on this host. A successful local
`sh ROCM/run.sh` and its newly written report are the required runtime proof;
configuration/unit tests alone do not establish ROCm model compatibility.

The focused tests also cover the existing downloader, tracker, and transcription
adapters. An additional run of the untouched Fuzzball/device-selection tests
found existing expectation mismatches: the vLLM cache volume is named
`vllm-model-data` rather than the expected `vllm-model-cache`, and two tests
expect device selection on stdout although the runtime logs it. These were
left untouched to avoid changing tomorrow's deployment.
