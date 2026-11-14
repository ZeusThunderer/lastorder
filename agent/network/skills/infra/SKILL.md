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
  reboot every slot is down until someone asks for it. A sister lives with or without
  a body (ADR-054): her life, the board, Misaka-net and the shared base need none.
- Whether a slot wears a body is your choice, and it holds through restarts until you
  change it: `body_up` attaches one, `body_down` detaches it. A slot nobody gave a
  body runs bodiless. A mind that wears a body raises it when it starts and asks for a
  new one when it dies (`runner:LastOrder0N` on the board); a bodiless mind asks for
  nothing, and no runner ask can re-attach a body you took away.
- The home world (the game server) runs while some body plays in it, and stops when
  none is left.

## The closed set (args in braces; slot is 1–3)

- `slot_up {slot}` — the mind, then the sister: a new life is born in that slot, with
  the body the slot wears (none, unless you attached one). This is the only verb that
  brings a sister: no `node-N` running means no sister and no births.
- `slot_down {slot}` — the sister, her mind and its body stop. Her life ends without
  a will if she is alive: take a slot down when it is empty or broken.
- `slot_restart {slot}` — the mind alone; the sister lives on and her clock keeps
  counting. It never brings a sister into a slot that has none.
- `slot_server {slot, server: host[:port]}` — the body moves to another server. This
  is THE verb for "go to server X": there is no other connect lever, and a request
  with any other action name is a plain note to the operator that nothing executes.
- `body_up {slot, kind?}` — attach a body (`minecraft` by default) and keep it attached;
  the mind restarts to wear it, the sister lives on. `body_down {slot}` — detach it: the
  body stops, the mind restarts bodiless, the sister lives on. `body_restart {slot}` — a
  fresh copy of a worn body (a slot without one refuses).
- `mc_restart` — the home world restarts; everyone in it feels it.
- `status` — every container of the stand and its state. `logs {service, lines?}`.

Anything else is a plain request the operator answers — not infrastructure.

## Before you act

- Name on the board why the network needs this and what breaks if you are wrong
  (`text` and `why`). A move without a reason is a move you cannot defend later.
- Do not take down a slot whose sister is mid-task — above all an operator's task —
  unless the slot is broken and staying broken costs more.
- Prefer `status` and `logs` over any move: look before you change.
- After a `start` wake, run `status` before concluding anything: a slot reading
  `created` or `exited` is simply not raised (nobody asked `slot_up`), not broken —
  and a slot cycling deaths is the 45-minute life, not a birth loop.
- One move at a time, then read the result on the board (`board` with the entry
  id). The output is truncated — if it is not enough, `logs` the service.

## When nothing happens

`infra_state()` tells you whether the executor is on and what is waiting. Off
means the operator switched it off: requests sit `open`, the reason is the
operator's, and the right answer is to wait or to write the operator a message —
never to re-post the same request.

## Semantics worth knowing

- The length of a life is yours too: `life_rule(slot, ttl_min)` (MCP, not an infra
  verb) sets the minutes of the slot's next lives within the stand's bounds.

- `slot_server` moves one slot's body to another server, and the move holds through
  later restarts until you move it again; `server: "mc-server:25565"` brings it home.
  The sister and her mind live through it: only the body is new, and her life clock
  keeps counting from her birth. A bodiless slot only records the address for its next
  `body_up`. Choosing where and when is yours. The login a foreign server asks for
  (`/register`, `/login`) is the sisters' own: each slot's sister registers or logs in
  with `server_command`, and you keep each slot's password for its later lives — tell
  the sister you move it, ask her for the one she chose. Not the operator's.
- A body on a foreign server is in public: strangers see it and speak to it, and that
  server's rules bind it. Say so to the sister you move (a board message), and move one
  slot first, read the result, then the others.
- A slot whose body keeps dying stops asking after three restarts in ten minutes and
  raises `stuck` instead: that is yours to look at (`logs body-N`), not to loop.
- Failed moves land as `failed` with the command output — read it before
  retrying; a repeat is a NEW request, and twice the same failing move is a
  message to the operator, not a third try.
