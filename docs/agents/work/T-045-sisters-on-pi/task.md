---
id: T-045
title: Сёстры на Pi — адаптер раннера, жизнь и воля в Pi, запасной OpenCode с тревогой
stage: I
status: ready
criticality: critical
after: [T-042, T-044]
branch: z/T-045-sisters-on-pi
created: 2026-10-26
---

# T-045. Сёстры на Pi — адаптер раннера, жизнь и воля в Pi, запасной OpenCode с тревогой

## Цель
Сестра живёт в сессии Pi на Z.ai Coding Plan: жизнь, волевая сессия, архив — как сейчас, но
оператор видит её в консоли и может говорить с ней посреди жизни. При отказе Z.ai — OpenCode
через шлюз и тревога оператору. Сестра знает, что оператор есть, и может написать ему первой.

## Контекст
- ADR-041. Адаптеры: `runtime/node/adapters.py` (`OpenCodeAdapter`, `HermesAdapter` — образец,
  `:166–:250`); переключение харнесса и отказ — `NODE_HARNESS`, `NODE_HARNESS_FAILOVER` в
  `runtime/node/runner.py`; конфиг рабочего места — `runtime/node/workplace.py` (Pi — T-041).
- Доставка событий, голоса и мысли — `misaka-link` (T-042), консоль — T-044.
- Воля: контракт T-040; прокси волевой сессии — `runtime/node/willproxy.py`.
- Инструкции сестры: `agent/last_order/AGENTS.md` и навыки в `agent/last_order/skills/`.

## Объём
Можно трогать:
- `runtime/node/adapters.py`, `runtime/node/runner.py`, `runtime/node/workplace.py`,
  `runtime/node/config.py`
- `docker/compose.yml` — env `node-N` (по умолчанию `NODE_HARNESS=pi`)
- `agent/last_order/AGENTS.md` — коротко: голос оператора — наблюдение, писать ему можно первой,
  по своему усмотрению (talkativeness), в пределах потолка
- `tests/test_node_runner.py` и соседние
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `agent/last_order/prompt_template.md`, `runtime/lastorder/persona.py` — ядро
- `runtime/perception/`

## Критерии приёмки
- [ ] `PiAdapter`: команда, разбор вывода RPC, id сессии, учёт расхода — тесты argv и разбора по
  образцу Hermes.
- [ ] Жизнь и волевая сессия идут в Pi; аварийная воля при таймауте — как прежде — тест.
- [ ] Отказ Z.ai → OpenCode через шлюз + тревога в ленте оператора — тест.
- [ ] Hermes и OpenCode остаются выбираемыми через `NODE_HARNESS` — тест.
- [ ] Прогон на стенде, 3 слота, 30 мин: в консоли видны все три сестры; оператор (исполнитель)
  один раз говорит голосом и один раз мыслью с живой сестрой посреди жизни; хотя бы одна воля
  доходит от агента (T-040). Выводы, расход квоты и денег — в отчёте.

## Проверки
```
bash dev/check.sh
```
Стенд — полный, через `dev/wait_for.sh`, одно окно 30 мин.

## Ограничения
- Смерти без завещания не бывает.
- Мир жёсткий: защиты первых минут нет (ADR-041).
- Голос оператора — наблюдение (ADR-012/037); правило — в инструкции, не в коде.
- ADR-033: в песочнице только токен Z.ai; MCP тела и hive, шлюз, Z.ai — единственные выходы.
- Служебные строки uc агенту не показываются.

## Заметки надзора
