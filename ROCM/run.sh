#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
compose() {
    docker compose --env-file "$HERE/.env" -f "$HERE/compose.yaml" "$@"
}
job() {
    compose --profile jobs run --rm --no-deps "$@"
}
cleanup() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then
        printf '%s\n' 'ROCm smoke run FAILED; volumes and logs retained.' >&2
        if ! compose --profile ai --profile web logs --no-color --tail=100 >&2; then
            printf '%s\n' 'Could not retrieve service logs.' >&2
        fi
        if ! compose --profile web stop web >/dev/null; then
            printf '%s\n' 'Could not stop the failed local web service.' >&2
            status=1
        fi
    fi
    # GPU ownership is sequential, even when a one-off job fails.
    if ! compose --profile ai stop vllm >/dev/null; then
        printf '%s\n' 'Could not stop the local vLLM service; stop it manually.' >&2
        status=1
    fi
    exit "$status"
}

if [ ! -f "$HERE/.env" ]; then
    printf '%s\n' 'Copy ROCM/.env.example to ROCM/.env and configure it first.' >&2
    exit 1
fi
docker info >/dev/null
compose config --quiet
test -c /dev/kfd
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Stop any prior local model server before allocating this GPU to transcription.
compose --profile ai --profile web stop vllm web
compose --profile jobs --profile ai --profile web build \
    app transcription downloads documents analytics tracker \
    meetings documents-schema analytics-schema web vllm
compose up -d --wait --wait-timeout 180 meetings-db documents-db analytics-db
job meetings-role
job documents-role
job analytics-role
job meetings
job documents-schema
job analytics-schema
job meetings chi-ciq-database backup --output-dir /work/backups/meetings-before
job documents-schema chi-ciq-documents-database backup --output-dir /work/backups/documents-before
job analytics-schema chi-ciq-analytics-database backup --output-dir /work/backups/analytics-before
job downloads
job app
job transcription python /rocm/smoke.py gpu
job transcription
job documents
compose --profile ai up -d --wait --wait-timeout 2100 vllm
job analytics
compose --profile ai stop vllm
job tracker
job meetings chi-ciq-database check
job documents-schema chi-ciq-documents-database check
job analytics-schema chi-ciq-analytics-database check
job meetings chi-ciq-database backup --output-dir /work/backups/meetings-after
job documents-schema chi-ciq-documents-database backup --output-dir /work/backups/documents-after
job analytics-schema chi-ciq-analytics-database backup --output-dir /work/backups/analytics-after
(
    umask 077
    mkdir -p "$HERE/../data/rocm/analytics-backups"
)
compose --profile jobs run --rm --no-deps --user "$(id -u):$(id -g)" analytics-export
compose --profile web up -d --wait --wait-timeout 120 web
job verify
printf '%s\n' 'ROCm smoke run PASSED. Web remains running; inspect ROCM Compose volumes for outputs.'
printf 'Analytics database dumps: %s\n' "$HERE/../data/rocm/analytics-backups"
compose --profile web port web 8080
