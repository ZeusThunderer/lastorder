"""Index of the GPU whose name contains JEV_GPU_NAME (default 'RTX 2070'), or -1.

Runs inside the jev image: CUDA enumeration order there differs from the host and from
nvidia-smi on the host, so the card is identified by name, not by a hardcoded index.
"""

import os

import torch

target = os.environ.get("JEV_GPU_NAME", "RTX 2070")
found = -1
for i in range(torch.cuda.device_count()):
    if target in torch.cuda.get_device_name(i):
        found = i
        break
print(found)
