---
id: T-064
title: Last Order и видео — доступ к media, ffmpeg, журналы сети на чтение, справка об инструментах
stage: I
status: done
criticality: critical
lane: b
after: [T-063]
branch: z/T-064-lo-media-access
created: 2026-10-31
---

# T-064. Last Order и видео — доступ к media, ffmpeg, журналы сети на чтение, справка об инструментах

## Цель
У Last Order есть сырьё, чтобы самой построить видео сети: она ходит в `media` (список записей и
вырезка), режет ffmpeg в своём контейнере и читает журналы сети на чтение. В её рабочем месте лежит
справка о готовых инструментах — знание, не приказ. Режиссёра, монтажёра и инструментов сёстрам код
не пишет (ADR-045).

## Контекст
- ADR-045, docs/agents/video-toolkit.md (§2 журналы, §4 инструменты, §9).
- Контейнер `last-order` (docker/compose.yml:331-371): образ `lastorder-node:dev`, `read_only`,
  `mem_limit: 1g`, `cpus: "0.5"`, тома `hive-work:/workplace` и `knowledge:/knowledge:ro`, сеть
  `svc-lo`. Для нарезки ffmpeg нужен писаемый каталог (`/workplace` или отдельный том) и, возможно,
  больше CPU.
- Журналы: hive `run/events.jsonl` (runtime/hive/app.py:267), `log.jsonl` в `mind-N`
  (`MCP_LOG_PATH: /mind-run/log.jsonl`, docker/compose.yml:44). Смонтированы ли они к Last Order,
  проверить.
- Инструкции Last Order: `agent/network/AGENTS.md`, раздел «What is around you».
- Мастерская (T-047) — отдельная задача. Эта её не требует, но справка ей не противоречит.

## Объём
Можно трогать:
- `docker/node/Dockerfile` — ffmpeg. Образ общий с сёстрами: в отчёте записать, нужен ли он им; если
  нет, ставить только в вариант для Last Order;
- `docker/compose.yml` — `last-order`: сеть до `media`, тома журналов `:ro`, писаемый каталог для
  видео, лимиты CPU и RAM. **Критичная зона: сеть и тома.**
- `agent/network/AGENTS.md` — одна-две строки в «What is around you»: есть `media`, где журналы, где
  справка. На английском, без указаний, что делать;
- `agent/network/reference/video-tools.md` — новый, на английском: сжатые §4 и §9 из
  video-toolkit.md (готовые инструменты, адреса `media`, формат журналов, ловушки) с пометкой, что
  это знание, а не цель;
- `docs/agents/docker-topology.md`;
- `tests/` — `node-N` по-прежнему не видит `media` и журналы.

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env*`
- `agent/network/PERSONA.md`, `GOALS.md`, `INBOX.md`, `agent/last_order/prompt_template.md`
- `runtime/hive/` — базу пишет только hive, видеоинструментов в MCP hive нет

## Критерии приёмки
- [ ] Из `last-order` работают `/list` и `/get` у `media`, ffmpeg режет полученный mp4 в писаемый
  каталог — команды и вывод в отчёте.
- [ ] Журналы hive и слотов Last Order видит только на чтение; запись в них падает — проверка в отчёте.
- [ ] Сёстры (`node-N`) не видят ни `media`, ни чужих журналов — тест.
- [ ] `AGENTS.md` и `reference/video-tools.md` описывают среду, а не поведение.
- [ ] `python tools/check.py` и `bash dev/check.sh` зелёные.

## Проверки
```
bash dev/check.sh
python tools/check.py
```

## Ограничения
- Поведение — в инструкциях Last Order, и пишет их не код: в справке нет «должна», «снимай», «монтируй».
- Инструкции и память персонажей пишутся на английском.
- Внешние API (распознавание, TTS, генерация) и OBS на хосте — только гранты (ADR-041), не в этой задаче.

## Заметки надзора
