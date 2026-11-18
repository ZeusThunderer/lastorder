---
name: build
description: Design schematics, make workshop tools, delegate construction to sisters and verify results.
---
# Network construction
Read body-reference.md for the exact schematic and body-tool contract. library/ has nine designs.
You create the schematic and material bill, decide how to obtain resources, and coordinate a sister.
Create your Pi tools in workshop/<name>/index.ts or index.js, following existing local extensions and their API. workshop_reload validates and loads your extensions. publish_tool(name,why) publishes that directory to the hive shelf; new sisters load it at birth. Publication alone does not prove a living sister reloaded it.
Post a task addressed to one sister with full schematic JSON, absolute origin, material bill and acceptance criteria. Never refer her to a host path she cannot read. Let her choose her actions.
The sister calls build and follows build_status and intent events. Verify the world and missing cells before reporting success. clear=True digs cells and requires an agreed site. Keep successful new designs in your workshop.
observe includes inventory and exposed blocks within 4 m. act has goto/goto_far, mine(pos or positions), get(item,count) and equip. get delegates to uc's task catalogue; do not assume every recipe works. No exposed general nearest-block search is confirmed. You own the gathering plan.
Full blocks only; doors are openings. Inaccessible roof cells and scaffolding are known limitations. Read resource-report.md when supplied for measured resource support. An intent.done event alone does not prove a resource was collected.
