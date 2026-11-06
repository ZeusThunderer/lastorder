# T-053. Отчёт исполнителя

## Раунд 1 · 2026-11-06
Коммиты: c5b1aa9

### Сделано
- Как uc отправляет команду [источник: unionclef/, только чтение]: `Py4jEntryPoint.ChatMessage`
  (Py4jEntryPoint.java:612-619) → `MessageSender.enqueueChat` → `sendChatInstant`
  (MessageSender.java:104-116): строка с `/` авто-детектируется, слэш срезается, уходит
  `PlayerVer.sendChatCommand` (PlayerVer.java:20-25) → `networkHandler.sendChatCommand` —
  командный пакет, НЕ чат. Прогон на `mc-server` [проверено: 2026-11-06 16:38 UTC, одноразовое
  тело ProbeT053 в сети стенда]: `/me probe-t053-alpha` и `/help` отправлены рычагом chat;
  в логе сервера — `[Not Secure] * ProbeT053 probe-t053-alpha` (эффект `/me`), сырых строк
  `/me …`/`/help` в чате нет. Значит `/login <пароль>` публикой стать не может.
- `server_command` в MCP тела (`runtime/mind/mcp_body.py`): валидатор `check_server_command` —
  список запрета (общий `MC_COMMANDS_DENY` + слот `MC_COMMANDS_DENY_SLOT`, `parse_deny`),
  дефолт входа/пароля (ADR-043) неотменяем даже явным конфигом; имя команды без учёта регистра
  и с префиксом плагина (`/authme:login` = `login`); отказ на перевод строки, `;`, `§`, длину;
  частота — как у say (4 с). say по-прежнему без `/`, но подсказывает инструмент.
- Вход: `runtime/mind/login.py` (`SessionLogin`) — при `MC_LOGIN_PASSWORD` (в compose — значение
  слота `MC_LOGIN_PASSWORD_N`) `mind-N` шлёт `/login` при входе в мир (и `/register`, если сервер
  просит; повтор — с кулдауном 30 с). Пароль маскируется в единой двери чата
  (`Executor.chat_entry` → `***`) — логи, события, `observe()`, `chat.line`. Команда идёт напрямую
  рычагом тела: список запрета инструменту агента её не касается.
- Docker: `up.sh` поднимает `MC_COMMANDS_DENY`, `MC_COMMANDS_DENY_N`, `MC_LOGIN_PASSWORD_N`;
  compose даёт пароль ТОЛЬКО `mind-N` [проверено: `docker compose config` —
  `MC_LOGIN_PASSWORD_2=testpass` → `mind-2: MC_LOGIN_PASSWORD: testpass`, у `node-N` переменной
  нет]. `.env.example` — имена и правила; `docker-topology.md` — рецепт оператора.
- AGENTS.md сестры: `server_command` в списке инструментов (что можно/чем ограничен), без правил
  про конкретные команды; «login — не твой, среда входит сама».
- Тесты (tests/test_mcp_body.py): валидатор (принятие/отказ/причина), parse_deny, стенд-список
  поверх дефолта, гварды через MCP (отказ/успех/rate, тело видит только прошедшее), SessionLogin
  (вход, register, кулдаун, rejoin), маскировка в chat_entry, логин на тике с фейковым телом.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 914 passed, 3 skipped, 2 warnings in 98.33s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests -k "server_command or login"
65 passed, 1 deselected... (в составе полного прогона выше)
$ docker compose -f docker/compose.yml config | grep MC_LOGIN_PASSWORD   # только mind-N
```

### Отклонения от задачи
- `dev/stand_keys.py` (вне «Можно трогать»): добавлены имена `MC_LOGIN_PASSWORD_1..3` — реестр
  ключей, `test_key_lists_cover_the_stand` требует знать каждое имя с KEY|PASSWORD. Правка
  реестра, не логики.
- Проба на живом теле: тела стенда лежали (py4j-шлюзы uc умерли во всех трёх, см. «Вне задачи»),
  поэтому поднял одноразовый контейнер `ProbeT053` (образ body, сеть default, `mc-server:25565`),
  проба по нему; зонд снят.

### Вне задачи
- py4j-шлюз uc умирает через часы работы во ВСЕХ телах стенда (main и t048): слушателя 25333 нет,
  умы перезапускаются. Восстановление сейчас — только пересоздание тела. body-2 после рестарта
  не подключается к серверу (застрял в меню, Realms-ошибки), mind-2 падает при остановке с
  `AttributeError: 'NoneType' object has no attribute 'join'` (py4j callback server не стартовал).
  Это блокер живых прогонов вообще (T-058 упрётся) — нужна отдельная задача надзору.
- `docker restart` тела не гарантирует переподключения к серверу (body-2) — похоже, связано с
  тем же износом; свежий контейнер подключается сразу (ProbeT053).

### Вопросы
- нет
