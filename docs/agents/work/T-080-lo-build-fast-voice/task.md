---
id: T-080
status: review
branch: codex/T-080-lo-build-fast-voice
---

# Поручение оператора
Комплект LO, проверка ресурсов, fast Qwen на 2070, Go API.

## Можно трогать
agent/network/skills/build/, runtime/tts/, runtime/voice/, runtime/node/, runtime/pi/misaka-link/, runtime/hive/admin.py, docker/tts/, docker/node/, docker/compose.yml, tests/, dev/resource_probe.py, docs/agents/work/T-080-lo-build-fast-voice/.

## Дополнение оператора 2026-11-18
Добавить каталог Go, убрать Z.AI из HAPI; разрешены также docker/gateway/config.yaml и runtime/node/go_models.json.
