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
Замер E1 (один игрок, мир у спавна): сервер 2,2 ГБ, тело 1,5 ГБ из лимита 3 ГБ и около 30% одного ядра.
Мозг и hive вместе меньше 0,5 ГБ. На 3 слота хватает 12 ГБ из 24 ГБ. vLLM на 5070 Ti, клиенты GPU не используют.

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
