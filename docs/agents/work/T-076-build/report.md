# T-076. Строительство по схеме (defence-plan W3, ADR-057). Отчёт

status: done

## 2026-11-15
Коммиты: e0c8671 (инструмент), следующий — исправление счёта и проба.

### Сделано
- `runtime/mind/build.py`: схема кубоидами в формате jevcraft (`size`, `palette` ≤ 16, `regions`
  по порядку, `air` вырезает, `blocks` — отдельные клетки, `entrance`), валидатор — порт
  `validateSchematic` jevcraft (MIT): границы, палитра, связность с фундаментом y=0, вход.
  Только полные блоки без ориентации. Имена — plain (`oak planks`, `blk:oak_planks`).
- Тело: инструменты `build(schematic, x, y, z)` и `build_status()`; рычаг `build_blocks` —
  только через `build`. Блок палитры не в хотбаре — отказ (uc `equipBlock` при отсутствии
  нужного блока ставит тот, что в руке: `BlockPlaceHelper.java:1115`). `busy()` учитывает
  очередь стройки uc, `stop()` её чистит, вердикт C1 — по счётчикам очереди. Список клеток
  в журнал и Jev не идёт (`public_args`).
- `agent/last_order/skills/build/SKILL.md` и `library/` — 9 схем jevcraft, ступени и плиты
  заменены полными блоками, двери — проёмом (`dev/convert_jevcraft_schematics.py`).
- `dev/build_probe.py` — проба инструмента изнутри `mind-1` без сестры.

### Проверки
- `pytest` весь набор: 1206 passed, 4 skipped, 2 failed — `test_gateway_config::
  test_provider_key_reaches_only_the_gateway` и `test_stand_keys::test_key_lists_cover_the_stand`
  падают на `docker/compose.yml` сервиса `tts` (T-075), файл не трогался. `tools/check.py` — чисто.
- Стенд [проверено]: `mc-server` + `body-1` + `mind-1`, без `node-1` (квота Z.ai не тратилась).
  - пустой хотбар → отказ «not in the hotbar: oak_planks … needs: oak_planks 108»;
  - 128 досок по RCON → Tiny Oak Cottage (5×6×5, 108 клеток) у тела: ~113 с,
    106 из 108, 2 клетки возвращены (`no_face`, `timeout`), `intent.failed` с причинами;
  - повтор с тем же началом: стоящее пропущено, доложено 5 клеток, 2 остались `no_face`.

### Найдено
- Счётчик uc `alreadyFilled` растёт вместе с `placed` (считает и только что поставленные):
  первый вердикт говорил «built 187 of 108». Исправлено: построено = всего − возвращено −
  в очереди; `already` в `build_status` — только занятые на старте.
- Две дыры в стене, закрытые со всех сторон кроме внешней: строитель uc уходит на крышу и
  не видит грань; `goto` к внешней стороне не помогает — следующий `build` снова уводит тело
  на крышу. `place_block` с земли тоже не поставил, а C1 счёл его `done` (рычаг не проверяется,
  известная граница `_verdict`). Это логика uc (только чтение) — вопрос к upstream, если
  повторится на показе.
- Стенд: `.stand/servers.env` после рейда держит `MC_SERVER_1..3` на чужом сервере — тело
  ушло в лобби авторизации. Для пробы переопределено `MC_SERVER_1=mc-server:25565` из окружения,
  файл не менялся.
- `.env`: `TTS_GPU_NAME="RTX 2070"`, `TTS_DTYPE=float16` — голос на 2070, 5070 Ti под обучением.

### Не сделано
- Живой прогон с сестрой (Pi): строит ли она по просьбе игрока, читая skills/build.
- Палитра ролями и предпросмотр (defence-plan W3, R19).
