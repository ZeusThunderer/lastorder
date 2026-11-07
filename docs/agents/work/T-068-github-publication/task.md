---
id: T-068
title: Репозиторий к публикации — README и обзор архитектуры на английском, схемы, аудит секретов
stage: —
status: accepted
criticality: normal
owner: supervisor
after: []
branch: c/T-068-github-publication
created: 2026-11-07
---

# T-068. Репозиторий к публикации — README и обзор архитектуры на английском, схемы, аудит секретов

Исполнитель — надзор (Claude Code). ZCode задачу не берёт (`owner: supervisor`).
Срок — до занятия 2026-11-07.

## Цель
Человек, который впервые открыл github.com/ZeusThunderer/ai-char, за 5 минут понимает, что такое
Last Order, как устроена сеть, что работает сейчас и что нет, и как поднять стенд. Текст на
английском, без шаблонных оборотов LLM. В репозитории и его истории нет секретов и личных данных.
Саму видимость репозитория (public) переключает оператор после проверки.

## Решение оператора
2026-11-07: публичная документация пишется на английском. Это исключение из правила «документы
по-русски» (CLAUDE.md, «Стиль») только для `README.md` и `docs/architecture/OVERVIEW.md`.
Внутренние документы, ADR и задачи остаются на русском.

## Контекст
- Сейчас `README.md` — README шаблона agent-workplace (upstream), о Last Order в нём ничего нет.
- Источники фактов: docs/architecture/NETWORK.md (v2), DECISIONS.md (ADR), KNOWLEDGE.md,
  GOALS_COMMS.md, docs/agents/docker-topology.md, docs/character/LAST_ORDER.md, CLAUDE.md
  («Решения, которых нет в коде»), task3-4.md, отчёты прогонов docs/agents/runs/.
- Код для сверки: docker/compose.yml, runtime/{hive,mind,node,perception,pi}/, agent/last_order/,
  agent/sister шаблон.
- `unionclef` и `NetTyan` в индексе — ссылки на чужие репозитории (gitlink); NetTyan — справочник
  (ADR-005).

## План
1. **Аудит** до любых текстов:
   - секреты в рабочем дереве и во всей истории (gitleaks или trufflehog; если не установлены —
     оператор ставит по инструкции, запасной вариант — grep по шаблонам ключей Z.ai, LiteLLM,
     Telegram, паролей RCON);
   - `docs/context/` (исходные переписки), `task1.md`–`task3-4.md`: что публиковать, решает оператор
     по списку надзора;
   - лицензии: LICENSE upstream, атрибуция agent-workplace, unionclef, NetTyan.
2. **README.md** (английский): одна фраза о проекте; что видит зритель; статус (что работает, что
   нет, с цифрами из прогонов и ссылками); схема сети; жизненный цикл узла; карта репозитория;
   как поднять стенд; благодарности и лицензия. README шаблона aw переезжает в
   `docs/agent-workplace.md` со ссылкой на upstream.
3. **docs/architecture/OVERVIEW.md** (английский): подробно, с опорой на ADR по номерам:
   - контейнеры и границы (mc-server, body-N, mind-N, node-N, hive, llm-gateway, egress, HAPI);
   - контуры управления (C1 тактика, харнесс узла, Last Order);
   - жизнь узла: рождение, черты, срок 45 мин, смерть, завещание, аварийное завещание, архив;
   - общая база и предложения, кто что пишет;
   - песочница и секреты (ADR-033), гранты (ADR-046), шлюз и бюджет;
   - восприятие `plain` и перевод имён (ADR-037);
   - как работаем: ZCode пишет код, Claude проверяет (ADR-019, ADR-040).
4. **Схемы** — Mermaid в markdown (GitHub рендерит): топология контейнеров, жизненный цикл узла
   (state diagram), последовательность «смерть → завещание → новый узел», поток LLM-запросов через
   шлюз. Каждая схема сверена с compose и кодом, не с памятью.
5. **Вычитка стиля:** прогон по списку запрещённых оборотов (ниже) и ручная вычитка вслух.

## Стиль английского текста
Писать как инженер для инженера: коротко, конкретно, с цифрами и путями к файлам.
Запрещено: «delve», «leverage», «seamless», «robust», «cutting-edge», «powerful», «elevate»,
«unlock», «harness the power», «it's worth noting», «in today's … world», «not just X, but Y»,
«whether you're … or …», «Let's dive in», тройки прилагательных, риторические вопросы, эмодзи в
заголовках, итоговые абзацы «In summary/In conclusion», тире вместо любой пунктуации. Утверждение
без факта за ним вычёркивается. Непроверенное помечается («not yet tested on the stand»).

## Объём
Можно трогать:
- `README.md`, `docs/agent-workplace.md` (новый, перенос README aw);
- `docs/architecture/OVERVIEW.md` (новый), `docs/architecture/diagrams/` (если нужны SVG);
- `docs/agents/work/T-068-github-publication/` (аудит, отчёт).

Нельзя трогать:
- `unionclef/`, `NetTyan/`, код, ADR (только ссылки на них), файлы upstream aw из ADR-002.
- Видимость репозитория и push в `origin` до явного решения оператора.

## Критерии приёмки
- [ ] Отчёт аудита: команда сканера, вывод, найденное и что с ним сделано; история проверена тоже.
- [ ] Список спорных файлов (`docs/context/`, task*.md и др.) с решением оператора по каждому.
- [ ] `README.md` и `OVERVIEW.md` на английском; каждое утверждение о составе системы сверено с
      `docker/compose.yml` или кодом (ссылки в отчёте).
- [ ] Не меньше 4 схем Mermaid, все рендерятся (проверка в предпросмотре GitHub или mermaid-cli).
- [ ] `grep -n -i -E "delve|leverage|seamless|robust|cutting-edge|elevate|unlock|it's worth noting|in today's" README.md docs/architecture/OVERVIEW.md` — пусто.
- [ ] Все относительные ссылки в двух документах ведут на существующие файлы.
- [ ] `python tools/check.py` проходит.

## Проверки
```
python tools/check.py
grep -n -i -E "delve|leverage|seamless|robust|cutting-edge|elevate|unlock|it's worth noting|in today's" README.md docs/architecture/OVERVIEW.md
```

## Ограничения
- `NetTyan/.env*` не читать; секреты из найденного не цитировать в отчёте (только путь и тип).
- Публикация наружу — действие оператора (CLAUDE.md: в `origin` пушит только надзор и только
  `main`; видимость репозитория меняет оператор).
- Если секрет найден в истории — остановиться и спросить оператора: ротация ключа и/или
  переписывание истории — его решение.

## Заметки надзора
