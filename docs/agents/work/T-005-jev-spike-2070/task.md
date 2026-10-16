---
id: T-005
title: Спайк FastJev на RTX 2070 в Docker
stage: E2
status: ready
criticality: critical
branch: z/T-005-jev-spike-2070
created: 2026-10-16
---

# T-005. Спайк FastJev на RTX 2070 в Docker

## Цель
Выяснить и показать замером, что локальный FastJev работает на RTX 2070 8 ГБ в docker и выбирает
вариант из короткого списка достаточно быстро для тактики C1 (ADR-023, ADR-004). Результат — сервис
`jev` в compose под профилем и отчёт с цифрами. Интеграции с мозгом в этой задаче нет.

## Контекст
- ADR-004: FastJev — открытая MIT-реализация интерфейса Jev, Qwen3.5-4B, медиана 83 мс на 3 решения
  через vLLM (README fastjev 0.2.0 на PyPI). Тогда план был 5070 Ti.
- ADR-023: `jev` — RTX 2070. ARCHITECTURE §8 предполагал «vLLM на Turing не используем» — проверить.
- Порт: ARCHITECTURE §5.3 `Decider.choose(state, options) -> (option, probs, conf)`.
- Хост: Docker Desktop на WSL2, две карты (5070 Ti 16 ГБ, 2070 8 ГБ). Карту выбирать через
  `device_ids`, не `count: all` (ARCHITECTURE §8).
- Варианты C1 — altoclef-команды и рычаги uc (ADR-003, `docs/agents/unionclef-bridge.md` «Управление»).

## Объём
Можно трогать:
- `docker/jev/` (новый: Dockerfile или готовый образ + конфиг)
- `docker/compose.yml` — новый сервис `jev` под профилем `jev`; остальные сервисы не менять
- `dev/jev_spike.py` (новый)
- `docs/agents/jev.md` (новый: как поднять, цифры, ограничения)
- `pyproject.toml` — только если нужна зависимость клиента, отдельной группой `dev`
- файлы задачи в `docs/agents/work/T-005-jev-spike-2070/`

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `runtime/` — порт и `FastJevDecider` появятся в задаче реализации C1

## Критерии приёмки
- [ ] `docker compose -f docker/compose.yml --profile jev up -d jev` поднимает сервер на 2070
  (`nvidia-smi` в отчёте показывает процесс на нужной карте); 5070 Ti не занята.
- [ ] `dev/jev_spike.py` гоняет ≥200 синтетических состояний: компактное состояние тела + 4, 8 и 16
  вариантов (тексты вариантов — команды и рычаги uc), печатает p50/p95 задержки на каждый размер
  списка, долю ответов в формате, пиковую VRAM.
- [ ] В отчёте: модель и квантование, движок (vLLM или запасной), версия, команда запуска, таблица
  задержек, VRAM. Если vLLM на Turing не работает — причина и замер запасного варианта.
- [ ] Вывод «годится / не годится для C1» относительно целей: p95 ≤ 150 мс на 8 вариантов (цель),
  ≤ 300 мс (порог из ROADMAP §0).
- [ ] `docs/agents/jev.md` — как поднять и проверить за 3 команды.

## Проверки
```
uv run pytest
python tools/check.py
docker compose -f docker/compose.yml --profile jev up -d jev
uv run python dev/jev_spike.py --n 200
```

## Ограничения
- Порты сервиса `jev` — только внутри docker-сети, без публикации на хост, если это не нужно замеру
  (тогда `127.0.0.1:` и пометка в отчёте).
- Модели скачиваются в именованный volume, не bind-mount.
- Облачный Jev не вызывать: эталон A/B — позже, с согласования (бюджет).

## Заметки надзора
