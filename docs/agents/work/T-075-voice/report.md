# T-075. Отчёт

## 2026-11-13
Коммиты: 5dbdaf8 (ADR-055), 3565ac3 (tts), 1c071aa (speaker + say), 9f80b33 (тело, compose), 8c42427 (камера + голос).

### Сделано
- `runtime/tts/`: сервис `POST /speak` → PCM s16le 48 кГц моно. Бэкенды `qwen3` (Qwen3-TTS-12Hz-1.7B-Base,
  клон по `ref.wav` + `ref.txt`, sdpa без flash-attn) и `openrouter` (`fish-audio/s2.1-pro`, `input_references`).
  `normalize.py`: без `*…*`, эмодзи, ссылок; числа — словами (`num2words`). `design_voice.py` — образец
  голоса по описанию (VoiceDesign), вариант без чужого голоса.
- `runtime/voice/speaker.py`: очередь на сестру, синтез следующей строки во время текущей, чат в момент
  начала звука, ожидание EOF от тела, отказ при 3 ждущих строках, строка без голоса — в чат молча.
- `runtime/mind/mcp_body.py`: `say` при `VOICE_ENABLED=1` ставит строку в очередь (`voice: queued, ahead`).
- Тело: PulseAudio (`lo_out`, `lo_voice`, микрофон `lo_mic`), SVC 2.6.27 только в голосовых телах,
  конфиг `voicechat-client.properties` на каждом старте, режим xvfb (если нет камеры), вход `:25500` на link-N
  (`socat` → `pacat`, `max-children=1`).
- compose: `BODY_VOICE_N` (тело и разум), `tts` (профиль `voice`, GPU по имени `TTS_GPU_NAME`),
  SVC на `mc-server` (UDP 24454). `up.sh` поднимает `BODY_VOICE_N`.

### Проверки
Ничего не прогонялось — запрет оператора (без тестов и `docker up`). Только `py_compile` и `bash -n`.
Написаны: `tests/test_voice_speaker.py`, `tests/test_tts_normalize.py`, `tests/test_voice_stand.py`;
обновлён `tests/test_fabric_server.py` (список модов сервера).

### Предположения до прогона
- SVC пишет с `lo_mic` через OpenAL LWJGL в контейнере; запасной путь — `BODY_VOICE_JAVA_MIC=true`.
- Путь конфига SVC — `config/voicechat/voicechat-client.properties`.
- PulseAudio под root в пользовательском режиме стартует (с предупреждением).
- `socat -t 120` держит соединение до выхода `pacat`; конец строки = EOF.
- Задержка Qwen3-TTS на 5070 Ti — не измерена.

### Как запустить (после разрешения)
1. Образец: `.stand/voice/ref.wav` + `ref.txt` (точная расшифровка) или `design_voice.py` (докстринг).
2. `docker compose -f docker/compose.yml --profile voice up -d --build tts`; проверка:
   `curl -s -X POST localhost:8790/speak -H 'content-type: application/json' -d '{"text":"Проверка связи, сказала Мисака 00007"}' -o a.raw && ffplay -f s16le -ar 48000 -ac 1 a.raw`.
3. `.env`: `BODY_VOICE_1=1`; пересобрать тело и `mc-server` (SVC), поднять слот; зайти с SVC-клиентом,
   вызвать `say` через MCP тела.

### Вне задачи
- С SVC в теле uc начнёт слать чужой голос в `onVoiceFeed` (сейчас `pass` в `py4j_body.py`) — нагрузка
  на мост py4j не измерена. Это же вход для STT (GigaAM v3, следующий шаг).
