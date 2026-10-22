"""The node runner (T-027, NETWORK.md section 3): one slot's life without a human.

`node-N` runs `python -m runtime.node`: the runner births a sister (hive `birth`, the
life clock armed at `mind-N` BEFORE any session starts — otherwise the agent could arm
another serial from bash), keeps her long harness session alive (a crashed harness
restarts on the same workplace with a growing pause), and on the death event from
`mind-N` walks the strict path: stop the life session → `mark_dead` → the will session
behind a timeout → the staged `submit_will` or the emergency will (`will.py`) → `retire`
→ the read-only archive → `respawn` → the next birth. There is no death without a will.

The runner never sets the agent's rhythm and never decides for her (ADR-035). It is a
service, not a solver: behaviour lives in the workplace files (ADR-032).
"""
