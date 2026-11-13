#!/bin/sh
# Pin the voice service to one GPU by name (ADR-055), the jev way: CUDA_VISIBLE_DEVICES
# takes a UUID, stable where indexes are not (WSL2 reorders them). Empty TTS_GPU_NAME
# keeps whatever the container sees.
set -e
NAME="${TTS_GPU_NAME:-5070 Ti}"
if [ -n "$NAME" ] && command -v nvidia-smi >/dev/null 2>&1; then
    UUID=$(nvidia-smi --query-gpu=uuid,name --format=csv,noheader | while IFS=, read -r u n; do
        case "$n" in *"$NAME"*) echo "$u" && break ;; esac
    done)
    if [ -z "$UUID" ]; then
        echo "pin_gpu: GPU '$NAME' not visible in this container" >&2
        exit 1
    fi
    echo "pin_gpu: CUDA_VISIBLE_DEVICES=$UUID ($NAME)"
    export CUDA_VISIBLE_DEVICES=$UUID
fi
exec "$@"
