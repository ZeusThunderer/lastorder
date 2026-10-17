# T-020. Отчёт исполнителя

## Раунд 1 · 2026-10-17
Коммиты: cd598cc (код и тесты), 9d8bd7d (smoke --actions), f89d911 (smoke: догон и прицел), 93a3ff3 (bridge, отчёт)

### Сделано
- `runtime/ports.py`: `EntityInfo.pos` и `BlockInfo.pos` — `tuple[int, int, int] | None = None`;
  умолчание None, остальной код не тронут (`tests/test_ports.py`).
- `runtime/game/py4j_body.py`:
  - `entities(radius=16, limit=32)`: дефект T-006 закрыт — `getEntitiesInfo(limit)` получает
    лимит числа, фильтр `dist_m <= radius` на стороне Python;
  - `pos` у сущностей и блоков из строки `"x, y, z"` (`_parse_cell_pos`): битая строка → None,
    без исключения;
  - рычаги `mine` (`pos`/`positions` → `mineBlocks`), `attack` (→ `punkAny`), `attack_stop`
    (→ `punkStop`), `use_held`, `eat` (`selectHotbar(n)` + `useHeldItem()`), `get`
    (`@get <id> <n>`, count по умолчанию 1), `equip` (`@equip <id>`);
  - `busy()`: `hasActiveTask()` **или** `punkStatus().active`; `stop()`: `stopPathing` + `@stop`
    + `punkStop`.
- `tests/test_py4j_body.py`: тесты на каждый критерий (радиус-фильтр, лимит в uc, pos/битая
  позиция, каждый рычаг, busy от боя, stop останавливает бой). Фикстура E1_ENTITIES дополнена
  дальней коровой (40 м) для фильтра.
- `dev/body_smoke.py --actions`: новый режим (см. «Проверки»); каждый шаг печатает до/после
  (hp, food, pos, busy, инвентарь).
- `docs/agents/unionclef-bridge.md`: раздел «Управление» дополнен подразделом «Рычаги
  выживания (проверены на стенде, T-020)» с формами ответов.

### Проверки
```
$ uv run pytest
95 passed in 3.99s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.

$ docker compose -f docker/compose.yml up -d mc-server body-1   # стенд уже стоял
$ docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/body_smoke.py --actions
actions smoke passed
```
Ветка релевантных шагов стенда (полный вывод — в git-истории прогона, выжимка здесь):
- mine блока под ногами по позиции из `blocks_around`: `{'ok': True, 'queued': 1}` → блок
  исчез из мира за 2,3 с; инвентарь +1 dirt;
- `act get log 2` → 3 с, инвентарь: 2 × birch_log; `select_hotbar` → `held='minecraft:birch_log'`;
- summon cow (RCON) → `EntityInfo(kind='cow', dist_m=2.6, pos=(-4, 64, -4))`;
- `attack allow=cow` → `{'ok': True, 'allow': ['cow']}`, 10 с наблюдения: hp коровы 10.0 → 10.0;
- `attack_stop` → `{'ok': True}`, busy False;
- strike-путь: goto к корове + `lookAt` (центр хитбокса) + `mouseClick("left")` →
  «cow down, 3 entity hit(s) landed» (железный меч), корова исчезла из entities;
- `eat slot=2` (bread): `{ok: True, result: …}` — food 17 → 17, count 3 → 3: одна интеракция
  не ест хлеб. Доследование: `hold_key use 1700` → food 17 → 20, saturation 6.0, bread 3 → 2.
  Вывод в unionclef-bridge.md: полный приём пищи = selectHotbar + holdKey("use", ~1700).

### Отклонения от задачи
- **punkAny бьёт только игроков — проверено на стенде** (корова, 10 с, hp без изменений).
  Альтернативы из задачи тоже игроки по коду uc: `interactEntity`/`attackPlayer` идут через
  `getEntity` → `EntityTracker.getPlayerEntity` (Py4jEntryPoint.java:477), `@punk` —
  `PlayerArg` + `getValidPlayer` (PunkCommand.java); сам tungsten фильтрует
  `isAcceptable: instanceof PlayerEntity` (PunkPlayerTask.java:175). Поэтому `attack` оставлен
  как punkAny (для игроков, по критерию), а для мобов добавлен рычаг `strike`
  (`{"pos": [x,y,z]}` → `lookAt` + `mouseClick("left")`) — проверен: корова убита. Выбор
  описан здесь и в unionclef-bridge.md. Прицел — центр хитбокса (клетка +0.5, y+0.7): точка
  ног даёт `hit: BLOCK`; моб должен быть в reach (~3 блока) — животное уходит, нужен догон
  goto (первый прогон это поймал, smoke исправлен).
- `busy()` в момент сразу после `punkAny` читает active=False: punkAny поднимает active в
  клиентском потоке (асинхронно). Факт в bridge, поведение busy() не менял (критерий про
  учёт punkStatus, тест на подставном mc есть).
- eat по критерию (selectHotbar + useHeldItem) оставлен как есть; ограничение (одна
  интеракция ≠ приём пищи) задокументировано — менять рычаг на holdKey без ведома надзора
  не стал, это решение для T-021/C1.

