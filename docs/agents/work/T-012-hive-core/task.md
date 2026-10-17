---
id: T-012
title: Хранилище базы сети — записи, статусы, файлы узлов, git
stage: E2
status: in_progress
criticality: critical
branch: z/T-012-hive-core
created: 2026-10-16
after: [T-007]
---

# T-012. Хранилище базы сети — записи, статусы, файлы узлов, git

## Цель
Библиотека `runtime/knowledge/store.py`, на которой стоит hive (T-022): единственный код, который
пишет базу. Она ведёт записи знаний, evidence, голоса и статусы по KNOWLEDGE v2, файлы узлов
(`birth.json`, `core.md`, `will.md`), сквозные номера, git-коммит на каждое изменение и собирает
срез знаний для шага. HTTP здесь нет. Требование R2 (сохранение и определение знаний).

## Контекст
- KNOWLEDGE.md v2 (T-007): раскладка volume `knowledge`, запись, дедуп (алгоритм и порог),
  переходы статусов, завещание, `birth.json`, `core.md`, `Inheritance`, бюджеты среза.
- `runtime/knowledge/schema.py`, `runtime/knowledge/budget.py` (T-007).
- `runtime/ports.py`: `KnowledgeEntry`, `KnowledgeSlice`, `KnowledgeCandidate`, `Vote`,
  `BirthRecord`, `NodeBrief`; `runtime/lastorder/persona.py` `Inheritance`; `runtime/lastorder/traits.py`
  `Traits`.
- ADR-017 (hive — единственный писатель, файлы под git ради истории и `blame`), ADR-018 (сквозной
  5-значный номер), ADR-020 (праймер только в `plain`).
- Праймер — `agent/last_order/primer/` (T-010). Если T-010 ещё не смержена — тест на фикстуре из двух
  записей, загрузчик берёт каталог из конфига.

## Объём
Можно трогать:
- `runtime/knowledge/store.py`, `runtime/knowledge/dedup.py`, `runtime/knowledge/slice.py` (новые)
- `tests/test_knowledge_store.py`, `tests/test_knowledge_dedup.py`, `tests/test_knowledge_slice.py`
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `runtime/knowledge/schema.py`, `budget.py` — правки только если в схеме T-007 найден дефект;
  каждое изменение — в отчёте с причиной
- `runtime/hive/`, `docker/` — сервис и compose в T-022

## Критерии приёмки
- [ ] `KnowledgeStore(root: Path, git: bool = True)`; при пустом `root` создаёт раскладку из
  KNOWLEDGE v2 и `git init`; при `PERCEPTION_MODE=plain` загружает праймер одним коммитом
  `primer: load N records`.
- [ ] Номера: `next_serial()` атомарен между потоками и процессами (файловая блокировка), сквозной,
  5-значный при выводе, переживает перезапуск. Тест: 3 процесса × 20 рождений → 60 разных
  номеров подряд.
- [ ] `birth(slot, traits, seed)` → `BirthRecord`, пишет `nodes/<serial>/birth.json`; `retire(serial,
  cause, will)` пишет `will.md` (frontmatter + тело по T-007), помечает узел мёртвым; повторный
  `retire` для того же номера — ошибка без перезаписи. Тесты.
- [ ] Кандидат: новая запись `hypothesis` с `author`, `ts`, `id` `k-NNNN`; дубль по алгоритму T-007
  (тот же `about`, похожий `claim`) — новое evidence существующей записи, без новой записи. Тест на
  пороге похожести: дубль и не дубль.
- [ ] Статусы: `confirmed` = 1 независимое evidence (не автора) или 2 голоса «за» без «против»;
  `disputed` при противоречащем evidence; `retired` после двух опровержений; повторный голос того же
  узла заменяет прежний. Таблица переходов T-007 проверена параметризованным тестом.
- [ ] Core memory: `store_core_memory(serial, text)` режет до 1.5 KB по границе строки и пишет
  `core.md`; `core_memory(serial)` читает. Тест.
- [ ] Каждая операция записи — один git-коммит с сообщением `<op>: <serial|id> …` и автором `hive`;
  `git log` во временном репо в тесте показывает ожидаемые коммиты. Узел в git не пишет: публичного
  метода «записать файл как есть» нет.
- [ ] `inheritance(serial)` — по T-007 (K последних завещаний, сводка предшественников и живых,
  термины). `knowledge_slice(serial, keys)` — каталог, записи по видимым ключам, хвост свежих, ≤2
  кандидата на голос (не свои и ещё не голосованные этим узлом), цели сети (пока пусто — их ведёт
  T-016); каждый блок в бюджете `budget.py`. Тесты на бюджет и на выбор кандидатов.
- [ ] Ошибки ввода (неизвестный узел, битый кандидат) — исключения своего типа, база не меняется.

## Проверки
```
uv run pytest
uv run python tools/check.py
```

## Ограничения
- Пишет базу только этот модуль, а вызывает его только hive.
- Секреты не нужны; путь к базе — из конфига (`KNOWLEDGE_DIR`).
- Никаких вызовов LLM.

## Заметки надзора
