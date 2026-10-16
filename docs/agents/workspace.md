# Рабочее место

Состояние на 2026-10-14.

## Корень

Корень — форк aw: merge-коммит `upstream/main` (`fc5f293`) поверх первого коммита проекта. Ветка `main`.

| Путь | Что это |
|---|---|
| `agent/`, `tools/`, `docs/ARCHITECTURE.md`, `docs/MEMORY.md`, `README.md`, `LICENSE` | из upstream aw. Правки только коммитами `aw-fix:` / `aw-ext:` |
| `agent/last_order/` | наш персонаж в формате aw |
| `runtime/`, `docker/`, `tests/` | наш код |
| `unionclef/` (uc) | сабмодуль github.com/3ndetz/unionclef @ `78c9565c` (v0.95.59+2, только документы). Fabric-мод: altoclef + tungsten |
| `NetTyan/` (nt) | сабмодуль github.com/3ndetz/NetTyan @ `b04bcb7`. Справочник. Локальная ветка `local/last-order-try` хранит пробный запуск Last Order на nt |
| `docs/architecture/` | ARCHITECTURE.md (контуры C0–C4), DECISIONS.md (ADR), ROADMAP.md, KNOWLEDGE.md (память сети) |
| `docs/character/LAST_ORDER.md` | бриф персонажа — источник истины |
| `docs/context/` | исходные переписки: экспорт чата Cursor (949 строк, концепции разума на стр. 661–886) и обрывок про имя и план |
| `task1.md` | домашка курса, сдана — не править |
| `AGENTS.md` (корень) | вход для ZCode (worker), отсылает к CLAUDE.md. Не путать с `agent/AGENTS.md` из aw |
| `docs/agents/coop-protocol.md`, `docs/agents/work/` | протокол двух агентов (ADR-019): задачи, отчёты, ревью, журнал прямых правок |
| `.claude/skills/`, `.zcode/skills/` | скилы Claude (`lo-plan`, `lo-review`, `lo-direct-edit`) и ZCode (`lo-take`, `lo-submit`, `lo-blocked`, `lo-call-supervisor`). ZCode сам ставит junction-ссылки на скилы Claude в `.zcode/skills/` — они в `.gitignore` |
| `dev/call_supervisor.sh` | headless-вызов Claude Code из ZCode для проверки плана или результата |
| `.env` | секреты, игнорируется git. Не читать |

Сокращения в документах: `aw`, `uc`, `nt`, `mca` — rmalde/minecraft-agent (PoC на Mineflayer, локально не лежит).

Конец строк: `core.autocrlf=true`, а `.gitattributes` требует LF для `*.sh`, `*.py`, Dockerfile и yml —
иначе скрипты в контейнерах ломаются.

## Окружение хоста
- Windows 10, 32 ГБ RAM, 24 потока. Docker Desktop на WSL2: 24 ГБ, 18 CPU (`~/.wslconfig`).
- GPU: RTX 5070 Ti 16 ГБ и RTX 2070 8 ГБ (по ARCHITECTURE §8).
- `ast-index` v3.56 установлен (`ast-index search|symbol|outline …`).
- Docker-образы на хосте: redis, postgres, nettyan-parakeet, fish-speech. Образа `mineswarm-mc` нет,
  исходников mineswarm тоже нет.
