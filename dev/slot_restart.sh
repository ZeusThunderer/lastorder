#!/usr/bin/env bash
# DEPRECATED (ADR-050): this tool served the old scheme where mind-N lived in the
# body's network namespace and a bridge-safe order had to be choreographed by hand.
# Today the slots are separate services on link-N and the board's infra verbs do it:
#   - body trouble  -> `body_restart {slot}` (the mind asks, or the operator's button)
#   - mind trouble  -> `slot_restart {slot}` (recreates mind-N alone, the sister and
#                      her clock live on; the infra executor runs it)
#   - full recycle  -> `slot_down {slot}` + `slot_up {slot}`
# A parallel stand: docker/node/up.sh --project-styles aside, the same verbs apply.
# Refuse loudly so stale invocations do not run the old choreography.
set -euo pipefail
cat >&2 <<'EOF'
slot_restart: deprecated (ADR-050). Use the board's infra verbs instead:
  body_restart {slot} | slot_restart {slot} | slot_down/slot_up {slot}
posted by Last Order or the operator's board buttons; the infra executor runs them.
EOF
exit 2