### Вне задачи
- Образ `lastorder-mind:dev` на стенде был собран до T-006 (в runtime/ нет game/) — пересобран
  из ветки задачи для прогона smoke. Сам стенд (mc-server, body-1) поднимался до меня.
- В первом прогоне RCON `kill @e[type=cow]` убил 8 сущностей: в мире оставались коровы прошлых
  сессий. Не чинил (мир стенда — вне задачи).

### Вопросы
- нет

### Вызовы надзора
- нет

## Раунд 2 · 2026-10-17
Коммиты: b3899ef (eat = holdKey), 441a590 (smoke: свежий summon, тег, честный kill/meal),
5b94ead (Hunger V в smoke), 0eb475a (C-1/C-3 чекера, устойчивость smoke к перепрогонам)

### Ответ на ревью
- R2-1 · fixed 441a590 — позиция перечитывается (`body.state()`) прямо перед summon,
  после `@get log 2`; в 0eb475a корова дополнительно призывается на клетку самого тела
  (feet+3 роняло её в старые туннели). Стенд: корова видна в 1,4–2,8 м от тела.
- R2-2 · fixed 441a590 — бой гейтен на видимую tagged-корову; strike требует
  ≥ 1 `hit == ENTITY` и смерть коровы по RCON-свидетелю, иначе FAIL. Ужесточено
  в 0eb475a по C-1 чекера: нечитаемая после punkAny корова — FAIL + повторный summon,
  strike выполняется всегда. Стенд: «cow down, 2 entity hit(s) landed» + «tagged cow
  removed from the world».
- R2-3 · fixed 441a590 — корова призывается с тегом `{Tags:["t020"]}`, здоровье и cleanup
  по `@e[tag=t020]`; cleanup после убийства: «No entity was found» (чужих коров не трогаем).
- R2-4 · fixed b3899ef — `eat` = `selectHotbar(slot)` + `holdKey("use", ms)`, `ms` из
  аргументов, по умолчанию 1700; `use_held` без изменений; тесты на подставном `mc`
  (последовательность вызовов и кастомный ms); строка в unionclef-bridge.md поправлена.
  Стенд: beef count −1, food 17 → 20 (5b94ead — дренаж food для проверки: Hunger V).
- R2-5 · fixed 441a590 — docstring: `python - --actions < dev/body_smoke.py`.

### Сделано
- `runtime/game/py4j_body.py::_lever_eat`: selectHotbar + holdKey("use", ms=1700) (R2-4),
  тесты `test_act_eat_selects_the_slot_then_holds_the_use_key`,
  `test_act_eat_takes_the_hold_ms_from_the_args`.
- `dev/body_smoke.py`: summon по свежей позиции на клетке тела; hop до целой земли для
  mine (прошлые прогоны перекапывают стартовую площадку — StopIteration); потолок 180 с
  на погоню; drain food через Hunger V (в 1.21.2+ шкала истощения ×10: Hunger I за 90 с
  не снижал food); проверка «eat consumed the food» (count −1 или food +).
- `docs/agents/unionclef-bridge.md`: события и образцы — отдельный подраздел (C-3).

### Проверки
```
$ uv run pytest
96 passed
$ uv run python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
$ docker compose -f docker/compose.yml up -d mc-server body-1
body-1 Running, mc-server Healthy
$ docker compose -f docker/compose.yml build mind-1   # runtime/ менялся — образ из ветки
Image lastorder-mind:dev Built
$ docker compose -f docker/compose.yml run -T --rm mind-1 python - --actions < dev/body_smoke.py
actions smoke passed
```
Ветка стенда: mine по клетке из `blocks_around` (hop до (8,63,-11), блок исчез за 2,3 с,
+1 grass block); `@get` ensured 2 logs; summon → корова в 1,4 м, hp 10.0; punkAny
10.0 → 10.0 (players-only подтверждён); strike — «cow down, 2 entity hit(s) landed»,
tagged cow dead по RCON; cleanup по тегу — никого лишнего; food 20 → 17 (Hunger V,
34 с), `eat` beef: count 2 → 1, food +3.

### Самопроверка
- Раундов `lo-checker`: 1 с находками, повторная проверка чистая. Итог: CHECK: clean.
- C-1 · fixed 0eb475a — проба hp после punkAny ассертует читаемость (потеря коровы =
  FAIL, не тихий skip); потерянная корова → повторный summon; strike выполняется всегда
  с обязательными проверками; та же дыра закрыта у hp0.
- C-2 · fixed настоящим коммитом — «Раунд 2» отчёта дописан при сдаче.
- C-3 · fixed 0eb475a — подраздел «События и Python-образцы» в unionclef-bridge.md.

### Отклонения от задачи
- Строка «Проверок» в task.md (`run --rm mind-1 python dev/body_smoke.py --actions`)
  невоспроизводима: образ mind не содержит dev/, скрипт идёт через stdin
  (`run -T --rm mind-1 python - --actions < dev/body_smoke.py`) — как в docstring smoke
  после R2-5. task.md не правлю (файл надзора), рабочий вызов показан выше.
