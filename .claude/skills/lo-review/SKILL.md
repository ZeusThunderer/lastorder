---
name: lo-review
description: Только для роли supervisor (Claude Code). Проверить задачу T-NNN, которую ZCode сдал на проверку (status review) — diff ветки, свои прогоны тестов, чек-лист проекта Last Order, вердикт в review.md, merge при accept. Не применять, если ты ZCode.
---

# lo-review — проверить сданную задачу

Если ты не Claude Code (supervisor) — остановись, этот скил не для тебя.

Протокол и чек-лист: `docs/agents/coop-protocol.md`. Шаблон: `docs/agents/work/_templates/review.md`.

## Шаги
1. Проверь, что у задачи статус `review`. Если нет — сообщи пользователю и остановись.
2. Прочитай `task.md`, последний раунд `report.md` и прошлые раунды `review.md`.
3. `git fetch` при наличии remote, затем `git log main..z/T-NNN-<slug>` и
   `git diff main...z/T-NNN-<slug>`. Прочитай каждый изменённый файл в контексте, а не только diff.
   Для `criticality: critical` — построчно.
4. Переключись на ветку (`git switch z/T-NNN-<slug>`) и прогони сам `uv run pytest`,
   `python tools/check.py` и команды из «Проверок». Отчёту не верь на слово: расхождение с отчётом —
   находка уровня blocker.
5. Пройди критерии приёмки и чек-лист проекта из протокола. Проверь, что изменения не вышли за
   «Можно трогать».
6. Ответы `disputed` из отчёта: прими или подтверди находку с доводом.
7. Допиши раунд в `review.md` (создай файл из шаблона, если его нет). Вердикт:
   - `accept` — нет blocker и major;
   - `changes` — есть хотя бы одна;
   - `reject` — неверна сама постановка: перепиши `task.md` и верни статус `ready`.
8. Если та же находка не закрыта второй раунд подряд — можно исправить самому через `lo-direct-edit`
   (основание 2). Это не обязательно: сначала подумай, не яснее ли переформулировать находку.
9. Закоммить `review.md` в ветку задачи: `T-NNN: review round N`, трейлер `Agent: claude`.
10. При `accept`:
    - `git switch main && git merge --no-ff z/T-NNN-<slug> -m "Merge T-NNN: <title>"`;
    - прогони проверки на `main`;
    - отметь пункт `prototype-plan.md`, если задача его закрывает;
    - статус `done` в `task.md` и BOARD, коммит в `main`;
    - ветку удали: `git branch -d`.
11. При `changes`: статус `changes` в `task.md` в ветке (тем же коммитом, что review) и в BOARD в `main`. Скажи пользователю одной строкой, что передать
    ZCode: «T-NNN: доработай по review раунд N».

Мерж и удаление ветки — обычная часть протокола. Push в remote — только если пользователь попросил.
