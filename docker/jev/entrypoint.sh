#!/bin/sh
# Pin the service to one GPU by name before handing over to fastjev-serve.
# On Docker Desktop/WSL2 the compose `device_ids` filter does not reach CUDA: every host GPU
# stays visible inside the container and the enumeration order is unstable (observed: container
# index 0 was the 5070 Ti). fastjev requires exactly one visible CUDA device, so we resolve the
# index by name and export CUDA_VISIBLE_DEVICES ourselves.
set -e
IDX=$(python /usr/local/bin/pick_gpu.py)
if [ "$IDX" -lt 0 ]; then
    echo "entrypoint: GPU '${JEV_GPU_NAME:-RTX 2070}' not visible in this container" >&2
    exit 1
fi
echo "entrypoint: CUDA_VISIBLE_DEVICES=$IDX (${JEV_GPU_NAME:-RTX 2070})"
export CUDA_VISIBLE_DEVICES=$IDX
exec fastjev-serve "$@"
