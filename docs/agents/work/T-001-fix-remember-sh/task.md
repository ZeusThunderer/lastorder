---
id: T-001
title: Починить agent/scripts/remember.sh
stage: —
status: done
criticality: normal
branch: z/T-001-fix-remember-sh
created: 2026-10-16
---

# T-001. Починить agent/scripts/remember.sh

## Цель
`remember.sh` снова дописывает событие в `agent/$AGENT_NAME/memory/entities/<entity>.md`.
Сейчас встроенный Python падает с SyntaxError на любом запуске. Задача заодно служит пилотом
протокола двух агентов.

## Контекст
- `agent/scripts/remember.sh:26-30`: в строковых литералах Python буквальные переводы строк вместо `\n`.
  Вероятно, escape-последовательности раскрылись при коммите `4238730` (`aw-fix:`).
- ADR-002: правки upstream aw — отдельным коммитом `aw-fix:`.
- Протокол: `docs/agents/coop-protocol.md`.

## Объём
Можно трогать:
- `agent/scripts/remember.sh`
- `tests/test_aw_scripts.py` (новый)
- файлы задачи в `docs/agents/work/T-001-fix-remember-sh/`

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- остальные скрипты в `agent/scripts/` — если в них та же ошибка, записать в «Вне задачи»
- реальную память персонажей в `agent/*/memory/`: тест работает во временной копии

## Критерии приёмки
- [ ] `AGENT_NAME=vesper bash agent/scripts/remember.sh <entity> "<text>"` в копии репо завершается с кодом 0
  и пишет строку `- YYYY-MM-DD — <text>` сразу под `## Events`.
- [ ] Это работает и для нового файла сущности, и для существующего файла без `## Events`.
- [ ] Тест `tests/test_aw_scripts.py` проверяет оба случая. Тест запускает скрипт через `bash` во временном
  каталоге (`tmp_path`) с копией `agent/scripts/` и минимальным `agent/<name>/PERSONA.md`. Без `bash` в PATH
  тест пропускается (`pytest.skip`).
- [ ] Исправление в `remember.sh` — отдельный коммит с префиксом `aw-fix:`.

## Проверки
```
uv run pytest
python tools/check.py
```

## Ограничения
- Скрипты aw запускаются из корня; на Windows — Git Bash. `AGENT_NAME` обязателен.
- `.gitattributes` требует LF для `*.sh` и `*.py` — не сломать окончания строк.

## Заметки надзора
