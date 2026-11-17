# T-079 — Go первым, OpenRouter резервом

## Реализация
- Запрос оператора: «Давай перепишем шлюз, на opencode основной шлюз а openrouter резервный».
- Четыре существующих имени node/smart ведут в Go GLM-5.3-flash. Имена оставлены для
  совместимости с конфигурацией клиентов и разрешениями виртуальных ключей.
- Резервы OR отдельные: node — Qwen3.7-flash, smart — GLM-5.3-flash; циклов нет.
- Прямое имя opencode-go/glm-5.3-flash тоже имеет smart-резерв OR.
- Jev, его задержки и локальный резерв, Pi/Z.ai и ключи потребителей не менялись.

## Проверки
- RED: новый контракт отверг прежнюю маршрутизацию OpenRouter первым.
- GREEN: новый контракт и Jev: 56 passed, 1 прежнее падение TTS (test_provider_key_reaches_only_the_gateway).
- tools/check.py: Every workplace is intact. git diff --check: ok.
- На установленном LiteLLM живого контейнера запущен check_fallback.py: локальный HTTP
  основной провайдер вернул 429, Router обратился к локальному резерву и вернул reserve-ok.
  Порядок: /go/v1/chat/completions → /or/v1/chat/completions. Реальных ключей/внешнего трафика нет.
- Полный pytest и внедрение ещё выполняются.

## Прямая правка
D-013, основание 6: задача надзора, прямо порученная оператором.

## Полная проверка перед внедрением
pytest: 1237 passed, 5 failed, 4 skipped, 1 deselected (276 s). Те же прежние пять,
подтверждённые на исходном 7b10fbe в T-078:
- tests/test_aw_scripts.py::test_remember_creates_note_for_new_entity
- tests/test_aw_scripts.py::test_remember_adds_events_header_to_note_without_one
- tests/test_gateway_config.py::TestComposeWiring::test_provider_key_reaches_only_the_gateway
- tests/test_stand_keys.py::test_key_lists_cover_the_stand
- tests/test_stand_keys.py::test_node_up_sh_passes_after_init
Новых падений нет. Полный вывод: /artifacts/characters/pytest-T079.log.
Саморевью: изменены только маршруты, два контракта и описание env; секретов нет;
отдельный агент/Claude не вызывались по ограничениям проекта и оператора.
