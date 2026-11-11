# T-064. Отчёт исполнителя

## Раунд 1 · 2026-11-11

### Сделано
- `docker/node/Dockerfile` — многостадийная сборка: стадия `base` (сёстры, как было) и `lo`
  (= base + ffmpeg) для стола Last Order. Нужен ли ffmpeg сёстрам — нет: у них нет ни
  маршрута к `media`, ни видеозадач (ADR-045), поэтому он только в варианте стола.
- `docker/compose.yml` — `last-order`: `build.target: lo`, свой образ `lastorder-node-lo:dev`
  (две цели под одним тегом перезаписывали бы друг друга), `networks: [svc-lo, media-net]`,
  журналы на чтение `hive-run:/net-logs/hive:ro` и `mind-N-run:/net-logs/mind-N:ro`,
  лимиты 1.5g / 1 cpu (харнесс ~600 MiB + кодирование одним ядром). `x-node` строит `base`
  явно (multi-stage по умолчанию строит последнюю стадию — сёстры получили бы lo).
- `agent/network/AGENTS.md` — раздел «What is around you»: media, журналы, ffmpeg,
  справка; описывают среду, без указаний. `agent/network/reference/video-tools.md` — новый,
  на английском: /list и /get (RFC3339, mp4), кольцо («записано» ≠ «хранится»), форматы
  журналов и ловушка двух часов (ISO у hive, epoch у mind), ffmpeg в контейнере,
  писаемый `/workplace/video`; пометка «знание, не цель».
- `docker-topology.md` — видео-стол в разделе «Камера и media».
- `tests/test_lo_media_access.py` — lo-вариант образа, media-net у стола, журналы :ro
  только у стола (у node-N — лишь subpath pending_wills), справка без повелений;
  обновлены `test_body_camera.py`/`test_stand_compose.py` (стол в media-net с T-064).

### Проверки
```
$ bash dev/check.sh
pytest: ok · 1019 passed, 3 skipped, 2 warnings in 137.70s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
```
Стенд (проект `t064`: media + синтетический RTSP-поток testsrc вместо Minecraft; проба
стола — `docker run` контейнера из собранного образа `lo` с той же сетью и теми же
монтированиями, что у сервиса: `compose run` блокирует исчерпание пулов подсетей docker
на хосте, см. «Вне задачи»):
```
== list:  [{"start":"2026-11-11T00:34:40.663982Z","duration":4.8,"url":…}]
== get:   /workplace/video/clip.mp4 — 28935 bytes
FFMPEG-CUT-OK; take.mp4: \0\0\0 ftyp isom
== ro:    touch /net-logs/hive/probe → Read-only file system
          touch /net-logs/mind-1/probe → Read-only file system
```
Сёстры не видят media и журналы: тесты (тома/сети) + живая проверка T-063 из slot-N.
Не прогонял: живую долгую сессию `last-order` (квота; стол поднимает оператор) — проверена
среда стола, а не его агент.

### Отклонения от задачи
- Проба стола шла `docker run` из образа `lo` с идентичными сети/томами вместо `compose run
  last-order`: хост упёрся в пулы адресов docker (см. «Вне задачи»); конфигурация сервиса
  при этом покрыта тестами.

### Вне задачи
- На хосте исчерпаны пулы подсетей docker («all predefined address pools have been fully
  subnetted»): новые compose-проекты с сетями не поднимаются целиком. Причина — остатки
  сетей стендов (t044/t048 и пр.). Чистил только свои висячие t064-сети; чистку остальных —
  оператору (`docker network prune` безопасна для прикреплённых сетей живых стендов).

### Вопросы
- нет
