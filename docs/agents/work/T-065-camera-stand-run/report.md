
## Раунд 1 · 2026-11-11 (каркас)
- `dev/camera_run.sh` — оркестрация прогона: изолированный проект (тела под своим тегом
  `<project>-body:dev`, общий образ стенда не перестраивается), node-N не поднимаются
  (квота не тратится), замер `measure_resources.py` по окну, /list по каждому телу,
  размер footage, выгрузка `log.jsonl` умов в `.camera-run-data/`, teardown `down -v`
  (scratch-мир). Guard: проект `lastorder` отказ (общий стенд — руками оператора под lock).
- `tests/test_camera_run.py` — последовательность dry-run, база `--no-camera`, guard,
  изолированный тег. Отчёт-план: `docs/agents/runs/2026-11-11-camera.md`.

## Передача · 2026-11-11
- сделано: каркас прогона, тесты (4), отчёт-план; дальше: сам прогон.
- не прогонял: замеры (4 окна) — на хосте идёт обучение в WSL2, прогон грузит CPU/RAM
  параллельно; вопрос оператору задан (режим: пилот 4×10 мин / полный 4×30 мин / отложить),
  ответа в чате нет — отложено до окна без обучения.
- проверить/запустить (когда будет окно), из worktree T-065:
  `bash dev/camera_run.sh --minutes 30 --distance 2`
  `bash dev/camera_run.sh --minutes 30 --distance 2 --no-camera`
  `bash dev/camera_run.sh --minutes 30 --distance 6`
  `bash dev/camera_run.sh --minutes 30 --distance 8`
  затем заполнить `docs/agents/runs/2026-11-11-camera.md` и выбрать дефолты
  (`BODY_CAMERA_*` в compose, дистанция/`BODY_XMX` камеры).
- статусы: задача остаётся in_progress до замеров.
