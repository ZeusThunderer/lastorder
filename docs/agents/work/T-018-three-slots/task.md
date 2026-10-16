---
id: T-018
title: Сеть из 3 слотов в compose
stage: E3
status: draft
criticality: critical
branch: z/T-018-three-slots
created: 2026-10-16
---

# T-018. Сеть из 3 слотов в compose

## Цель
`docker compose up` поднимает сервер, `hive`, `body-1..3`, `mind-1..3` с настройками ресурсов из T-003;
число слотов задаётся без копипасты. Требование R1.

## Контекст
- После T-003 (ресурсы), T-012 (hive), T-014 (цикл мозга). ADR-015, ADR-016, ADR-018.

## Объём
Можно трогать:
- `docker/compose.yml`, `docker/mind/Dockerfile`, `.env.example`
- `docs/agents/docker-topology.md`
- файлы задачи

## Критерии приёмки (предварительно)
- [ ] Три пары body/mind, у каждой своя `network_mode: service:body-N` и volume; hive один.
- [ ] Перезапуск одного `mind-N` не трогает остальные слоты; узел в слоте продолжает с новым номером
  или аварийным завещанием.
- [ ] `docker stats` трёх слотов в отчёте, в пределах бюджета T-003.
- [ ] Секреты только через env, новых bind-mount нет.

## Заметки надзора
