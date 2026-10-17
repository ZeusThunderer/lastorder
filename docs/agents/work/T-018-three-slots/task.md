---
id: T-018
title: Сеть из 3 слотов в compose
stage: E3
status: draft
criticality: critical
branch: z/T-018-three-slots
created: 2026-10-16
after: [T-003, T-014, T-022]
---

# T-018. Сеть из 3 слотов в compose

## Цель
`docker compose -f docker/compose.yml up -d` поднимает `mc-server`, `hive`, `body-1..3`, `mind-1..3` в
одном мире с настройками ресурсов из T-003. Слоты описаны без копипасты. Падение или перезапуск
одного слота не задевает остальные. Требование R1 и R5 (ресурсы трёх слотов).

## Контекст
- `docker/compose.yml`: якорь `x-body`, `body-1`, `mind-1` (`network_mode: service:body-1`),
  `hive` (T-022), volumes `world`, `body-1`, `knowledge`, `run-1` (T-014).
- T-003 (`docs/agents/docker-topology.md` «Ресурсы»): тело `BODY_XMX=768M`, `mem_limit` 2g, 1,08–1,19 ГБ;
  сервер `MEMORY=1536M`, до 1,76 ГБ; прогноз — 3 слота + сервер ≈ 5,5 ГБ.
- ADR-015, ADR-016, ADR-018 (слот = аккаунт `LastOrder01..03`, py4j каждого тела — только localhost
  своего сетевого пространства; два тела в одном пространстве ломают порт 25333,
  `runtime/game/py4j_body.py:27`).
- Модели уровней: node = `openrouter/qwen/qwen3.7-flash`, smart =
  `openrouter/deepseek/deepseek-v4.1-flash` (NETWORK.md §6). В `.env.example` сейчас smart =
  `z-ai/glm-5.3-flash` — устарело.
- Docker Desktop подвисал при параллельных больших сборках (T-005 «Вне задачи»): собирать по одному.

## Объём
Можно трогать:
- `docker/compose.yml` — слоты 2 и 3, якорь `x-mind`, env и volumes слотов
- `docker/mind/Dockerfile` — если нужно
- `.env.example` — модели уровней и `SLOTS`-переменные, без ключей
- `dev/measure_resources.py` — режим трёх слотов, если нужен
- `docs/agents/docker-topology.md` — разделы «Сервисы», «Ресурсы», «Запуск»
- `dev/compose.two-slots.yml` — удалить, если T-017 его оставила
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `runtime/` — дефекты многослотовой работы записать в отчёт; блокирующий — `lo-blocked`

## Критерии приёмки
- [ ] Три пары `body-N`/`mind-N` через якоря `x-body`/`x-mind`: на слот отличаются только имя,
  `MC_USERNAME=LastOrder0N`, `SLOT`, volumes `body-N` и `run-N`; у каждого mind свой
  `network_mode: service:body-N`. Hive один, volume `knowledge` один.
- [ ] `docker compose config` валиден; секреты только через env; новых bind-mount и публикаций портов нет.
- [ ] `.env.example`: node и smart — выбранные модели; комментарий, как запустить 2 слота вместо 3
  (`docker compose up … body-1 mind-1 body-2 mind-2`).
- [ ] Холодный старт всех трёх слотов (тела по очереди, чтобы не грузить Docker Desktop): все три
  узла рождаются с разными номерами, в `alive()` три записи. Вывод в отчёте.
- [ ] Изоляция: `docker compose restart mind-2` — узлы 1 и 3 продолжают шаги (по их `log.jsonl`),
  слот 2 рождает новый узел, прежний отмечен по T-022 (`lost`) или завершён завещанием. Вывод в отчёте.
- [ ] `docker compose stop body-3` на 2 мин и старт: `mind-3` ждёт и переподключается (растущая
  пауза), не падает в цикл без пауз. Вывод в отчёте.
- [ ] `dev/measure_resources.py` на трёх слотах 10 мин: RAM и CPU каждого сервиса, сумма; укладывается
  в 24 ГБ хоста с запасом ≥ 8 ГБ. Таблица в отчёте и в `docker-topology.md`.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker compose -f docker/compose.yml config -q
docker compose -f docker/compose.yml up -d
uv run python dev/measure_resources.py --minutes 10
```

## Ограничения
- Пути bind-mount разрешает демон; новых bind-mount не добавлять.
- RCON-пароль только из env. `network_mode` каждого mind — только его тело.
- Прогон с LLM: стоимость в отчёте, ≤ $0.15.

## Заметки надзора
- 2026-10-17 · возвращена в draft: архитектура меняется на агента в харнессе (решение пользователя); задача будет переописана или снята.
