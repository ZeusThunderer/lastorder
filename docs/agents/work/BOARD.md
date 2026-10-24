# Доска задач

Протокол — [coop-protocol.md](../coop-protocol.md). Ведёт только Claude. Источник статуса — `task.md` (в ветке задачи, пока она в работе).

| id | заголовок | этап | статус | критичность | ветка |
|---|---|---|---|---|---|
| T-001 | Починить agent/scripts/remember.sh | — | done | normal | z/T-001-fix-remember-sh |
| T-002 | Архитектура сети узлов (NETWORK.md) и порты | E2 | done | critical | z/T-002-network-architecture |
| T-003 | Замер и снижение ресурсов тела и сервера | E2 | done | normal | z/T-003-resource-budget |
| T-004 | Уровни LLM в клиенте и замер моделей-кандидатов | E2 | done | critical | z/T-004-llm-tiers-bench |
| T-005 | Спайк FastJev на RTX 2070 в Docker | E2 | done | critical | z/T-005-jev-spike-2070 |
| T-006 | Py4jBody — порт GameBody поверх моста uc | E2 | done | normal | z/T-006-py4j-body |
| T-007 | Проект знаний, праймера и завещаний (KNOWLEDGE.md v2) | E2 | done | critical | z/T-007-knowledge-will-design |
| T-008 | Проект целей сети и общения узлов | E2 | done | critical | z/T-008-goals-comms-design |
| T-009 | Режимы восприятия plain/masked и фильтр служебных строк uc | E2 | done | critical | z/T-009-perception-modes |
| T-010 | Праймер — основы Minecraft как стартовая база сети | E2 | done | normal | z/T-010-primer |
| T-011 | Промпт узла под режимы восприятия и схема ответа шага | E2 | done | critical | z/T-011-prompt-step-schema |
| T-012 | Хранилище базы сети — записи, статусы, файлы узлов, git | E2 | done | critical | z/T-012-hive-core |
| T-013 | Путь смерти — детектор, завещание, retire, respawn, birth | E2 | done | critical | z/T-013-death-will-path |
| T-014 | Цикл мозга — шаг узла C2 поверх C1, логи, один узел живёт | E2 | rejected | critical | z/T-014-mind-loop |
| T-015 | JevDecider в тактике C1 (облачный Jev, ADR-027) | E3 | rejected | critical | z/T-015-jev-decider |
| T-016 | Контур целей сети в hive (C5) и консолидация | E3 | rejected | critical | z/T-016-network-goals |
| T-017 | Общение узлов — Мисака-нет, игровой чат, болтовня с режиссёром | E3 | rejected | critical | z/T-017-node-comms |
| T-018 | Сеть из 3 слотов в compose | E3 | rejected | critical | z/T-018-three-slots |
| T-019 | Отчёт MVP и 2-часовой прогон сети | E3 | rejected | normal | z/T-019-mvp-run |
| T-020 | Тело — позиции в наблюдении и рычаги добычи, боя, еды | E2 | done | normal | z/T-020-body-actions |
| T-021 | Наблюдение, варианты шага и тактика C1 на правилах | E2 | rejected | critical | z/T-021-observe-options |
| T-022 | Сервис hive и HiveClient — HTTP API портов, срок жизни, RCON | E2 | done | critical | z/T-022-hive-service |
| T-023 | Рефлексия C4 — личная цель, core memory, кандидаты знаний | E2 | rejected | critical | z/T-023-reflection |
| T-024 | Спайк харнесса — узел 15 мин в OpenCode и ZCode через MCP тела | E2 | done | critical | z/T-024-harness-spike |
| T-025 | MCP тела в mind-N — исполнитель C1, события, сторож, детектор смерти | E2 | done | critical | z/T-025-mcp-body |
| T-026 | Шлюз LLM — LiteLLM proxy, виртуальные ключи узлов, учёт и потолки | E2 | done | normal | z/T-026-llm-gateway |
| T-027 | Контейнер узла node-N — песочница, раннер с адаптерами харнесса, архив | E2 | done | critical | z/T-027-node-container |
| T-028 | MCP сети в hive — знания, предложения, Мисака-нет, завещание | E2 | done | critical | z/T-028-mcp-hive |
| T-029 | Шаблон рабочего места сестры — ядро, правила, стартовые скилы | E2 | done | critical | z/T-029-sister-instructions |
| T-030 | Last Order — агент-администратор сети по событиям | E3 | done | critical | z/T-030-last-order-agent |
| T-031 | Jev в C1 по текстовой политике сестры | E3 | done | normal | z/T-031-jev-policy |
| T-032 | Отчёт MVP и 2-часовой прогон сети агентов | E3 | done | normal | z/T-032-mvp-run |
| T-033 | Адаптер Hermes Agent в раннере узла — Z.ai, сухой прогон | E2 | done | critical | z/T-033-hermes-adapter |
| T-034 | Прогон узла 15 мин в Hermes на стенде — сравнение с T-024 | E2 | done | normal | z/T-034-hermes-run |
| T-035 | Hermes — инструменты MCP тела и hive в сессиях узла | E3 | done | critical | z/T-035-hermes-mcp-tools |
| T-036 | Ключи стенда — генерация локальных токенов и виртуальных ключей шлюза в .env | E3 | done | normal | z/T-036-stand-keys |
| T-037 | Стенд — память node-N, zai-egress в зависимостях, без автозапуска при старте Docker | E3 | ready | normal | z/T-037-stand-compose |
| T-038 | Инструкции сестры — первая реплика в линии, нет инструмента — находка, воля без расследований | E3 | done | normal | z/T-038-sister-line-and-will |
| T-039 | Повтор 2-часового прогона MVP на стенде с ключами, шлюзом и Last Order | E3 | queued | normal | z/T-039-mvp-run-2 |
