# NetTyan (nt): что брать

nt — прототип стримера другого трека. Python 3.10.9 строго, uv, монолит `src/main.py` на 1000 строк
с Gradio. По ADR-005 мы его не запускаем: берём идеи и куски кода.

## Берём (образцы, не импорт)
- `src/agent/llm_clients/lc_clients.py` — `ChatLiteLLM`, провайдер задаётся через `CORE_LLM_MODEL_NAME` / `CORE_LLM_API_BASE`.
- `src/agent/multi/chat_agent.py` (~149–170) — кеш префикса: постоянный system-промпт, сообщения
  только дописываются, меняющаяся ситуация — в последнем HumanMessage, keepalive.
- `src/data_collectors/minebridge.py` — рабочая реализация `PythonCallback` для uc и очередь чата.
- Позже, для стрима и голоса:
  - `src/live2d/vtube_studio.py`, `src/front/hud_layer.py` (браузерный источник OBS);
  - TTS-контейнеры: piper и simpletts на CPU, fish только на GPU;
  - Parakeet STT (`docker-compose.stt.yml`, CPU);
  - Twitch: `src/data_collectors/social_service.py`.

## Не берём
`main.py`, `core_agent.py`, `old_chat_agent.py`, `experiments/`, `agent/browser/`, Trovo, SheepChat,
DonationAlerts, контейнер фильтра, `audio_device.py`. Telegram в коде nt нет совсем.

## Локальные правки пользователя (2026-10-14): переносить не нужно
- `lc_clients.py`: при `CORE_LLM_REASONNING=NONE` отключает «thinking» у OpenRouter
  (`model_kwargs={"reasoning": {"enabled": false}}`). **Единственное полезное.** Это одна идея:
  учесть в нашем `runtime/llm/client.py` как опцию.
- `chat_agent.py`, `base_tools.py` — зеркалят голос в игровой чат и обратно. Это специфика
  пайплайна nt (TTS + chat tool), у нас такого пайплайна нет.
- `resources/Characters/MyChar/` — попытка запустить Last Order на nt. Имена в `char_config.yml`
  — Last Order, а `personalities.yml` — чужой шаблон «Worshiper». Для нас ценности нет. Исключение —
  `Voice/*.wav`, если это выбранный голос персонажа: пригодится на этапе TTS.
- Рекомендация: в корень не переносить. Чтобы не потерять и не держать сабмодуль грязным, закоммитить
  правки в локальную ветку nt (`local/last-order-try`) или сделать `git stash`.
