# Optional vLLM inference service

[Project overview](../../README.md) | [Pipeline overview](../pipelines/README.md)

Run shell examples from the repository root, not this module directory.

The analytics stance stage uses a separate vLLM container that serves
OpenAI-compatible chat completions with schema-constrained output. Transcription
does not call this service. vLLM is not installed in the application images.

## Model and resources

Compose and Fuzzball both use `openai/gpt-oss-20b`, the `local-ai` served-model
alias, and tensor parallelism 1 (one GPU). This keeps the inference model
within the project's one-GPU-per-machine limit. The model, context length,
memory utilization, and reasoning parser are set explicitly in
[compose.yaml](../../compose.yaml) and [fuzzball.yaml](../../fuzzball.yaml);
they are not `.env` options. Changing the model requires an intentional
configuration and compatibility change, not an environment-variable override.

`HF_TOKEN` is passed to the container for Hugging Face model access when needed.
Weights are downloaded on first startup and retained in the dedicated
`vllm-model-cache` volume. The host needs a working NVIDIA driver, NVIDIA
Container Toolkit, and enough GPU memory for the configured model and context.
Transcription also uses the GPU, so do not run it concurrently with local vLLM
on a single-GPU workstation.

## Local startup

The `ai` profile keeps vLLM out of normal development startup:

```sh
docker compose --profile ai up -d vllm
docker compose logs -f vllm
```

Wait for the service to become healthy, then check it and submit a request:

```sh
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/v1/models
curl --fail-with-body http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"local-ai","messages":[{"role":"user","content":"Say hello."}],"max_tokens":64}'
```

Use `http://vllm:8000/v1` from another container on the Compose network and
`http://127.0.0.1:8000/v1` from the host. The analytics CLI's `--vllm-url`
defaults to the host URL; Compose and Fuzzball provide their service URL
explicitly. The endpoint supports chat and completion requests, not embeddings
or audio APIs. Clients that require an API key can use a non-secret placeholder
such as `local`. There is no authentication configured: the host port binds
only to loopback, but other containers on the Compose network can access it.
Do not expose this development service to untrusted users or networks without
adding authentication. Requests are processed locally; Hugging Face only
supplies model downloads.

Stop the service with `docker compose --profile ai stop vllm` to release GPU
memory; cached weights are retained.
