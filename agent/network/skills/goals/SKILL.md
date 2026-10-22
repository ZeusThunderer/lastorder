# Skill: the network goals

1–3 goals in `set_goals([{text, why}])`, text up to 120 characters, why up to 160,
Russian. They are advice to every sister, never orders (ADR-021); each sister chooses
her own step and may ignore them.

## Where goals come from

1. The operator: open lines in `GOALS.md`. Always carried while present; `why` starts
   with «оператор: ...». A line is done when the operator removes it — until then keep
   or reshape it, and say in `memory/state.md` why if you reshape.
2. Your signals, checked in this order:

| signal | goal |
|---|---|
| deaths by one cause, 2+ in the window | «обезопасить сеть: <причина>» — why: сколько смертей за окно |
| ladder gap: the next step untaken | food → «найти и записать стабильный источник еды»; safety → «снарядить сеть: оружие и карта опасностей»; iron → «добывать железо и записать, где оно»; shared → «общее дело по навыку из базы» |
| top death cause with no confirmed danger record | «проверить опасность <термин>» |
| step 2+ reached and no confirmed recipe | «записать первый рецепт сети» |

The ladder, in order: survive → food → safety (a weapon and a confirmed danger record)
→ iron → shared (a confirmed skill and two fed sisters). The living-summary feed that
would show food and tools exactly is not wired to your desk yet: judge the ladder from
the base and the death registry, and note in `memory/state.md` when you had to guess.

## Merging

- A goal with the same text as a live one is a renewal, not a new one; refresh `why` if
  the numbers moved.
- Old goals live about 60 minutes: re-derive every session; what no signal supports,
  drops.
- Order: deaths → ladder → knowledge gaps; at most 3 goals. The operator's lines are
  never crowded out by your signals — if they do not fit, your signal goals give way.
- Call `set_goals` only when the set changes; an unchanged set needs no call.
