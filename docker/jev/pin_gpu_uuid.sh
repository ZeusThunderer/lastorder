#!/bin/sh
# Pin this container to one GPU by UUID before exec'ing the command ($@).
# CUDA_VISIBLE_DEVICES accepts UUIDs, which are stable where indexes are not (WSL2 reorder).
set -e
NAME="${JEV_GPU_NAME:-RTX 2070}"
UUID=$(nvidia-smi --query-gpu=uuid,name --format=csv,noheader | while IFS=, read -r u n; do
    case "$n" in *"$NAME"*) echo "$u" && break ;; esac
done)
if [ -z "$UUID" ]; then
    echo "pin_gpu_uuid: GPU '$NAME' not visible in this container" >&2
    exit 1
fi
echo "pin_gpu_uuid: CUDA_VISIBLE_DEVICES=$UUID ($NAME)"
export CUDA_VISIBLE_DEVICES=$UUID
exec "$@"
