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
| T-037 | Стенд — память node-N, zai-egress в зависимостях, без автозапуска при старте Docker | E3 | done | normal | z/T-037-stand-compose |
| T-038 | Инструкции сестры — первая реплика в линии, нет инструмента — находка, воля без расследований | E3 | done | normal | z/T-038-sister-line-and-will |
| T-039 | Повтор 2-часового прогона MVP на стенде с ключами, шлюзом и Last Order | E3 | done | normal | z/T-039-mvp-run-2 |
| T-040 | Контракт воли — submit_will принимает текст, служебные поля заполняет среда | I | done | critical | z/T-040-will-contract |
| T-041 | Pi в образе узла на Z.ai Coding Plan и выбор готовой веб-консоли | I | done | critical | z/T-041-pi-and-console |
| T-042 | Доска задач в hive — хранилище, переходы, смерть, HTTP, лента, MCP-инструменты | I | done | critical | z/T-042-board |
| T-043 | Last Order на Pi — одна долгая сессия, события сети сообщениями, запасной OpenCode с тревогой | I | done | critical | z/T-043-lo-long-session |
| T-044 | HAPI на стенде — хаб, `hapi pi` в node-1 и рядом с Last Order, чат оператора, граница токена | I | done | critical | z/T-044-console |
| T-045 | Сёстры на Pi — адаптер раннера, жизнь и воля в Pi, запасной OpenCode с тревогой | I | done | critical | z/T-045-sisters-on-pi |
| T-046 | Гранты — запрос выхода наружу и секрета, одобрение оператора, хранилище hive, egress-прокси | I | done | critical | z/T-046-grants |
| T-047 | Мастерская Last Order — свои расширения Pi, перезагрузка, публикация инструментов сёстрам | I | draft | critical | z/T-047-workshop |
| T-048 | misaka-link — расширение Pi доставляет доску, Мисака-нет и события сети в живую сессию | I | done | critical | z/T-048-misaka-link |
| T-049 | Страница доски для оператора — задачи, запросы, тревоги, живое обновление | I | done | normal | z/T-049-board-page |
| T-050 | Доска у сестёр на текущем раннере — события доски будят wait_events, инструкции по разделу 13 | I | done | critical | z/T-050-board-on-runner |
| T-051 | Оператор в игровом чате — сестра узнаёт его реплики, чат не теряет строк | I | done | critical | z/T-051-operator-in-game-chat |
| T-052 | Короткий прогон на стенде — задача с доски и реплика оператора в чате у живой сестры | I | done | normal | z/T-052-board-chat-run |
| T-053 | Команды сервера сестре — команды игрока с запретным списком и вход /login средой | I | done | critical | z/T-053-server-commands |
| T-054 | Старт слота — нет смерти от устаревшего hp, wait_events не дольше таймаута харнесса | I | done | critical | z/T-054-slot-start-fixes |
| T-055 | Мост py4j — порядок подъёма тела и mind, мягкая остановка, перезапуск слота одной командой | I | done | normal | z/T-055-py4j-bridge |
| T-056 | Контекст запроса сестры — рост за жизнь, пробуждения, крупные части | I | done | normal | z/T-056-context-cut |
| T-057 | Мост инфраструктуры — Last Order поднимает и снимает сестёр, хост исполняет без одобрения на каждое действие | I | ready | critical | z/T-057-infra-bridge |
| T-058 | Прогон стенда после исправлений — команда сервера, старт слота, мост, размер запроса | I | ready | normal | z/T-058-stand-run-fixes |
| T-059 | Last Order на Z.ai Coding Plan — харнесс Pi, модель GLM-5.3, запасной OpenCode через шлюз | I | done | critical | z/T-059-lo-on-zai |
| T-060 | Классификатор безопасности — второе мнение о запросах infra Last Order на локальной модели | I | ready | normal | z/T-060-safety-classifier |
| T-061 | Скин сестёр — тела LastOrder01..03 в скине sister.png на своём и внешнем сервере | I | ready | normal | z/T-061-sister-skin |
| T-062 | mc-server на Fabric — закреплённые версии загрузчика и модов, мир без потерь | I | ready | normal | z/T-062-fabric-server |
| T-063 | Камера тела и сервис media — экран тела потоком в MediaMTX, кольцевой буфер, вырезка по времени | I | ready | critical | z/T-063-body-camera |
| T-064 | Last Order и видео — доступ к media, ffmpeg, журналы сети на чтение, справка об инструментах | I | ready | critical | z/T-064-lo-media-access |
| T-065 | Прогон камер на стенде — три тела на Xvfb в media, цена CPU и памяти, влияние на C1 | I | ready | normal | z/T-065-camera-stand-run |
| T-066 | Сёстры на Pi напрямую — адаптер раннера, MCP без codemode, потолок контекста сжатием | I | done | critical | z/T-066-sisters-pi-direct |
| T-067 | Ролик-знакомство с Last Order — персонаж, сеть сестёр, кадры игры (надзор) | — | ready | normal | c/T-067-intro-video |
| T-068 | Репозиторий к публикации — README и обзор архитектуры на английском, схемы, аудит секретов (надзор) | — | ready | normal | c/T-068-github-publication |
