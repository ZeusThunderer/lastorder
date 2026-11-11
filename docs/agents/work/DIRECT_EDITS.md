# Прямые правки надзора

Правки кода, которые Claude внёс сам, а не через ZCode. Основания 1–4 —
[coop-protocol.md, «Прямые правки Claude»](../coop-protocol.md#прямые-правки-claude). Файл только дописывается.

Формат записи:

```
## D-NNN · YYYY-MM-DD · T-NNN | —
- Основание: <1 критичная зона | 2 два раунда | 3 блокер | 4 контракт> — <конкретно>
- Файлы: <пути>
- Коммит: <sha>
- Почему не через ZCode: <одно-два предложения>
```

<!-- записи ниже -->

## D-001 · 2026-10-17 · T-007
- Основание: 1 критичная зона — аварийное завещание: при всех полях на пределе тело 249 токенов > 220,
  `emergency_will` бросал `SchemaError` на пути смерти; `_want` падал `TypeError` на YAML с int- и
  str-ключами
- Файлы: runtime/knowledge/budget.py, runtime/knowledge/schema.py, tests/test_knowledge_schema.py,
  docs/architecture/KNOWLEDGE.md (§5, лимиты полей)
- Коммит: ae6fe32 (ветка z/T-007-knowledge-will-design)
- Почему не через ZCode: правка в три константы и тест, найдена в headless-ревью раунда 2; лишний раунд
  дороже правки, а дефект — в последнем рубеже «смерти без завещания не бывает»

## D-002 · 2026-10-17 · —
- Основание: 3 блокер — `dev/merge_task.sh` звал голый `python tools/check.py`; в окружении
  headless-надзора `python` — заглушка Windows Store (exit 49), скрипт отказывал в merge при зелёных
  проверках (найдено при merge T-007)
- Файлы: dev/merge_task.sh
- Коммит: 839ca49
- Почему не через ZCode: скрипт надзора, ломает merge всех задач конвейера; правка в одно слово

## D-003 · 2026-10-17 · —
- Основание: 3 блокер — `board_set` в `dev/_common.sh` звал голый `python`; в headless-окружении это
  заглушка Windows Store, строки BOARD молча не менялись после merge T-007
- Файлы: dev/_common.sh
- Коммит: 720705a
- Почему не через ZCode: тот же дефект, что D-002, в общей функции скриптов надзора; правка в одну строку

## D-004 · 2026-10-31 · T-066
- Основание: исключение оператора, вне оснований 1–4 — ZCode упёрся в квоту Z.ai (окно 5 ч,
  1308), а квоту съели сёстры на zcode (≈125M из ≈141M токенов окна 18:04–21:06 UTC). Оператор:
  «сёстры… сожгли больше всего лимита… возьми ты тогда эту задачи в приоритете, раз глм не может»;
  на вопрос о протоколе выбрал «Me, as an exception», затем «sisters still uses zcode… Fix this too».
- Файлы: runtime/node/{adapters,workplace,config,runner,__main__}.py, docker/compose.yml,
  tests/test_node_runner.py
- Коммит: efcce7b (ветка z/T-066-sisters-pi-direct)
- Почему не через ZCode: у ZCode нет квоты до сброса; ревью — ZCode после сброса (роли наоборот).
  Тем же решением надзор делает T-056.

## D-005 · 2026-11-05 · —
- Основание: исключение по решению оператора (ни одно из 1–4) — обновить uc до последнего релиза.
  Оператор: «you can proceed, but do everything yourself for faster results».
- Файлы: docker/body/Dockerfile (UC_VERSION 0.95.66, UC_SHA256 ff23056a…7961), сабмодуль unionclef
  → v0.95.66 (a5309c8e), docs/agents/{workspace,unionclef-bridge}.md
- Коммит: 2bcf80d
- Почему не через ZCode: оператор попросил сделать самому ради скорости. Между v0.95.59 и v0.95.66
  исходники py4j/gateway не менялись, только игровые фиксы. [проверено: pytest 856 passed, check.py ok]
  Сборка образа body и смоук на стенде не прогнаны — Docker Desktop не был запущен.

## D-006 · 2026-11-07 · T-056
- Основание: 6 задача надзора — оператор отдал T-056 надзору (ADR-046, 2026-11-07; раньше — D-004)
- Файлы: dev/context_report.py, runtime/mind/mcp_body.py, runtime/hive/mcp.py (только сериализация
  ответов в `_run`), tests/test_mcp_body.py, tests/test_hive_mcp.py, docs/agents/runs/2026-11-07-context.md
- Коммит: adbfbae (ветка c/T-056-context-cut)
- Почему не через ZCode: решение оператора ускорить очередь; ZCode занят T-055. [проверено: pytest
  925 passed, check.py ok в worktree] Живой стенд не прогонялся — T-058.

## D-007 · 2026-11-07 · —
- Основание: исключение по решению оператора (ни одно из 1–6) — «you can set low effort if you think
  thats better». По отчёту T-056 повторно отправляемый thinking — 19 % входа сестры.
- Файлы: runtime/node/workplace.py (`PI_SISTER_THINKING_LEVEL`, параметр `thinking_level` у
  `render_pi_configs`), tests/test_node_runner.py
- Коммит: bc88043
- Почему не через ZCode: оператор попросил надзор. Жизнь сестры — `low`, завещание и стол Last Order —
  умолчание Pi `medium`. Ключ `defaultThinkingLevel` [проверено: docs/settings.md Pi в образе
  lastorder-node:dev]. [проверено: pytest 935 passed, check.py ok] На стенде не прогонялось — замер в T-058.

## D-008 · 2026-11-07 · T-068
- Основание: решение оператора (T-068, аудит) — вычистить из истории материалы курса, домашние
  задания, исходные скины; ник игрока в тестах → `ZeusThunderer`.
- Действие: `git filter-repo` (через `uvx`) по всем ссылкам, 773 коммита; все sha изменились.
  Файлы остались на диске оператора вне git (`.gitignore`). Резерв старой истории —
  `C:/ai-char-backup-20261107.git` (mirror), копия файлов — `C:/ai-char-private/`.
- Старые sha в документах (D-001…D-007, отчёты, ревью) переводит таблица
  `C:/ai-char-private/commit-map-20261107.txt` (колонки old, new; вне репозитория).
- Публикация: `main` в новый `origin` github.com/ZeusThunderer/lastorder.
  [проверено: в истории 0 путей из списка и 0 вхождений ника; pytest 935 passed, check.py ok]

## D-009 · 2026-11-08 · T-057
- Основание: 1 критичная зона / 5 мелочь на ревью — вывод исполнителя infra шёл на доску без фильтра
  сырых id (R1-1); переключатель исполнителя читался раз за цикл, выключение посреди пачки не
  останавливало следующие take (R1-2)
- Файлы: runtime/hive/board.py, dev/infra_bridge.py, tests/test_hive_infra.py
- Коммит: 66548fe (ветка z/T-057-infra-bridge)
- Почему не через ZCode: headless-ревью критичной задачи, правка в 4 строки кода и 2 теста; лишний
  раунд дороже. [проверено: pytest 983 passed, check.py ok]

## D-010 · 2026-11-08 · —
- Основание: 3 блокер — живой зонд квоты (`quota_exhausted`, хвост лога обёртки hapi) ловил `\b429\b`
  в метке времени строки «[21:16:00.429] [pi][event] message_update»: здоровые сессии сестёр
  убивались как `session.quota_live` и уходили в `quota.waits` (стенд 2026-11-08, съёмка T-067:
  00183 — 21:12:52, 21:16:00; 00184 — 21:20:15; в логах hapi ни одной ошибки провайдера).
- Файлы: runtime/node/adapters.py (429 после «.» или «:» — не метка квоты), tests/test_node_runner.py
- Коммит: ce0c099
- Почему не через ZCode: стенд стоит, съёмка идёт; правка в 1 строку без выбора решения.
  [проверено: pytest 984 passed, check.py ok] На стенде — после пересборки образа node.

## D-011 · 2026-11-07 · T-067
- Основание: 6 (задача надзора) с расширением объёма по решению оператора — «For the spectators,
  you can create an additional container with a higher level of detail».
- Файлы: docker/camera/{Dockerfile,entrypoint.sh,cam.sh}, docker/compose.camera.yml (оверлей,
  профиль `camera`; основной compose.yml не тронут), dev/video/{cam.sh,direct.sh}.
- Коммит: 10b74bf (+ правки в ветке c/T-067-intro-video)
- Что это: клиент Fabric без uc в режиме наблюдателя, Xvfb 1920×1080, дистанция 10, ffmpeg пишет
  видео и звук игры (PulseAudio null-sink). Агента, ключей и RCON в контейнере нет; spectate/tp
  даёт скрипт оператора через `rcon-cli` mc-server. Сеть — только default (рядом с mc-server).
- Находка для T-063: HeadlessMC в офлайне без `hmc.check.xvfb=true` вырезает рендер даже под Xvfb
  («game will start in headless mode»), то есть `BODY_RENDER=xvfb` тел, вероятно, рисует чёрное.
  [проверено: камера, чёрный кадр до флага, мир после] Тела с этим флагом не проверялись.
- [проверено: 1080p, ~15–18 к/с на 6–7 ядрах CPU (llvmpipe), звук −3.6 дБ пик на тестовом
  playsound] Тестов pytest нет: оверлей вне основного compose, тесты конфига его не читают.
