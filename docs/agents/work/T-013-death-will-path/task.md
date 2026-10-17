---
id: T-013
title: Путь смерти — детектор, завещание, retire, respawn, birth
stage: E2
status: accepted
criticality: critical
branch: z/T-013-death-will-path
created: 2026-10-16
after: [T-007, T-020, T-022]
---

# T-013. Путь смерти — детектор, завещание, retire, respawn, birth

## Цель
Модуль завещаний и смены поколений (R2, R1). Любая смерть идёт по одному пути:
1. смерть в игре, по сроку 45 мин или застреванию;
2. финальная рефлексия с завещанием (`smart`);
3. завещание через hive;
4. `retire`;
5. `respawn()`;
6. `birth` следующего узла в том же слоте.

Если LLM упала, пишется аварийное завещание. Смерти без завещания нет. Цикл мозга (T-014) встраивает
модуль; здесь он проверяется отдельным сценарием.

## Контекст
- NETWORK.md §7 (порядок шагов, причины `game|ttl|stuck`, как mind определяет причину), §9 (смерть
  при недоступном hive: завещание локально, respawn, birth — после восстановления).
- KNOWLEDGE.md v2 (T-007): `will.md`, аварийное завещание, вход и выход завещания (≤3000/300
  токенов, `smart`), `birth.json`. `runtime/knowledge/schema.py` (`Will`, `WillMeta`).
- Детектор: hp = 0 в `getGameState` и ванильная строка смерти в чате; `onDeath` молчит при смерти от
  мобов, падения и лавы (CLAUDE.md «Ловушки»; подтверждено в T-003). Строка смерти приходит в
  `getRecentChat` **дважды** (T-006, «Вне задачи»).
- `Py4jBody.respawn()` (T-006); `HiveClient` — `NodeRegistry`, `Memory` (T-022);
  `runtime/lastorder/traits.py` `roll`, `runtime/lastorder/persona.py`.
- LLM: `LLMConfig.for_tier("smart")`, модель `openrouter/deepseek/deepseek-v4.1-flash` (NETWORK.md §6).
- Бриф §3: завещание 2–4 строки, один урок, одна шутка; смерть — передача смены.

## Объём
Можно трогать:
- `runtime/lastorder/death.py` (детектор), `runtime/lastorder/will.py` (вызов завещания и аварийное),
  `runtime/lastorder/lifecycle.py` (порядок шагов, birth)
- `agent/last_order/will_prompt.md` (новый: промпт завещания на английском, ответ по-русски)
- `tests/test_death.py`, `tests/test_will.py`, `tests/test_lifecycle.py` (новые)
- `dev/death_scenario.py` (новый: сценарий на стенде без цикла шага)
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `agent/last_order/prompt_template.md` — промпт шага в T-011
- `runtime/hive/`, `runtime/knowledge/` — только через `HiveClient`

## Критерии приёмки
- [ ] `DeathDetector` получает состояния и строки чата, отдаёт одно событие `game.death` на одну
  смерть: hp=0 и строка смерти схлопываются, двойная строка не даёт второй смерти, новая смерть
  возможна только после hp > 0. Тест на последовательностях из T-003/T-006 (убит зомби, `/kill`,
  падение, двойная строка).
- [ ] Причина: `ttl`, если часы mind ≥ `born_at + ttl_min`; `stuck`, если узел сам выбрал
  «устраниться» (флаг от T-021/T-014); иначе `game` с текстом ванильной строки. Тест.
- [ ] `write_will(...)`: один вызов `smart` по KNOWLEDGE v2; ответ проверяется (2–4 строки, без чисел
  черт); таймаут, исключение, пустой или битый ответ дают аварийное завещание с `emergency: true`. Тест
  на каждый из четырёх отказов с подставным клиентом.
- [ ] `Lifecycle.on_death(...)` выполняет строго: пауза C1/C2 (колбэк) → завещание → `retire` →
  `respawn()` → новые черты (`roll`) → `birth` → наследие → возобновление (колбэк). Тест проверяет
  порядок вызовов.
- [ ] Hive недоступен: `will.md` пишется в volume `run` (`run/<slot>/pending/`), respawn выполняется,
  `retire` и `birth` повторяются с растущей паузой, после восстановления завещание доходит до hive
  ровно один раз. Тест с выключаемым подставным hive.
- [ ] Ошибка `respawn()` (тело не поднялось) → повтор, затем `BodyUnavailable` наверх; завещание
  при этом уже сохранено. Тест.
- [ ] `dev/death_scenario.py` на стенде (hive + body-1 + mind-1): рождение, RCON `/kill`, завещание,
  рождение; затем `request_death(stuck)`. Итог: два `will.md` в volume `knowledge` (причины `game` и
  `stuck`), номер узла вырос на 2. Вывод и тексты завещаний в отчёте; стоимость ≤ $0.01.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker compose -f docker/compose.yml up -d mc-server hive body-1
docker compose -f docker/compose.yml run --rm mind-1 python dev/death_scenario.py
```

## Ограничения
- Смерти без завещания не бывает. Числа черт в тексте завещания запрещены (только frontmatter).
- Новых вызовов LLM нет: завещание — финальная рефлексия, уровень `smart`.
- py4j: `close()`, не `shutdown()`; авторесп без задачи не срабатывает — только `respawn()`.

## Заметки надзора
