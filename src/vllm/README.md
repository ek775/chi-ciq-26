# Optional vLLM inference service

[Project overview](../../README.md) | [Pipeline overview](../pipelines/README.md)

Run shell examples from the repository root, not this module directory.

This namespace describes an upstream image, not local inference code. It is
excluded from Python package discovery so it cannot shadow upstream `vllm`.
Transcription does not call this service.

## Optional AI inference server

A separate `vllm` container serves OpenAI-compatible text completion and chat
completion endpoints for future pipelines. It uses the official, version-pinned
vLLM image; it does not install vLLM into the transcription container or change
the current transcription workflow.

Set `VLLM_MODEL` in `.env` to a supported Hugging Face text-generation model with
a chat template. The default in `.env.example`, `Qwen/Qwen2.5-0.5B-Instruct`, is a
small chat model for initial integration, not a production-quality recommendation.
`HF_TOKEN` is passed through for gated models; accept their terms before use.
Weights are downloaded on first startup and kept in a separate Docker volume.

The service requires a working NVIDIA driver, NVIDIA Container Toolkit, and
enough GPU memory for the selected model. `VLLM_MAX_MODEL_LEN` defaults to 4096
tokens and `VLLM_GPU_MEMORY_UTILIZATION` to 0.5 (a fraction of total GPU memory).
Adjust these in `.env` for your hardware. Transcription also uses the GPU, so
these settings do not guarantee both workloads fit; stop one if memory is tight.
The `ai` profile keeps vLLM out of normal development startup:

```sh
docker compose --profile ai up -d vllm
docker compose logs -f vllm
```

First startup can take several minutes to download and load the model. Wait for
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
