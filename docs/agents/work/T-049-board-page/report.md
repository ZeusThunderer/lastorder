
## Раунд 1 · 2026-10-31
Коммиты: c6d3c41, 8b888f4

### Сделано
- `runtime/hive/static/board.{html,js,css}` (новые): страница доски без сборки и внешних
  ресурсов. Три колонки («Задачи», «Запросы», «Тревоги и сообщения»), фильтр по адресату
  (живые узлы `serial · слот`, `net`, `last-order`), форма новой задачи/сообщения (для
  сообщения адресат один `net` — матрица прав 3.3). Действия, каждое одним запросом к API
  T-042: `approve`, `edit+approve` (правка значений `args` инлайн, 5.2), `deny`, `respond`
  (кнопки только из `request.allowed`), отмена задачи, ответ на `needs_input`, `ack`
  тревоги, реплика на любую запись. Живое обновление — SSE `/board/stream` (рефреш с
  debounce 250 мс) плюс лёгкий опрос `/registry/alive` раз в 10 с: retire меняет живость,
  не трогая доску, SSE-события на это нет. Весь агентский текст — только `textContent`,
  разметка строится `createElement`; `innerHTML`/`insertAdjacentHTML`/`outerHTML`/
  `document.write` в JS отсутствуют. Мёртвый узел: карточка серым, имя зачёркнутым, из
  фильтра и списка адресатов пропадает. Пресет имени не зашит: узлы отображаются
  серийником + слотом.
- `runtime/hive/app.py`: `GET /board` (Basic-auth, 401 + `WWW-Authenticate` без пароля) и
  `GET /board/{filename}` — whitelist из трёх файлов, `Cache-Control: no-store`.
  `_require_operator` дополнительно принимает ту же пару Basic (TASKBOARD §2: «Basic-пароль
  страницы»): браузер страницы ходит и на API, ключ не попадает в HTML/URL/storage; пароль
  и ключ сравниваются `secrets.compare_digest`. Явно неверный `X-Operator-Key` при верном
  Basic — отказ (отправленный заголовок проверяется).
- `tests/test_hive_board_page.py` (новый, 6 тестов): 401/200 страницы с `WWW-Authenticate`,
  неверный пароль и мусорный заголовок — 401, отдача файлов побайтово, whitelist и
  кодированный traversal — 404, Basic открывает операторский API (`from` ставит сервер),
  в статике нет `http`-подстроки и `(src|href)='//'`, в JS нет markup-синков и есть
  `textContent`.
- `docs/agents/docker-topology.md`: раздел «Доска оператора (T-049)» — URL
  `http://127.0.0.1:18080/board` (`HIVE_BOARD_PORT`), пароль — `HIVE_OPERATOR_KEY` из
  `.env` (`dev/stand_keys.py init`), для скриптов — `X-Operator-Key`.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 789 passed, 3 skipped in 71.21s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests/test_hive_board_page.py tests/test_hive_board.py tests/test_hive_board_mcp.py -q
74 passed   # 6 новых + 68 досковых не задеты правкой app.py
```
Стенд (docker, проект `t049-check` из worktree, `t049-mind:check`, hive + mc-server, два
слота, LLM-ходов нет; после проверки свёрнут, `down -v`; порт 18082, стенд оператора не
трогал). Скриншоты — headless Edge через CDP (`Authorization` через
`Network.setExtraHTTPHeaders`), в каталоге задачи:
```
$ GET /board (без пароля) → 401 + WWW-Authenticate: Basic realm="last-order board"
$ форма страницы: задача «Постройте укрытие у спавна до ночи.» → #1 видна в колонке
$ маркер window до curl сохранён (перезагрузки нет); curl message/alarm → на открытой
  странице без перезагрузки: message visible=true, alarm visible=true  → board-sse.png
$ узел 2 (MCP post запроса оператору) → mark_dead → will → retire 200 → карточка
  запроса canceled и серая (entry.dead): card gray=true → board-sse.png
$ MCP-пост от узла «<img src=x onerror=alert(1)> — проверка…» → на странице видна как
  текст (probe visible=true), <img> в колонке 0 шт → board-xss.png
$ MCP сестры (X-Node-Key слота 1): board(filter=net) →
  ["#3 message operator->net posted: curl: живое обновление…",
   "#1 task operator->net open: Постройте укрытие у спавна до ночи."]
```

### Отклонения от задачи
- `_require_operator` принимает Basic не только на `GET /board`, но и на операторских
  маршрутах API: без этого действия страницы не проходят авторизацию нигде, кроме как
  ключом в JS (запрещено). Считаю это объёмом «Basic-auth» задачи; TASKBOARD §2 прямо
  называет Basic-пароль способом личности оператора.
- Сравнение `X-Operator-Key` с ключом заменено на `secrets.compare_digest` (было `!=`).
- Ссылка на статику в HTML корневая (`/board/board.js`), не относительно документа: страница
  отдаётся на `/board` без слэша, относительные имена резолвятся в `/board.js` (нашёл на
  стенде — скрипт просто не грузился).
- «Мёртвые узлы серым»: серой делается вся карточка, если любой из участников (from/to/
  claimed_by) мёртв, а не только адресат — сценарий «запрос умершего автора» иначе не
  выделяется. Плюс опрос живости раз в 10 с (см. «Сделано»): retire не порождает
  board-события, SSE один отстаёт навсегда.

### Вне задачи
- `unionclef` подмодуль грязный (`.settings/org.eclipse.buildship.core.prefs`, VS Code
  gradle) — тот же шум, что в T-042; оператор тогда разрешил игнорировать, трогать не стал.
- `/registry/alive` не отличает «тело умерло, воля пишется» от «жив»: короткое окно между
  mark_dead и retire страница может предложить умершего адресатом; POST вернёт 422 «not
  live», страница покажет ошибку. Если нужно точнее — расширение `/registry/alive` полем
  liveness, это к надзору (менять маршрут вне «Можно трогать»).

### Вопросы
- нет
