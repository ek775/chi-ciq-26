# Optional vLLM inference service

[Project overview](../../README.md) | [Pipeline overview](../pipelines/README.md)

Run shell examples from the repository root, not this module directory.

This namespace describes an upstream image, not local inference code. It is
excluded from Python package discovery so it cannot shadow upstream `vllm`.
Transcription does not call this service. The
[analytics pipeline](../pipelines/analytics/README.md) calls it for policy stance.

## Optional AI inference server

A separate `vllm` container serves OpenAI-compatible text completion and chat
completion endpoints. The analytics stance stage sends temperature-0 chat
requests with `response_format` JSON schemas (structured outputs). It uses the official, version-pinned
vLLM image; it does not install vLLM into the transcription container or change
the current transcription workflow.

Set `VLLM_MODEL` in `.env` to a supported Hugging Face text-generation model with
a chat template. The default is Z.ai's
[`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) (MIT
license; 321B-parameter mixture of experts with 18B active), which requires vLLM
0.29.0 or later; the pinned image is v0.32.0. GLM-5.3 always reasons before
answering: `VLLM_EXTRA_ARGS` (default `--reasoning-parser glm47`) separates the
reasoning from the answer, and analytics sends
`chat_template_kwargs.reasoning_effort` (`VLLM_REASONING_EFFORT`, default `low`;
`high` or `max` are slower) with a `VLLM_REASONING_TOKENS` thinking budget.

| Model | GPUs | `.env` changes |
|---|---|---|
| `zai-org/GLM-5.3-Flash` (default) | ~306 GiB FP8, e.g. 8× H100/H200 | none |
| `zai-org/GLM-4.7-Flash` | one 80 GB GPU | `VLLM_TENSOR_PARALLEL_SIZE=1` |
| `Qwen/Qwen2.5-0.5B-Instruct` (integration only) | any small GPU | `VLLM_TENSOR_PARALLEL_SIZE=1`, `VLLM_EXTRA_ARGS=`, `VLLM_REASONING_EFFORT=`, `VLLM_MAX_MODEL_LEN=4096` |

The native Rust frontend for GLM-5.3 (`--tokenizer-mode glm5_next`) needs an
image newer than v0.32.0; the default Python frontend works.
Analytics records the served model's `root` with each stance, so changing
`VLLM_MODEL` causes stances to be recomputed on the next run.
`HF_TOKEN` is passed through for gated models; accept their terms before use.
Weights are downloaded on first startup and kept in a separate Docker volume.

The service requires a working NVIDIA driver, NVIDIA Container Toolkit, and
enough GPU memory for the selected model. `VLLM_MAX_MODEL_LEN` defaults to
32768 tokens, `VLLM_GPU_MEMORY_UTILIZATION` to 0.9 (a fraction of each GPU's
memory), and `VLLM_TENSOR_PARALLEL_SIZE` to 8 (GPUs). Adjust these in `.env` for
your hardware. Transcription also uses the GPU, so
these settings do not guarantee both workloads fit; stop one if memory is tight.
The `ai` profile keeps vLLM out of normal development startup:

```sh
docker compose --profile ai up -d vllm
docker compose logs -f vllm
```

First startup downloads hundreds of GiB and can take up to an hour (the engine
timeout and health check allow 60 minutes). Wait for
`docker compose --profile ai ps vllm` to show healthy, then check and submit a
request:

```sh
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/v1/models
curl --fail-with-body http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"local-ai","messages":[{"role":"user","content":"Say hello."}],"max_tokens":64}'
```

Use base URL `http://vllm:8000/v1` from `app` or another container on the same
Compose network, and `http://127.0.0.1:8000/v1` from the host. Request model name
`local-ai` is a stable alias even when `VLLM_MODEL` changes. The default supports
`/v1/chat/completions` and `/v1/completions`, not embeddings or audio APIs.
Clients that require an API key can use a non-secret placeholder such as `local`.
There is no authentication configured: the host port binds only to loopback,
but other containers on the Compose network can access it. Do not expose this
development service to untrusted users or networks without adding authentication.
Requests are processed locally; Hugging Face only supplies model downloads.

After changing model settings, rerun the startup command to recreate the service.
Stop it with `docker compose --profile ai stop vllm` to release GPU memory; cached
weights are retained.
