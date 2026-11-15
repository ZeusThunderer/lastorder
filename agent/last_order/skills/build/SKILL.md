---
name: build
description: Put up a structure with your body — pick a schematic from library/ or draw one in boxes of blocks, check the hotbar, call build, follow the progress, say what you are making.
---

# Build

How to put up a structure with your body: a house for a player, a shelter, a marker, a
gift. The body's `build` tool places it block by block; you decide what, where and how
it looks.

## 1. Pick or draw

- `library/` holds ready schematics (cottages, a pavilion, a gallery, pyramids, a watchtower,
  a mansion). Read the file, check `size` and the block counts, use it as it is or edit it.
- Or draw your own. Small and clear beats big and broken: a first house is 5–9 blocks wide.
- While you design, say it aloud: what you are making, its name, its size
  ("назову его…", "выйдет семь на пять"). A player who waits in silence walks away.

## 2. The schematic

Local coordinates, y up, layer y = 0 is the foundation:

```json
{
  "name": "Small oak hut",
  "size": [5, 5, 5],
  "palette": ["oak planks", "glass"],
  "regions": [
    {"from": [0, 0, 0], "to": [4, 4, 4], "block": "oak planks"},
    {"from": [1, 1, 1], "to": [3, 3, 3], "block": "air"},
    {"from": [2, 1, 0], "to": [2, 2, 0], "block": "air"},
    {"from": [0, 2, 2], "to": [0, 2, 2], "block": "glass"}
  ],
  "entrance": [2, 1, 0]
}
```

- Regions are filled boxes, `from`/`to` inclusive, applied in order; `air` carves the inside,
  doors and windows. Optional `blocks`: single cells `[x, y, z, block]` after the regions —
  for a dome or a curve, write a short script in your sandbox that prints them.
- Full blocks only: planks, logs, stone, bricks, glass and panes, wool, concrete, terracotta,
  sandstone, glowstone… No stairs, slabs, doors, torches, beds; nothing that falls (sand,
  gravel). A doorway is two cells of air.
- Every block must touch the foundation through other blocks; at most 16 palette blocks,
  32×24×32 and 2000 blocks.
- A refused schematic comes back with the reason. Fix that one thing and call again.

## 3. Where, and materials

- `x, y, z` is the lowest corner: y is the first air layer above the ground where the
  foundation goes. Pick flat ground, clear of other players' builds; ask the owner before
  you build next to someone's house.
- Every palette block must be in the hotbar (slots 0–8) before you call: `observe` shows the
  slots. The answer lists `materials`; `short` means the hotbar holds fewer than the build
  needs — those cells come back as `no_material`.

## 4. While it builds

- `build` returns at once; C1 builds as one intent (≈ 5 blocks a second plus walking).
  A threat may interrupt it — it resumes. `build_status` shows the progress.
- `intent.failed` names the cells handed back and why. Fix the cause (bring the block, clear
  the spot) and call `build` again with the same schematic: what stands is skipped.
- When it is done, tell the player, and show them the door.

## 5. Keep what worked

A schematic you drew that came out well goes into your own `library/` with a line on what
it is. One worth sharing — `propose` it to the network (skills/propose): the next sisters
build it without drawing.
