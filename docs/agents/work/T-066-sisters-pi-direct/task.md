---
id: T-066
title: Сёстры на Pi напрямую — адаптер раннера, MCP без codemode, потолок контекста сжатием
stage: I
status: review
criticality: critical
after: []
branch: z/T-066-sisters-pi-direct
created: 2026-10-31
---

# T-066. Сёстры на Pi напрямую — адаптер раннера, MCP без codemode, потолок контекста сжатием

## Цель
Сестра живёт и пишет завещание в Pi (`pi -p --mode json`) на Z.ai Coding Plan, а не в zcode.
Контекст запроса не растёт до конца жизни: Pi сжимает сессию по порогу. По умолчанию
`NODE_HARNESS=pi`; zcode, hermes и opencode остаются выбираемыми.

## Контекст
- ADR-041: основной харнесс узла — Pi. По факту стенд 2026-10-31 работал на zcode
  (`docker/compose.yml:93`, `runtime/node/config.py:80`) [проверено: логи node-1..3].
- Замер надзора 2026-10-31 (логи раннера `session.ended`, БД zcode CLI хоста): за день сёстры
  ≈205M токенов, ZCode-разработка ≈162M; в окне 18:04–21:06 UTC сёстры ≈125M из ≈141M, квота
  5 ч кончилась в 21:06. Жизнь — один `zcode -p` с `--resume` той же сессии, `contextUsed`
  растёт до 200–240k при окне zcode 1M, сжатия нет; ≈23 мин сессии — 10–16M токенов.
- Конфиги Pi уже рендерятся: `runtime/node/workplace.py:410-482` (`render_pi_configs`,
  `drop_pi_agent_dir`), в рантайме не вызываются. Pi 1.4.0 в образе (`docker/node/Dockerfile:32-39`).
- Pi 1.4.0, документация в образе (`$(npm root -g)/@earendil-works/pi-coding-agent/docs`):
  - `json.md`: первая запись `--mode json` — `{"type":"session","id":…}`; продолжение —
    `--session <id>`, каталог — `--session-dir`;
  - `mcp.md`: экспозиция MCP по умолчанию `codemode` — инструменты не объявлены модели;
    нужен `"exposure": "direct"`, `autoEnableCodemode: false`;
  - `settings.md`/`compaction.md`: `compaction.reserveTokens` (16384),
    `compaction.keepRecentTokens` (20000); сжатие при `contextTokens > contextWindow - reserveTokens`;
    `contextWindow` модели — в `models.json`.
- Образец адаптера с перерендером конфига на каждую сессию — `HermesAdapter`
  (`runtime/node/adapters.py:180-256`), воля — `set_will_mode` (`runtime/node/workplace.py:574`),
  вызов — `runtime/node/runner.py:745-748`.
- Признак квоты: `runtime/node/adapters.py:36-75`; Z.ai отвечает кодом 1308
  («Usage limit reached for 5 hour») [проверено: model_usage zcode, 2026-10-31] — в метках его нет.
- Ревью T-041 R2-3: конфиги в каталоге сестры (0700) она может подменить — перерендер перед
  каждой сессией и `--no-approve` (проектные `.pi/` из рабочего места не читаются).

## Объём
Можно трогать:
- `runtime/node/adapters.py` — `PiAdapter`, метка 1308;
- `runtime/node/workplace.py` — `render_pi_configs` (exposure, сжатие, `contextWindow`),
  Pi-ветка `set_will_mode`;
- `runtime/node/config.py`, `runtime/node/runner.py` — выбор `pi`, ключ в env процесса Pi,
  каталог сессий;
- `docker/compose.yml` — `NODE_HARNESS` по умолчанию `pi`, параметры сжатия;
- `tests/test_node_runner.py` и соседние, `docs/agents/docker-topology.md`;
- файлы задачи.

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env*`
- `agent/last_order/prompt_template.md` — ядро
- `runtime/perception/`, `runtime/hive/` — не эта задача
- HAPI, тревоги доски, Мисака-нет — это T-045/T-048

## Критерии приёмки
- [ ] `PiAdapter`: argv жизни, продолжения и воли; id сессии из записи `session` — тесты по
  образцу Hermes.
- [ ] Конфиги Pi перерендериваются перед каждой сессией; воля видит hive через прокси — тест.
- [ ] `mcp.json`: body и hive `direct`, codemode не включается — тест на рендер.
- [ ] Потолок контекста: `contextWindow` и `compaction.*` из конфига узла, значения по умолчанию
  с обоснованием (порог выше базового контекста + `keepRecentTokens` с запасом, без сжатия
  подряд) — тест на рендер.
- [ ] `quota_exhausted` ловит 1308 — тест.
- [ ] `NODE_HARNESS` по умолчанию `pi`; zcode, hermes, opencode выбираемы — тест.
- [ ] Короткий прогон на стенде, 1 слот, до 15 мин, через `dev/wait_for.sh`: сестра в Pi
  вызывает инструменты тела, контекст по файлу сессии Pi, было ли сжатие, сколько запросов
  и токенов. Числа — в отчёте. Квоты Z.ai нет — прогон переносится, остальное сдаётся.
- [ ] `bash dev/check.sh` и `python tools/check.py` зелёные.

## Проверки
```
bash dev/check.sh
python tools/check.py
```

## Ограничения
- ADR-033: в песочнице только токен Z.ai; выход — MCP тела и hive, шлюз, `zai-egress`.
- Смерти без завещания не бывает: аварийная воля по таймауту — как прежде.
- Поведение — в инструкциях, не в коде (ADR-032).
- Сырых команд uc и `@gamer` в инструментах нет.

## Заметки надзора
- 2026-10-31: задачу делает надзор — исключение по решению оператора (ZCode без квоты Z.ai;
  оператор: «сёстры сожгли больше всего лимита… возьми ты, раз глм не может»). Ревью — ZCode
  после сброса квоты. HAPI, тревоги и прогон 3 слотов остаются в T-045.
- D-004 · efcce7b · вся реализация задачи (исключение оператора, не основание 1–4).
- 2026-10-31, решения оператора: (1) T-066, T-056 делает надзор как исключение, ревью — ZCode;
  (2) объём T-056 расширен (рост контекста за жизнь, пробуждения); (3) сёстры — на Pi сейчас,
  HAPI и тревоги — T-045; модель сестёр не меняется (`ZCODE_MODEL`, GLM-5.3-Flash).
  `node-1..3` остановлены 2026-10-31 ≈21:20 UTC, чтобы не съесть окно квоты 23:04 UTC: поднимать
  после merge и пересборки образа `node`.
- Открыто к ревью (найдено надзором после сдачи): ключ Z.ai в env процесса Pi наследуют все
  команды bash сестры — `env` выведет его в контекст, в файл сессии и в архив рабочего места.
  У zcode ключ лежал файлом в её home: читаем, но сам в вывод не попадает. Проверить, умеет ли
  `apiKey` в `models.json` `!command` (чтение файла из tmpfs), и убрать ключ из env. T-059
  требует того же для Last Order.
- Прогон на стенде заблокирован не только квотой: из контейнеров хоста TLS наружу рвётся
  (`unexpected eof`, и к www.google.com), с хоста api.z.ai отвечает — сеть Docker/VPN оператора.
