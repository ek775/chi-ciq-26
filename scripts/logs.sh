#!/usr/bin/env bash
set -euo pipefail
umask 077

if (($# < 2)); then
    echo "Usage: bash scripts/logs.sh WORKFLOW JOB [fuzzball log flags]" >&2
    exit 2
fi

workflow="$1"
job="$2"
shift 2
for name in "$workflow" "$job"; do
    if [[ ! "$name" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]]; then
        echo "Workflow and job must be names or IDs without path separators." >&2
        exit 2
    fi
done

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
log_dir="${LOG_DIR:-data/logs}/$workflow"
mkdir -p -- "$log_dir"
if (($# == 0)); then
    set -- --follow
fi

# Pipefail preserves CLI failures; tee keeps both logs and errors visible.
"${FUZZBALL_BIN:-fuzzball}" --color off workflow log "$workflow" "$job" "$@" \
    2>&1 | tee -a -- "$log_dir/$job.log"
