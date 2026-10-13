# Рабочее место

Состояние на 2026-10-14.

## Корень

| Путь | Что это |
|---|---|
| `agent-workplace/` (aw) | сабмодуль github.com/3ndetz/agent-workplace @ `fc5f293`. Будет влит в корень (R1) |
| `unionclef/` (uc) | сабмодуль github.com/3ndetz/unionclef @ `78c9565c` (v0.95.59+2). Fabric-мод: altoclef + tungsten |
| `NetTyan/` (nt) | сабмодуль github.com/3ndetz/NetTyan @ `b04bcb7`. Прототип другого трека курса, только справочник |
| `docs/architecture/` | ARCHITECTURE.md (контуры C0–C4, порты, шина), DECISIONS.md (ADR-001…014), ROADMAP.md, KNOWLEDGE.md (память сети) |
| `docs/character/LAST_ORDER.md` | бриф персонажа — источник истины |
| `task1.md` | домашка курса: ч.1 идея, ч.2 шаблон промпта + пример + три ответа + план по занятиям |
| `LAST_ORDER.md` | точная копия брифа — удалить (R1) |
| `cursor_ai_character_creation_course.md` | экспорт чата Cursor (949 строк): оценка aw, VLA/Jev, концепции разума (стр. 661–886). Перенести в `docs/context/` |
| `Архитектура Minecraft агента….md` | обрывок чата про имя и план. Перенести в `docs/context/` |
| `.github/modernize/` | артефакт расширения VS Code Java upgrade — в `.gitignore` |

Сокращения в документах: `aw`, `uc`, `nt`, `mca` — rmalde/minecraft-agent (PoC на Mineflayer, локально не лежит).

## Состояние сабмодулей
- `NetTyan`: грязное. Правки в `lc_clients.py`, `chat_agent.py`, `base_tools.py` и неотслеживаемая
  папка `resources/Characters/MyChar/`. Оценка — в [nettyan-reuse.md](nettyan-reuse.md).
- `unionclef`: изменён `.settings/org.eclipse.buildship.core.prefs` (IDE). Не важно.

## Окружение хоста
- Windows 10, 32 ГБ RAM, 24 потока. Docker Desktop на WSL2: 24 ГБ, 18 CPU (`~/.wslconfig`).
- GPU: RTX 5070 Ti 16 ГБ и RTX 2070 8 ГБ (по ARCHITECTURE §8).
- `ast-index` не установлен.
- Docker-образы на хосте: redis, postgres, nettyan-parakeet, fish-speech. Образа `mineswarm-mc` нет,
  исходников mineswarm тоже нет.
