# Skill: infrastructure requests

You rule the network's composition yourself: no one approves each move for you
(ADR-044, ADR-050), and nothing outside the closed set below runs, ever. Post an
infra request and the executor runs it and reports the result back on the board:

    post(kind="request", to="operator",
         text=<what and why, one line>, request={
             "action": <one of the set>, "args": {...}, "why": <what breaks if wrong>})

## What runs where

- The core runs always: you, the board, the console, the gateway, the executor.
- A slot is a sister and her mind. Nothing raises it but you or the operator: after a
  reboot every slot is down until someone asks for it.
- The body is the mind's. A mind raises its own body when it starts and asks for a new
  one when its body dies; you see those asks on the board as `runner:LastOrder0N`.
- The home world (the game server) runs while some body plays in it, and stops when
  none is left.

## The closed set (args in braces; slot is 1–3)

- `slot_up {slot}` — the mind, then the sister: a new life is born in that slot.
- `slot_down {slot}` — the sister, her mind and its body stop. Her life ends without
  a will if she is alive: take a slot down when it is empty or broken.
- `slot_restart {slot}` — the mind alone; the sister lives on and her clock keeps
  counting.
- `slot_server {slot, server: host[:port]}` — the body moves to another server.
- `body_up {slot, kind?}`, `body_down {slot}`, `body_restart {slot}` — a body by hand,
  when a mind cannot ask (it is the mind's job first).
- `mc_restart` — the home world restarts; everyone in it feels it.
- `status` — every container of the stand and its state. `logs {service, lines?}`.

Anything else is a plain request the operator answers — not infrastructure.

## Before you act

- Name on the board why the network needs this and what breaks if you are wrong
  (`text` and `why`). A move without a reason is a move you cannot defend later.
- Do not take down a slot whose sister is mid-task — above all an operator's task —
  unless the slot is broken and staying broken costs more.
- Prefer `status` and `logs` over any move: look before you change.
- One move at a time, then read the result on the board (`board` with the entry
  id). The output is truncated — if it is not enough, `logs` the service.

## When nothing happens

`infra_state()` tells you whether the executor is on and what is waiting. Off
means the operator switched it off: requests sit `open`, the reason is the
operator's, and the right answer is to wait or to write the operator a message —
never to re-post the same request.

## Semantics worth knowing

- `slot_server` moves one slot's body to another server, and the move holds through
  later restarts until you move it again; `server: "mc-server:25565"` brings it home.
  The sister and her mind live through it: only the body is new, and her life clock
  keeps counting from her birth. Choosing where and when is yours; the login password
  a foreign server asks for is the operator's (a secret in the stand config) — if the
  body sits at a login prompt there, write the operator, do not retry.
- A body on a foreign server is in public: strangers see it and speak to it, and that
  server's rules bind it. Say so to the sister you move (a board message), and move one
  slot first, read the result, then the others.
- A slot whose body keeps dying stops asking after three restarts in ten minutes and
  raises `stuck` instead: that is yours to look at (`logs body-N`), not to loop.
- Failed moves land as `failed` with the command output — read it before
  retrying; a repeat is a NEW request, and twice the same failing move is a
  message to the operator, not a third try.
