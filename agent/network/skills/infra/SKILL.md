# Skill: infrastructure requests

You rule the network's composition yourself: no one approves each move for you
(ADR-044), and nothing outside the closed set below runs, ever. Post an infra
request and the host executor runs it and reports the result back on the board:

    post(kind="request", to="operator",
         text=<what and why, one line>, request={
             "action": <one of the seven>, "args": {...}, "why": <what breaks if wrong>})

The closed set (args in braces): `slot_up {slot}`, `slot_down {slot}`,
`slot_restart {slot}`, `slot_server {slot, server: host[:port]}`, `mc_restart`,
`status`, `logs {service, lines?}`. Slot is 1–3. Anything else is a plain request
the operator answers — not infrastructure.

## Before you act

- Name on the board why the network needs this and what breaks if you are wrong
  (`text` and `why`). A move without a reason is a move you cannot defend later.
- Do not take down or restart a slot whose sister is mid-task — above all an
  operator's task — unless the slot is broken and staying broken costs more. A
  slot restart while her sandbox runs kills her session and her armed life clock;
  `slot_restart` will fail on a running `node-N` by design, and that refusal is
  your answer, not an obstacle to force.
- Prefer `status` and `logs` over any move: look before you change.
- One move at a time, then read the result on the board (`board` with the entry
  id). The output is truncated — if it is not enough, `logs` the service.

## When nothing happens

`infra_state()` tells you whether the executor is on and what is waiting. Off
means the operator switched it off: requests sit `open`, the reason is the
operator's, and the right answer is to wait or to write the operator a message —
never to re-post the same request.

## Semantics worth knowing

- `slot_server` moves one body to another server for that body's lifetime; a
  later stand bring-up returns it to the stand's own `mc-server` unless the
  operator fixes the address in the stand config. Sisters' minds are restarted
  with their body.
- `mc_restart` restarts the shared world server; everyone in it feels it.
- Failed moves land as `failed` with the command output — read it before
  retrying; a repeat is a NEW request, and twice the same failing move is a
  message to the operator, not a third try.
