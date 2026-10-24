---
id: T-037
title: Стенд — память node-N, zai-egress в зависимостях, без автозапуска при старте Docker
stage: E3
status: in_progress
criticality: normal
branch: z/T-037-stand-compose
created: 2026-10-23
---

# T-037. Стенд — память node-N, zai-egress в зависимостях, без автозапуска при старте Docker

## Цель
Три дефекта стенда из T-032 и T-034 закрыты в `docker/compose.yml`: у песочницы узла есть запас
памяти, `node-N` не стартует без `zai-egress`, стенд не поднимается сам при запуске Docker Desktop.

## Контекст
- Память: node-1 — 1005 MiB из 1 GiB к 60-й минуте, node-2 — 794, node-3 — 580; OOM не было
  (`docs/agents/runs/2026-10-23-mvp.md`, инцидент 2). Лимит 1g — якорь `x-node` (`docker/compose.yml:69`) и NETWORK.md §9.
- П-1 из T-034 (`docs/agents/work/T-034-hermes-run/report.md:72`): без `zai-egress` у харнесса нет пути
  к api.z.ai — сессия падает; сервис не в `depends_on` у `node-N`.
- П-4 из T-034: `restart: unless-stopped` поднимает стенд при старте Docker Desktop без надзора и
  тратит квоту (утренний инцидент, узел 77).

## Объём
Можно трогать:
- `docker/compose.yml`
- `docs/architecture/NETWORK.md` — §9, только лимиты и политика перезапуска
- тесты compose, если есть (`tests/` — найти по `compose.yml`)
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- сети и egress песочницы (ADR-033): правится только `depends_on`, не `networks`
- `docker/node/up.sh`, `docker/node/entrypoint.sh`, `runtime/`

## Критерии приёмки
- [ ] `mem_limit` у `node-N` поднят до 1.25–1.5g; выбор обоснован пиком T-032 и общим бюджетом хоста
  из NETWORK.md §9 (обновить таблицу).
- [ ] `node-N` зависит от `zai-egress`: `docker compose -f docker/compose.yml up -d node-1` поднимает его.
  Вывод `docker compose ps` — в report.md.
- [ ] Политика перезапуска сервисов стенда такова, что после рестарта демона Docker стенд не
  поднимается сам, а падение процесса в работающем стенде по-прежнему перезапускается (если это
  достижимо одной политикой — объяснить выбор по документации Docker со ссылкой; если нет —
  зафиксировать компромисс). Проверено `docker inspect` и рестартом Docker Desktop либо объяснено,
  почему рестарт не прогонялся.
- [ ] `docker compose -f docker/compose.yml config` без ошибок.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker compose -f docker/compose.yml config --quiet
```

## Ограничения
- Пути bind-mount разрешает демон, а не шелл (CLAUDE.md, «Ловушки»).
- Стенд с харнессом не запускать: проверка без сессий агента, квота не тратится.
- Параллельно с T-035, T-036, T-038: общих путей нет.

## Заметки надзора
- 2026-10-23 · поставлена по разбору T-032 и предложениям П-1, П-4 из T-034.
