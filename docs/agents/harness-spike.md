# Спайк харнесса узла (T-024)

Цель — цифры для выбора харнесса узла (ADR-032): живёт ли агент-сестра 15 минут через
минимальный MCP тела, сколько это стоит, какие задержки. Прогоны: OpenCode 1.19.3
(node-модель `openrouter/qwen/qwen3.7-flash`) и ZCode 3.15.3 (GLM Coding Plan). Сводки ниже;
мир прогонов не коммитится (каталог запуска вне репозитория), ключи в артефактах проверены grep'ом.

## Шаг 0. ZCode без GUI, квота, прокси

- **ZCode в Linux-контейнере без GUI — работает, но не Electron-обёртка.**
  [проверено: ubuntu:24.04, docker] ZCode 3.15.3 Linux x64 — AppImage
  (`https://cdn-zcode.z.ai/zcode/electron/releases/3.15.3/linux-x64/ZCode-3.15.3-linux-x64.AppImage`,
  sha256 `2edc79650fcff72358670af4ffbfbf7d979ba95476611a80ad1151d577e90fe3`; на странице установки
  Linux помечен бета-каналом). Electron-часть без X-сервера падает: `Missing X server or $DISPLAY`,
  platform failed to initialize (segfault); под `xvfb-run` живёт, окно создаётся. Внутри бандла
  есть headless CLI `resources/glm/zcode.cjs` (версия `zcode 0.16.9`, требует Node >= 22 —
  `node:sqlite`): `zcode -p "<prompt>" --mode yolo --json [--resume sess_...]`, `app-server`
  (ACP stdio), `--surface terminal`. `--help`, `version`, `doctor` проходят в чистом контейнере
  без иксов. Вывод: харнесс ZCode в контейнере возможен через CLI-бандл; Electron не нужен.
- **Квота ×1.5 — действующее условие ZCode, не акция.**
  [источник: https://docs.z.ai/devpack/tool/others — карточка «ZCode (1.5× Usage)»; то же для
  AutoClaw] Квоты GLM Coding Plan считаются в кредитах
  [источник: https://docs.z.ai/devpack/overview]: Lite 2 000 / 5 ч и 10 000 / нед; Pro 12 000 /
  60 000; Max 28 000 / 140 000. Кредиты = (вх × 6.9 + кеш-вх × 1.7 + вых × 24)/10 000 для
  GLM-5.3 и (2.3 / 0.56 / 8) для GLM-5.3-Flash; 5-часовое окно обновляется через 5 ч после
  траты, недельное — в 7 дней.
- **Свой прокси (LiteLLM) планом не разрешён.**
  [источник: https://docs.z.ai/devpack/usage-policy] «GLM Coding Plan may only be used within
  officially supported tools and products»; список инструментов
  (https://docs.z.ai/devpack/tool/others): ZCode, Claude Code, Codex, OpenCode, Pi, Cursor,
  Cline, TRAE, Qoder, Droid, Kilo, Roo, Crush, Goose, Eigent + best-effort AutoClaw, OpenClaw,
  Hermes Agent. LiteLLM и любые прокси в списке нет. Нарушения — риск-контроль: rate limiting,
  заморозка, бан после трёх нарушений. Обязательны base URL `https://api.z.ai/api/coding/paas/v4`
  (или `/api/anthropic` для Claude Code/Goose) и только GLM-5.3 / GLM-5.3-Flash.
  Следствие для ADR-033: токен подписки остаётся в `node-N`, в шлюз не переносится; отзыв
  «если условия разрешают — уходит в шлюз» закрыт отрицательно.
- **Аутентификация headless-CLI ZCode без браузера.** Провайдер `zai-api` (Z.ai Coding Plan)
  в `zcode-builtin.json` — тип `anthropic-messages`, `https://api.z.ai/api/anthropic`; бандл
  читает ключ из `ANTHROPIC_API_KEY` (заголовок `x-api-key`). [проверено: запуск CLI в
  контейнере — доходит до создания модели и падает только без ключа; живой вызов не проверен —
  токена в окружении нет]

## Прогон OpenCode (15 мин)

(заполняется после прогона)

## Прогон ZCode (15 мин)

(заполняется после прогона; требует токен Z.ai — см. отчёт T-024)

## Вывод

(после обоих прогонов)
