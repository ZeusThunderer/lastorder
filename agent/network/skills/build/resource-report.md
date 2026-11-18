# Resource capability reference — verified 2026-11-18

Live body: unionclef-1.21.11-0.95.66.jar, home server, slot 1. Tests ran without a sister controlling the body, at a natural site away from existing builds. The world's existing inventory was retained; these are incremental tests, not fresh-start survival tests.

| Capability | Loaded mod / wrapper | Observed result |
| --- | --- | --- |
| Find nearest block of a chosen type | No general public entry-point method in the loaded jar; no exposed sister tool | Missing. Do not assume this primitive exists. |
| Nearby blocks | getBlocksAround / observe | Mod radius 5 and requested radius 50 returned the same 1062 blocks; radius 4 returned 594. observe exposes visible blocks within radius 4, groups names and limits positions. These are client-world reads; no remote/server scan was added. |
| Coordinate navigation | gotoXYZ / act goto | Method exists; a local blocked destination did not move the body in 6 seconds. get resource chains did move it from [329,82,372] to [318,65,424]. General reachability is not proven. |
| Coordinate mining | mineBlocks, mineStatus / act mine | A dirt cell was removed; dirt dropped nearby. Mining does not guarantee the drop has entered inventory. |
| Pickup | Resource task through act get | After mining, get dirt increased inventory 8 → 9. Separate pickup-only control is not exposed. |
| Logs | ExecuteCommand / act get oak log | Inventory +3 logs. |
| Planks | ExecuteCommand / act get oak planks | One log consumed, +4 oak planks. |
| Cobblestone | ExecuteCommand / act get cobblestone | Inventory +1 cobblestone; travel/mining changed surrounding terrain. |
| Crafting table | ExecuteCommand / act get crafting table | Four planks consumed, +1 crafting table. |
| Wooden pickaxe | ExecuteCommand / act get wooden pickaxe | Target was a second pickaxe; after 120 seconds inventory stayed at one. Task stopped. Ingredients changed and a crafting table was placed: completion is NOT established. |
| Glass / furnace chain | ExecuteCommand / act get glass | Started with no coal or sand in inventory. +1 glass, +12 coal; a furnace appeared in observe. This verifies the combined gather/fuel/smelt/extract chain, not an independent furnace-control API. |
| Unsupported item | act get definitely nonexistent item | intent.failed; inventory unchanged. |
| Stop | act stop | Returned stopped=true. Resource timeouts were stopped, not retried indefinitely. |

Raw evidence is in development task T-080, resource-results.jsonl, primitive-results.jsonl and game-voice-results.jsonl. Successes above use inventory/world changes, not intent.done alone. Collection task events matched the intent ids in these runs. Every resource request was bounded at 120 seconds, glass at 300 seconds. Missing-resource exhaustion (finite unavailable but supported material) is not separately proven; the pickaxe run demonstrated bounded stalling.

LO owns the material list, acquisition strategy and any new workshop tools. This reference supplies capabilities and limitations only. The missing nearest-type block primitive requires a separate runtime/mod task with explicit loaded-chunk bounds, distance, no-result behavior and a sister-facing wrapper; do not build a planner into the runtime.
