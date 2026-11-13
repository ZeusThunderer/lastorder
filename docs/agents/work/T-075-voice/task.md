---
id: T-075
title: Голос сестёр в игре через SVC (v1, к рейду)
stage: —
status: done
criticality: normal
after: []
branch: main
created: 2026-11-13
---

# T-075. Голос сестёр в игре через SVC (v1, к рейду)

## Цель
Строка `say` звучит голосом сестры в Simple Voice Chat на любом сервере с SVC. Строка появляется
в чате в момент начала звука; сестра не перебивает сама себя.

## Контекст
- ADR-055 (решение), ADR-030 (голос), этап 4b ROADMAP, ADR-010 (слух — следующий шаг).
- Оператор, 2026-11-13: без тестов и `docker up` до его команды (квота Z.ai кончилась);
  GPU освобождается под демо; образец голоса — Мисака 10032 или похожий.

## Объём
Можно трогать: `runtime/tts/`, `runtime/voice/`, `runtime/mind/mcp_body.py`, `docker/body/`,
`docker/tts/`, `docker/compose.yml`, `docker/node/up.sh`, `tests/`, документы.
Нельзя трогать: `unionclef/`, `NetTyan/`.

## Критерии приёмки
- [ ] `pytest tests/test_voice_speaker.py tests/test_tts_normalize.py` зелёный (после разрешения).
- [ ] `tts` отдаёт PCM на русскую фразу голосом образца.
- [ ] На стенде: голос от LastOrder01 слышен в SVC; чат-строка появляется с началом звука;
      две строки подряд не перекрываются.
