---
id: T-006
title: Py4jBody — порт GameBody поверх моста uc
stage: E2
status: review
criticality: normal
branch: z/T-006-py4j-body
created: 2026-10-16
---

# T-006. Py4jBody — порт GameBody поверх моста uc

## Цель
`runtime/game/py4j_body.py` даёт мозгу один устойчивый объект «тело»: подключение и переподключение
к py4j, чтение состояния, рычаги, команды uc, респаун, чат и колбэки. Ловушки из E1 закрыты в одном
месте, а не в каждом вызывающем коде.

## Контекст
- Порт `GameBody` — ARCHITECTURE §5.3: `state()`, `act(lever, args)`, `stop()`, `busy()`, `server()`.
  Если T-002 к моменту работы смержена, сигнатуры брать из `runtime/ports.py`; иначе — из §5.3, и в
  отчёте отметить, что сверка с портами нужна.
- Рабочий образец всех вызовов: `tools/body_probe.py` (E1, прогон 2026-10-14).
- Ловушки (CLAUDE.md «Ловушки», `docs/agents/unionclef-bridge.md`):
  - `ExecuteCommand` без префикса `@` молча теряется;
  - `gateway.shutdown()` гасит py4j-сервер внутри uc до перезапуска клиента — только `close()`;
  - авторесп без активной задачи не срабатывает — `respawnPlayer()`;
  - `onDeath` молчит при смерти от мобов, падения, лавы.
- Callback-класс реализует `adris.altoclef.PythonCallback` (образец — `tools/body_probe.py`,
  `NetTyan/src/data_collectors/minebridge.py` только читать).

## Объём
Можно трогать:
- `runtime/game/__init__.py`, `runtime/game/py4j_body.py` (новые)
- `tests/test_py4j_body.py` (новый) — с подставным gateway, без Minecraft
- `dev/body_smoke.py` (новый) — ручная проверка на живом стенде через `Py4jBody`
- файлы задачи в `docs/agents/work/T-006-py4j-body/`

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `runtime/perception/` — перевод имён не здесь: тело отдаёт сырые данные uc
- `tools/body_probe.py` — остаётся как есть

## Критерии приёмки
- [ ] `Py4jBody.command("get log 1")` и `command("@get log 1")` оба уходят в uc как `@get log 1`. Тест.
- [ ] `close()` никогда не вызывает `shutdown()` у gateway. Тест с подставным gateway.
- [ ] Переподключение с растущей задержкой при `Py4JNetworkError`; после N неудач — исключение
  `BodyUnavailable`, бесконечного цикла без пауз нет. Тест с подставным временем.
- [ ] `state()` возвращает dataclass из `getGameState` (hp, food, pos, held, lookingAt, onGround …)
  плюс `in_game`; `dead` = hp ≤ 0. Тест на разбор образца ответа из прогона E1.
- [ ] `respawn()` вызывает `respawnPlayer()` и ждёт hp > 0 с таймаутом. Тест.
- [ ] Чат: `recent_chat(n)` и очередь колбэков `onChatMessage`/`onDeath`/`onDamage` с отметкой
  времени; служебные строки uc (`[Alto Clef]`, `[Tungsten]`) помечены флагом `service=True`, но не
  удалены — фильтрует perception.
- [ ] `dev/body_smoke.py` на живом стенде: вход, состояние, `gotoXYZ` на +5 по x, `@get log 1`,
  RCON-kill, `respawn()`; вывод в отчёте.

## Проверки
```
uv run pytest
python tools/check.py
docker compose -f docker/compose.yml up -d mc-server body-1
docker compose -f docker/compose.yml run --rm mind-1 python dev/body_smoke.py
```

## Ограничения
- uc отдаёт только сырые имена Minecraft; `Py4jBody` их не переводит и не прячет.
- RCON-пароль только из env (`RCON_PASSWORD`), не в коде.
- Никаких вызовов LLM.

## Заметки надзора
Blocked: нужен оператор — headless-надзор не может прогнать `docker compose … run --rm mind-1 python dev/body_smoke.py`
(отказ в правах), живой критерий не подтверждён надзором. Код и юнит-тесты — без blocker/major, см. review.md раунд 1.
