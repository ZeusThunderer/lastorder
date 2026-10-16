# Docker-топология MVP

Решение: всё работает в контейнерах (ADR-015, записать в R2). Это заменяет ADR-007 «Python на Windows».

## Сервисы

| Сервис | Образ | Что делает |
|---|---|---|
| `mc-server` | `itzg/minecraft-server` | 1.21.11, `ONLINE_MODE=FALSE`, survival, `DIFFICULTY=easy`, обычный мир, RCON. Мир на volume `world` — один постоянный мир на всех |
| `body-N` (N=1..3) | свой `docker/body` | Java 21 + HeadlessMC + Fabric 1.21.11 + fabric-api + jar uc. Ник `LastOrder0N`, `autoConnectServer=mc-server` |
| `mind-N` | свой `docker/mind` | Python 3.12 + uv. `network_mode: service:body-N`. Цикл узла, py4j-клиент и callback-сервер на localhost |
| `hive` | тот же `docker/mind` | FastAPI: реестр узлов (сквозной номер), общая база знаний (единственный писатель, git), каталог, сводки, ротация по сроку жизни |
| `llm` | vLLM (профиль `local`) | Локальная модель на 5070 Ti, OpenAI-совместимый API. Облако подключается через переменные LiteLLM без этого сервиса |

Volumes: `world`, `knowledge` (база сети под git), `body-N` (`.minecraft`, игровая папка, настройки uc).

## Слот и узел
- Слот — это пара `body-N` + `mind-N`, она живёт постоянно. Имя в Minecraft — имя слота: `LastOrder01`.
- Узел живёт внутри `mind-N` и называется «Мисака 00007» (сквозной номер из hive, формат в конфиге).
- Смерть узла:
  1. завещание;
  2. `hive.retire`;
  3. респаун тела;
  4. `hive.birth` → следующий номер с новыми чертами.
  Контейнеры не перезапускаются, доступ к docker-сокету не нужен.
- Срок жизни без смерти — `MAX_LIFE_MIN=45`, потом RCON `/kill LastOrder0N`. Застревание тоже ведёт к принудительной смерти.

## Ресурсы
Замер — `dev/measure_resources.py` (docker stats, среднее и максимум, T-003). Хост: Docker Desktop
WSL2, 24 ГБ / 18 CPU. До T-003 (замер E1 и baseline T-003, сервер MEMORY=2G VIEW_DISTANCE=8;
тело Xmx2G/Xms512M, rd4/sd5/fps20): сервер 2,2 ГБ, тело 1,6 ГБ в покое, CPU тела ~45%.

После T-003: сервер `MEMORY=1536M`, `VIEW_DISTANCE=6`, `SIMULATION_DISTANCE=5`; тело `BODY_XMX=1G`,
`BODY_XMS=256M`, options.txt переписывается на каждом старте (`renderDistance:2`,
`simulationDistance:5` — минимум ванили, ниже клиент молча ставит 12, `maxFps:10`); `mem_limit`
тела 2g (было 3g). Покой после входа: сервер ~1,5 ГБ (после долгой эксплорации подползает к
1,76 ГБ — куча не отдаётся, потолок задан Xmx), тело ~1,2 ГБ (после тяжёлой сессии ~1,4 ГБ).
Задача `@get log 3` на свежей местности: сервер до 1,7 ГБ, тело до 1,4 ГБ, CPU тела avg
40–60 % (пики 200–470 % приходятся на генерацию чанков сервером). Три слота плюс сервер —
≈ 7 ГБ в худшем наблюдении: на 24 ГБ остаётся запас под hive, мозги и jev. После всех
изменений `tools/body_probe.py --kill` проходит целиком (T-003, 2026-10-16).

## Запуск (E1)
- `docker compose -f docker/compose.yml up -d mc-server body-1`, затем `up mind-1` — проба `tools/body_probe.py`.
- Первый старт тела качает около 540 МБ ассетов и библиотек, это занимает 4 минуты. Всё ложится в volume
  `body-N` (`/data/.minecraft`), поэтому повторный старт до входа в мир занимает около 30 с. Java берёт
  `user.home` из passwd, а не из `$HOME`, поэтому в entrypoint стоят `-Duser.home=/data` и `hmc.mcdir`.
- Без GL (`-lwjgl`) клиент работает. Ошибки OpenAL, Realms и 401 на ключ профиля при старте — норма.

## Риски
- Запасной рендер — тот же образ с `BODY_RENDER=xvfb`, 854×480. Для MVP не нужен.
- Simple Voice Chat: если он стоит, без аудиоустройства падает при входе в мир. В тело его не ставим.
- Bind-mount в compose разрешает демон, а не шелл. Запускать compose с хоста из корня репозитория.
