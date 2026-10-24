# T-039. Ревью надзора

## Раунд 1 · 2026-10-24 · вердикт: accept · headless
Проверено: `git diff main...z/T-039-mvp-run-2` @ 6750497

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest: 731 passed, 3 skipped; check.py: ok.
- `uv run python dev/mvp_report.py --knowledge C:/lastorder-t039-run/knowledge --workplaces …-1
  --workplaces …-2 --workplaces …-3 --run C:/lastorder-t039-run/run --to 2026-10-24T03:04:01Z
  --window 2h` — FAIL; inheritance 7/12 (58%), wills 0 без will.md / 11 аварийных (92%),
  sessions 6 проблем, cost $0.05979/ч ($0.11958), LO принято 15 — совпадает с отчётом прогона.
- Корневая причина таймаутов воли подтверждена: ошибки hive «missing field: cause» / «expected ---
  frontmatter» в hermes-*-messages.jsonl (70 вхождений); `agent/last_order/skills/write-will/SKILL.md:20,31`
  — «plain text, no markdown», `submit_will(text)`.
- grep `sk-…` / `Bearer …` по `docs/agents/` — совпадений нет.
- Стенд не поднимался: прогон 2 ч не повторяется, проверка — по артефактам `C:/lastorder-t039-run/`.

### Критерии приёмки
- [x] Токены `init`/`issue`, вывод только имена — раздел «Окно» отчёта прогона.
- [x] Стенд без `NODE_DEV_OPEN`, команда дословно, шлюз и LO всё окно — снимки ps и 18 сессий LO
  01:05–03:01 по events [источник: отчёт; события в артефактах есть].
- [x] 3 слота × 2 ч, отчёт и таблица сравнения по 8 пунктам — `docs/agents/runs/2026-10-24-mvp-2.md`.
- [x] Вызовы MCP body/hive по state.db — body 857, hive 262.
- [x] Деньги ≤ $0.80 ($0.11958), квота 2382 кр/ч.
- [x] FAIL → причина и предлагаемая задача по обоим пунктам (3 и 5).

### Находки
- R1-1 · minor · `docs/agents/runs/2026-10-24-mvp-2.md:83` · навык write-will велит остановиться
  после двух неудачных `submit_will` (SKILL.md:34), а агенты ретраили до таймаута 900 с; это второй
  дефект инструкции (правило не исполнено), в разборе не назван · учесть в задаче на write-will.
- R1-2 · minor · `docs/agents/runs/2026-10-24-mvp-2.md:267` · фраза оборвана: «…либо дать шаблон
  с ними в волевом промпте, либо);» · дописать или убрать «либо».
- R1-3 · minor · `docs/agents/runs/2026-10-24-mvp-2.md:69` · том `gateway-db` сброшен исполнителем
  без согласования; не база сети и не мир, задокументировано — принимается, впредь сброс томов
  стенда — заявкой надзору.

### Чек-лист проекта
Без замечаний. Код вне `dev/mvp_report.py` (перенос комментария, R1-1 T-032) не тронут; `.env`
изменён только `stand_keys.py init/issue` по решению оператора.

Предложенные задачи (write-will контракт, защита первых минут жизни, lift LO-ключей в
`docker/node/up.sh`, `_reference_kind`) нарезает надзор отдельно через `lo-plan`.
