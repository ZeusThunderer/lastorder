# Harness wrappers

The workplace above is harness-agnostic: everything the node is lives in `AGENTS.md`,
`PERSONA.md` and the skills, and any coding harness that reads `AGENTS.md` and speaks MCP
can run it (ADR-032). A harness gets only a thin config: where the MCP servers are and how
to surface the skills as commands.

## What the runner does at birth (T-027)

1. Copy the whole `agent/last_order/` template into the node's workplace.
2. Render `PERSONA.md` through `runtime/lastorder/persona.py`
   (`PersonaProvider.render_core`): name and speech manner from the naming preset
   (ADR-018, both presets render the same file), dealt traits in words, the dealt quirk.
   The trait numbers stay in `birth.json`.
3. Drop the chosen harness's files at the workplace root:
   - ZCode: `harness/zcode/zcode.json` as `zcode.json` (MCP servers; the T-024 spike
     verified ZCode merges it from the working directory), `harness/zcode/skills/` into
     `.zcode/skills/`. The Coding Plan provider is rendered by the container entrypoint,
     never stored here.
   - OpenCode: `harness/opencode/opencode.json` as `opencode.json`, and
     `harness/opencode/command/` into `.opencode/command/`. The model comes from the
     runner/gateway policy, not from this file.
4. Ship `agent/shared/` next to the workplace: the shared rules are network instructions
   (ADR-034); the node reads them read-only.
5. Point `body` at the slot's own body (`body-<N>:8700/mcp`) and add the network MCP
   server (`hive`) when it ships (T-028). Today only the body MCP exists, so the configs
   carry it alone.

The skill stubs under `harness/*/` never duplicate the canonical text: they point at
`skills/<name>/SKILL.md` in the workplace root, so there is one source to edit.

No secrets live in this directory: keys belong to the gateway and the container
entrypoints (ADR-033).
