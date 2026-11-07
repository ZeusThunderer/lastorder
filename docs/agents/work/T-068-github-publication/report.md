# T-068. Отчёт (надзор, `owner: supervisor`)

## Раунд 1 · 2026-11-07
Коммиты: 1c2c71b и этот.

### Сделано
- Аудит — `audit.md`: шаблонный скан 769 коммитов и дерева, ключей нет; личные данные; спорные
  файлы с решениями оператора; лицензии.
- `README.md` на английском: что это, что видно, статус с цифрами прогонов (6/8 MVP, $0.06/ч,
  11/12 аварийных завещаний), схема, карта репозитория, рецепт стенда, авторство.
- `docs/architecture/OVERVIEW.md`: контейнеры и сети, контуры, жизнь сестры, база и Last Order,
  шлюз и бюджет, восприятие, как строится проект. Схем Mermaid — 5 (1 в README, 4 в OVERVIEW).
- README шаблона aw — в `docs/agent-workplace.md` с пометкой и ссылкой на upstream.

Сверка с кодом: сервисы, сети, порты — `docker/compose.yml:164-652`; порядок смерти и завещания —
`runtime/node/runner.py:1-20`; срок жизни — `runtime/hive/lifecycle.py:47`; потолки шлюза —
`dev/gateway_keys.py:66-72`; ключ Z.ai у сестры — `runtime/node/workplace.py` (`zai-key` 0400);
HAPI — `docker/hapi/Dockerfile:1-5` (npm `@twsxtd/hapi`, AGPL-3.0); Pi — `package.json` в образе
`lastorder-node:dev` (`earendil-works/pi`, MIT). Исправлено при сверке: в задаче гранты названы
ADR-046, на деле ADR-041; T-047 и T-057 в тексте помечены «not built yet».

### Проверки
```
$ grep -n -i -E "delve|leverage|seamless|robust|cutting-edge|elevate|unlock|it's worth noting|in today's" README.md docs/architecture/OVERVIEW.md
(пусто; расширенный список и тире — тоже пусто)
$ ссылки: 19 относительных ссылок в README, OVERVIEW, agent-workplace.md — все ok
$ uv run python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
```
Не прогонял: рендер Mermaid — mermaid-cli на хосте нет, предпросмотр GitHub — после push.
Сканер gitleaks не прогонялся (не установлен).

### Отклонения от задачи
- Рендер схем не проверен (см. выше).
- Удаление спорных файлов и замена ника — после merge, переписыванием истории с согласия оператора.
