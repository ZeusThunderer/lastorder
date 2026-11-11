# T-063. Отчёт исполнителя

## Раунд 1 · 2026-11-11

### Сделано
- `docker/compose.yml` — сервис `media` (MediaMTX `bluenviron/mediamtx:1.21.3`, pinned):
  только RTSP :8554 + запись + playback :9996 (rtmp/hls/webrtc/srt/moq/api/metrics/pprof
  выключены), volume `footage`, глубина кольца `MEDIA_RETENTION` (env →
  `MTX_PATHDEFAULTS_RECORDDELETEAFTER`, default `1d` = 24ч; `MEDIA_SEGMENT` — длительность
  сегмента, для стенд-проверок). Внутренняя сеть `media-net` (internal), портов наружу нет.
- Тела: `body-1..3` добавлены в `media-net`; переключатель камеры — `BODY_CAMERA_N`
  (по умолчанию пусто = камера выключена, стенд работает как раньше); параметры потока в
  `x-body`: `BODY_CAMERA_FPS/SIZE/BITRATE` (10 / 854x480 / 1M).
- `docker/body/Dockerfile` — ffmpeg; `docker/body/entrypoint.sh` — при `BODY_CAMERA=1`:
  фиксированный Xvfb `:99` (ffmpeg должен знать номер дисплея, `xvfb-run -a` не подходит),
  ffmpeg `x11grab` → `rtsp://media:8554/<MC_USERNAME>` в цикле перезапуска (`set +e` внутри
  сабшелла: пережить падение ffmpeg — работа цикла, без этого `set -e` убивал его на первом
  же отказе — найдено на стенде).
- `docker/media/mediamtx.yml` — запись всех потоков (fmp4, сегменты 1ч, кольцо 1д),
  playback. `docker-topology.md` — раздел «Камера и media». `tests/test_body_camera.py`:
  камера выключена по умолчанию, media pinned/internal/без портов, node-N и (до T-064)
  last-order вне `media-net`, retention из env, конфиг mediamtx.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 1015 passed, 3 skipped, 2 warnings in 155.49s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
```
Стенд (изолированный проект `t063`, порт 25590, `BODY_CAMERA_1=1`, для проверки кольца
`MEDIA_SEGMENT=20s`/`MEDIA_RETENTION=2m`):
```
$ wget -qO- "http://media:9996/list?path=LastOrder01"
[{"start":"2026-11-11T00:19:28.099948Z","duration":37.625424,"url":"http://media:9996/get?…"}]
$ wget -O clip.mp4 "http://media:9996/get?path=LastOrder01&start=…&duration=8&format=mp4"
15049 bytes, head: \0\0\0 ftyp isom   # валидный mp4
```
- Обрыв: `docker restart t063-media-1` → тело осталось healthy, в логе
  `camera: ffmpeg exited (rc=224), restarting in 3 s`, поток вернулся (в /list новый сегмент).
- Кольцо: сегменты по 20с; за 45с старейший файл сместился с 00-19-47 на 00-20-39
  (удаление по `MEDIA_RETENTION=2m` работает), счёт держится ~8 файлов.
- Сёстры: из `t063_slot-1` (сеть node-1) `wget http://media:9996` → `bad address 'media'` —
  маршрута нет.
- Камера выключена: recreate body-1 без `BODY_CAMERA_1` → healthy, процессов Xvfb/ffmpeg — 0.
- MoQ-сервер (поднялся со старым конфигом) выключен: `moq: false`, после рестарта 0 упоминаний.
Не прогонял: три камеры одновременно — это T-065 (замер ресурсов); мир не переносился —
для камеры не нужно, стенд изолированный.

### Отклонения от задачи
- Сегмент записи по умолчанию 1ч при кольце 1д (в задаче не задано; вырезка идёт по /get
  из fmp4-частей, длинный сегмент не мешает). Для стенд-проверок — `MEDIA_SEGMENT`.

### Вне задачи
- `recordDeleteAfter` нельзя ставить ниже `recordSegmentDuration` — MediaMTX отказывается
  стартовать (поймано на стенде, учтено в env-переменных compose).

### Вопросы
- нет
