# T-NNN. Ревью надзора

<!-- Проверка плана: ## План N · YYYY-MM-DD · вердикт: ok | revise [· headless], находки P<N>-<k>.
     Раунд, вызванный ZCode через dev/call_supervisor.sh, помечается в заголовке: · headless -->

## Раунд 1 · YYYY-MM-DD · вердикт: accept | changes | reject
Проверено: `git diff main...z/T-NNN-<slug>` @ <sha>

### Проверки (прогнаны надзором)
- `uv run pytest` — <N passed / что упало>
- `python tools/check.py` — <итог>
- <команды из задачи>

### Критерии приёмки
- [x] / [ ] <критерий> — <чем подтверждено>

### Находки
- R1-1 · blocker · `path:line` · <что не так> · <что ожидается>

### Чек-лист проекта
<Только отклонения от чек-листа в coop-protocol.md. «Без замечаний», если их нет.>
