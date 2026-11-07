# T-068. Аудит к публикации · 2026-11-07

## Секреты
Сканер: gitleaks и trufflehog не установлены (`which` пусто). Запасной путь из задачи — свой скрипт
по шаблонам (`C:/tmp/t068s/scan.py`, вне репозитория): все добавленные строки всех коммитов всех
веток (`git log --all -p -U0`, 769 коммитов) и все отслеживаемые файлы рабочего дерева. Значения
не печатаются, только тип, место и маска.

Шаблоны: ключи Anthropic, OpenAI, LiteLLM/виртуальные `sk-…`, Z.ai (`<32 hex>.<16>`), токен бота
Telegram, GitHub, Hugging Face, AWS, Google, приватные ключи PEM, присваивание `api_key|secret|token|
password|rcon_password = <8+ символов>` без признаков заглушки.

Итог [проверено: вывод скрипта 2026-11-07]:
- ключей провайдеров, токенов и приватных ключей — 0 в истории и в дереве;
- `sk-…`: 2 места — фикстура теста `tests/test_hive_grants.py:36` (слова через дефис, не ключ) и
  URL в `docs/architecture/TASKBOARD.md:17`;
- присваивания: 35 мест, все — код (чтение переменной, `secrets.token_urlsafe`, параметр функции)
  или тестовые значения; `docs/agents/work/T-026-llm-gateway/report.md:54` — хэш ключа шлюза из
  ответа 429, уже усечён до 6 символов;
- файлов `.env`, `*.pem`, `*.key`, `credentials` в истории нет; есть `.env.example` и
  `docker/node/secrets/.gitignore`;
- блобов больше 1 МБ в истории нет.
Ограничение: шаблонный поиск слабее gitleaks (нет энтропии, нет сотен правил). Перед переключением
в public — прогон gitleaks оператором (инструкция в отчёте).

## Личные данные
- Авторы коммитов: `ZeusThunderer <…@users.noreply.github.com>` (768) и `3ndetz` (1, upstream).
- Почта оператора, путь `C:\Users\<имя>` — 0 вхождений в истории и дереве.
- Ник `ZeusThunderer` — пример имени игрока-оператора в тестах (`tests/test_executor.py`,
  `test_mcp_body.py`, `test_perception_chat.py`) и в T-051 — на решение оператора.
- Скриншоты `docs/agents/work/T-044-console/hapi-login.png` (пустая форма входа),
  `T-049-board-page/board-*.png` (доска с тестовыми данными) — секретов нет [проверено: просмотр].

## Спорные файлы — решает оператор
| файл | что это | риск |
|---|---|---|
| `docs/context/lec4.md` | конспект занятия курса с course.personalabs.ru | чужой материал курса |
| `docs/context/cursor_ai_character_creation_course.md` (124 КБ) | переписка с ассистентом о курсе | материалы курса, черновые мысли |
| `docs/context/Архитектура Minecraft агента….md` | исходная переписка об архитектуре | низкий |
| `task1.md`, `task2.md`, `task3-4.md` | домашние задания курса, ответы оператора | формулировки заданий курса |
| `agent/last_order/skin/src_misaka_10032.png`, `src_misaka_mikoto_v2.png` | исходные скины персонажа франшизы | права на скины и персонажа |
| ник `ZeusThunderer` в тестах | имя игрока оператора | личное |

Удаление из дерева не убирает файл из истории; чистка истории (`git filter-repo`) переписывает все
sha — решение оператора.

## Лицензии
- `LICENSE` — MIT, «Copyright (c) 2026 3ndetz» (upstream agent-workplace). Своей строки авторства нет.
- `unionclef` — GPL-3.0, в репозитории только ссылка-сабмодуль; в образ тела release-jar качается при
  сборке (`docker/body/Dockerfile`), в git его нет.
- `NetTyan` — MIT, ссылка-сабмодуль, только справочник (ADR-005).

## Решения оператора · 2026-11-07
| файл | решение |
|---|---|
| `docs/context/lec4.md`, `docs/context/cursor_ai_character_creation_course.md` | удалить и вычистить из истории |
| `task1.md`, `task2.md`, `task3-4.md`, `docs/context/Архитектура Minecraft агента….md` | удалить тем же способом |
| `agent/last_order/skin/src_misaka_10032.png`, `src_misaka_mikoto_v2.png` | удалить тем же способом; `sister.png`, `preview.png` остаются |
| ник `ZeusThunderer` | заменить на `ZeusThunderer` (ник оператора в Minecraft и на GitHub) |

Уже в `origin/main` (push 2026-10-16, 48 коммитов): переписка о курсе, переписка об архитектуре,
`task1.md`. Видимость репозитория надзор проверить не может (`gh` нет).
Исполнение — переписывание истории `git filter-repo` после merge T-068, с согласия оператора на
время (ZCode остановлен) и на способ публикации (force-push или новый репозиторий). Файлы
остаются на диске оператора вне git (`.gitignore`): на них ссылаются CLAUDE.md и задачи.