- Отчёт раунда 2 пишется при сдаче, а не в момент `implemented` (по указанию
  диспетчера): самопроверка раундом не считается.

### Вне задачи
- В 1.21.2+ шкала истощения ×10 (40 на единицу food): Hunger I за 90 с не снижал ни
  food, ни saturation. Факт стенда, использован в smoke; в bridge не вносил (не про
  uc-мост).
- Прошлые прогоны оставляют в мире перекопанный рельеф и мобов сессий (rotten_flesh
  в инвентаре — strike бил и по зомби в reach); мир стенда вне задачи, не чинил.

### Вопросы
- нет

### Вызовы надзора
- 2026-10-17 · result · 7854974 · VERDICT: changes · cost_usd: unknown (stdout-парсинг call_supervisor.sh упал на python-стабе Windows, вердикт взят из review.md)

## Раунд 3 · 2026-10-17
Коммиты: 24c289d (R3-1/R3-2, устойчивость smoke), f8eac90 (C-1 чекера: гейт еды)

### Ответ на ревью
- R3-1 · fixed 24c289d — strike целится в tagged-корову по точной серверной позиции
  (`mob_pos` → NBT-двойки, прицел y+0.7), дистанция от `body.state().pos`;
  попадание засчитывается только по падению hp tagged-коровы (RCON), не по
  `hit == ENTITY` (чужая корова под прицелом тоже отвечает ENTITY). Перед summon
  чистятся tagged-остатки красных прогонов (`kill @e[tag=t020]`). Стенд, два
  прогона подряд: «cow down, 2-3 confirmed hit(s) landed» + смерть по RCON.
- R3-2 · fixed 24c289d — исчерпание 40 попыток/дедлайна при живой корове:
  AssertionError «tagged cow alive after N tries (hp=…, … confirmed hit(s))»;
  «cow down» — только когда корова исчезла по RCON и есть ≥ 1 подтверждённое
  попадание (исчезновение без попаданий — тоже FAIL).

### Сделано
- `dev/body_smoke.py`: прицел и подтверждение ударов по RCON (R3-1), честные
  выходы цикла (R3-2), зачистка tagged-остатков перед summon.
- Устойчивость к повторным прогонам (найдено при перепрогонах, тот же класс
  дыр — тихий успех): хлеб выдаётся `item replace entity … hotbar.N` + поллинг
  (`/give` при полном хотбаре клал хлеб вне `selectHotbar`, а `[skip] eat`
  оставлял прогон зелёным); mine — до 3 попыток на свежих местах (очередь
  tungsten однажды молча потеряла клетку: ok/queued, блок цел 90 с — факт в
  bridge); дренаж голода гейтит съедобность (food < 20, Hunger V, окно 120 с),
  FAIL — лишь если есть вообще нельзя.
- `docs/agents/unionclef-bridge.md`: факт про молчаливую потерю клетки очередью
  mineBlocks и рекомендация (поллить мир, перекидывать очередь со свежего места).

### Проверки
```
$ uv run pytest
96 passed
$ uv run python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
$ docker compose -f docker/compose.yml run -T --rm mind-1 python - --actions < dev/body_smoke.py
actions smoke passed
```
Стенд после C-1 (f8eac90): mine (try 1, 3 с); punkAny 10.0 → 10.0 (players-only);
strike «cow down, 2 confirmed hit(s) landed» (19 с) + tagged cow dead по RCON;
drain 20 → 19 (40 с); eat beef: count 2 → 1, food 19 → 20, гейт живого тела
в силе. Перед этим два зелёных прогона подряд на 24c289d (второй пережил смерть
тела посреди прогона: дроп инвентаря, респаун — шаги перепроверились).

### Самопроверка
- Раундов `lo-checker`: 2 с находками (раунд 1 — C-1..C-3, раунд 2 — C-1/C-2),
  повторные проверки чистые. Итог раунда 3: CHECK: clean.
- C-1 (раунд 2 чекера, гейт еды) · fixed f8eac90 — трапеза засчитывается только
  на живом теле (`not dead and hp > 0`): дроп при смерти не является едой.
- C-2 (раунд 2 чекера, отчёт) · fixed настоящим коммитом — «Раунд 3» дописан.

### Отклонения от задачи
- нет

### Вне задачи
- При диагностике упавшего mine снял 10 блоков травы `fill … air replace
  grass_block` в квадрате (-22..-18, 62, -11..-7) — мир стенда, шрамы от прогонов
  там и так накопились; не чинил.
- В прогоне D тело погибло посреди smoke (позиция скачком, инвентарь дропнулся,
  hp 2.9 после) — smoke прошёл, шаги самодостаточны; причину (зомби/падение)
  не разбирал, мир стенда вне задачи.

### Вопросы
- нет

### Вызовы надзора
- 2026-10-17 · result · 28279ca · VERDICT: changes · cost_usd: unknown (stdout-парсинг call_supervisor.sh упал на python-стабе Windows)
