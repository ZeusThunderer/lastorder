
## 2026-11-11 · сделано напрямую (ADR-047)

### Сделано
- **Сборка.** Docker Desktop обновился до Compose v5.6.1: `compose build` через bake на Windows
  просит право `fs.read` на собственный Dockerfile, печатает «additional privileges requested»
  и выходит с кодом 0, ничего не собрав. `COMPOSE_BAKE=false` не помогает,
  `BUILDX_BAKE_ENTITLEMENTS_FS=0` помогает — в docker-topology и `dev/camera_run.sh`.
- **Мир на Fabric.** Перед переключением снят свежий архив `backups/lastorder_world-20261111-135130.tar.gz`
  (121 МБ, 293 записи; `/backups/` в .gitignore). Подъём `bash docker/node/up.sh`: Fabric Loader
  0.20.1, тот же сид `-4419881977858743592`, три тела вошли в мир на своих координатах.
- **Скины.** `bash dev/apply_skin.sh` → «Applied skin changes for LastOrder01..03». Исключение
  skinrestorer при старте (`AccessDeniedException … config.json…tmp`) — запись мода поверх
  read-only bind, как в T-061.
- **Мастерская на живом GLM** (то, что T-047 оставил непроверенным). Задача оператора #87.
  Last Order сама выбрала и написала `workshop/registry/index.ts` — реестр смертей сети
  (init/add/note/stats/list, идемпотентный add, хранилище `memory/registry.json`), вызвала
  `workshop_reload`, получила его в сессии и вызвала. Лог: `workshop.check` →
  `[{"tool":"registry","from":"own","ok":true}]`, `workshop.reloaded` → `["registry"]`.
  На полку не поставила, с причиной: «данные стола, у сестёр свои песочницы». Сёстры рождаются
  с `workshop.json`, `workshop-active/`, пустыми `net-tools/` (полка пуста).
- **Сеть.** За ~50 мин: 00190, 00192, 00194 прожили 8–20 мин, умерли в игре, написали
  завещания сами (`origin: agent`), архив, следующие 00191, 00193, 00195 родились. Квота не
  кончалась.
- **Камера.** Включена на теле 2 живого стенда; первый кадр чёрный — найдена и починена
  причина: офлайн-HeadlessMC подменял LWJGL пустым, пока не видит Xvfb, а смотрит только при
  `hmc.check.xvfb=true` (байткод `LaunchOptions`/`XvfbService` HMC 2.10.0). После фикса — вид
  от первого лица, 1 Мбит/с. Цена: ≈ +0.85 ядра и +110 MiB на тело, ≈ 0.2 ГБ/ч footage
  (docs/agents/runs/2026-11-11-camera.md).

### Проверки
```
pytest: 1033 passed (после слияния очереди b), test_body_camera 7 passed, tools/check.py ok
docker logs mc-server: Loading Minecraft 1.21.11 with Fabric Loader 0.20.1; rcon seed — тот же
docker logs body-2: "You are offline but running with xvfb, not using headless mode."
                    "Backend library: LWJGL version 3.3.3-snapshot"
ffprobe /get LastOrder02: 854x480, 10 fps, bit_rate=1022530 (до фикса 13448)
```

### Не сделано / вне задачи
- Камера на теле 2 включена переменной окружения на одно пересоздание: следующий полный
  `up.sh` её снимет. Постоянно — `BODY_CAMERA_2=1` в `.env` (решение оператора).
- Переключение слота 2 дважды прерывало живую сестру 00193: она получила `fetch failed` и
  поставила оператору запрос #91 «перезапустить транспорт»; сессия сама переподключилась
  (последующие `wait_events` ok), запрос устарел.
- Сёстры раз за разом пробуют `/warp pers` («Unknown or incomplete command» на нашем сервере),
  в завещаниях и запросах #90, #88, #84, #70 просят оператора о варпе или координатах. Это
  знание из общей базы (внешний сервер), чинится в базе через Last Order, не кодом.
- Открыты старые тревоги #52–#85 (квота, пустые сессии 2026-11-08) — подтверждает оператор.
- Оркестрованный контейнер `lastorder-camera-1` (T-067, `compose.camera.yml`) поднялся сам
  по restart-политике, держит ~1.2 ГБ; не трогал.
