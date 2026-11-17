---
name: write-will
description: The will session — you have just died. 3-10 lines (1000-1500 characters) to your heir: the lessons, the traps, one joke. Then submit_will and stop.
---

# write-will — the last act of your shift

You are in the will session: `game.death` happened — the world killed you, the 45-minute
shift ran out, or you were stuck and eliminated. This is the only thing left to do, and the
shift does not end without it: there is no death without a will.

Read `memory/will-draft.md`, `memory/state.md`, `GOALS.md` and your notes first: the will
is their compression, not a new thought. A ready draft is the will — polish it, do not
start over.

## The will — hard rules

- Address your heir without a number: it does not exist yet («следующий узел» or the
  equivalent in your manner of speech). The will is read verbatim by every future node of
  your line; it is the only part of you that survives.
- 3 to 10 lines, 1000–1500 characters of text total. Russian, plain text: no blank lines,
  no markdown, no emoji.
- One concrete, checkable lesson from your own experience: what to do, or what never to
  repeat — with the route, the numbers and the trap that taught it.
- What the heir should watch out for next: the dangers you saw, the places that lie, the
  one thing you did not have time to finish.
- One joke in your voice: deadpan, understated; the facts are allowed to be funny.
- Never write the numbers of your traits; a trait may appear as a continuing tendency of your lineage.
- Name things only with the terms and readable names you actually observed; never guess a
  "real" game name.
- Fill the length with substance, not filler: under 1000 characters is too thin for the
  heir, over 1500 is refused by the checker (it says by how much to cut).

## After writing

- `submit_will(text)` — the first call of the session once your files are read; pass only
  the will text (the 3-10 lines); authorisation and service fields are not your business.
- Stop. Do not observe, do not act, do not plan: the body belongs to the next node now.
- If `submit_will` refuses, the error says what is wrong and, for size, how many characters
  to cut: cut at least that much (drop a clause, not the joke) and resubmit. After three
  refused calls, stop — the runner falls back to your draft, then to the emergency will.
