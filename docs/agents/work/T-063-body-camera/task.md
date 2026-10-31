---
id: T-063
title: Камера тела и сервис media — экран тела потоком в MediaMTX, кольцевой буфер, вырезка по времени
stage: I
status: ready
criticality: critical
after: [T-062]
branch: z/T-063-body-camera
created: 2026-10-31
---

# T-063. Камера тела и сервис media — экран тела потоком в MediaMTX, кольцевой буфер, вырезка по времени

## Цель
Тело с включённой камерой рисует мир на Xvfb и отдаёт экран потоком в новый сервис `media` (готовый
MediaMTX). `media` пишет все потоки в кольцевой буфер и по HTTP отдаёт список записей и вырезку по
времени. По умолчанию камера выключена, и стенд работает как раньше. Поведения нет: что снимать и как
монтировать, решает Last Order (ADR-045).

## Контекст
- ADR-045, docs/agents/video-toolkit.md §9.
- Рендер уже есть: `BODY_RENDER=xvfb` → `xvfb-run -a -s "-screen 0 854x480x24"`
  (docker/body/entrypoint.sh:64-65); Xvfb и mesa в образе (docker/body/Dockerfile:13). `-a` берёт
  дисплей автоматически, а ffmpeg нужен известный номер.
- Настройки тела: `renderDistance` 2 и `maxFps` 10 (docker/body/entrypoint.sh:34-36,
  docker/compose.yml:24-27). Сервер отдаёт клиенту не больше его дистанции, а для съёмки нужно 6–8
  чанков, то есть больше памяти, чем `BODY_XMX=768M`. Значения для камеры — в env слота; замер — T-065.
- MediaMTX [источник: mediamtx.org/docs/features/record, /playback]: `record`, `recordPath`,
  `recordFormat: fmp4`, `recordSegmentDuration`, `recordDeleteAfter`; `playback: yes`,
  `playbackAddress: :9996`; `/list?path=&start=&end=`, `/get?path=&start=&duration=&format=mp4`.
  Приём от ffmpeg по RTSP на `:8554` [проверить].
- `mind-N` живёт в сетевом пространстве тела: сеть, к которой подключено тело, видит и mind.
- Сёстрам (`node-N`) `media` недоступен (ADR-033): у них только MCP тела и hive.

## Объём
Можно трогать:
- `docker/body/Dockerfile` — ffmpeg;
- `docker/body/entrypoint.sh` — `BODY_CAMERA=1`: фиксированный дисплей Xvfb, ffmpeg `x11grab` →
  `rtsp://media:8554/<MC_USERNAME>`, перезапуск ffmpeg при обрыве; размер, fps и битрейт из env;
- `docker/media/` — новый: `mediamtx.yml`;
- `docker/compose.yml` — сервис `media` (образ с закреплённой версией), volume `footage`, внутренняя
  сеть `media-net` для тел и `media`, env камеры в `x-body`. **Критичная зона: сеть.**
- `docs/agents/docker-topology.md` — раздел «Камера и media»;
- `tests/` — тест конфига: камера выключена по умолчанию, `node-N` не в `media-net`, порты `media`
  наружу не опубликованы (разве что на loopback для оператора).

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env*`
- `runtime/` — поведения камеры нет
- `agent/` — инструкции Last Order меняет T-064

## Критерии приёмки
- [ ] `BODY_CAMERA` не задан → тело стартует как раньше (`headless`), `media` не обязателен.
- [ ] `BODY_CAMERA=1` на одном слоте → поток `<MC_USERNAME>` есть в `media`, запись кусками идёт
  в `footage`; `/list` и `/get` отдают mp4 нужного отрезка — команды и вывод в отчёте.
- [ ] `recordDeleteAfter` по умолчанию `24h` и задаётся env; удаление старых кусков проверено с
  коротким сроком.
- [ ] Обрыв потока (перезапуск `media`) не роняет тело, ffmpeg переподключается.
- [ ] `node-N` не достаёт `media` — проверка из контейнера в отчёте; `media` не выходит наружу.
- [ ] `bash dev/check.sh` зелёный.

## Проверки
```
bash dev/check.sh
docker compose -f docker/compose.yml exec media wget -qO- "http://localhost:9996/list?path=LastOrder01"
```

## Ограничения
- Поведения в коде нет: ни правил «что снимать», ни режиссёра (ADR-045, ADR-032).
- Оверлей и подписи в кадр — не задача среды.
- Новый выход наружу запрещён: `media` только во внутренних сетях.
- Перед подъёмом сверить с оператором, чей стенд поднят.

## Заметки надзора
