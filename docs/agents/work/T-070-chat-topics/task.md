---
id: T-070
title: Запретные темы чата других игроков не доходят до моделей; второе мнение infra снято
stage: I
status: done
criticality: high
after: [T-069]
created: 2026-11-11
---

# T-070. Запретные темы чата

Оператор (2026-11-11): провайдер может забанить аккаунт за присланный ему текст, даже если
сестра его не повторит. Строки чужих игроков на запретные темы сеть должна не видеть совсем
(ADR-048). Заодно — решение по T-060: второе мнение о запросах infra снято (ADR-049).

Можно трогать: `runtime/perception/topics.py`, `runtime/mind/executor.py`, `runtime/mind/mcp_body.py`,
`docker/mind/chat-topics/`, `docker/compose.yml`, тесты, ADR, docker-topology.
