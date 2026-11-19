"use strict";

const $ = (id) => document.getElementById(id);
const sections = {
  overview: "Доска", tasks: "Задачи", requests: "Запросы",
  events: "События", history: "История", network: "Состояние сети",
};
const kinds = { task: "Задача", request: "Запрос", message: "Сообщение", alarm: "Тревога" };
const statuses = {
  open: "Открыто", taken: "В работе", needs_input: "Ждёт уточнения",
  done: "Выполнено", failed: "Не выполнено", declined: "Отказ", canceled: "Отменено",
  approved: "Одобрено", denied: "Отклонено", answered: "Есть ответ",
  acked: "Получение подтверждено", cleared: "Снято",
};
const lanes = {
  alarms: { title: "Открытые тревоги", section: "events", empty: "Открытых тревог нет.", link: "К открытым тревогам" },
  decisions: { title: "Запросы на решение", section: "requests", empty: "Запросов на решение нет.", link: "К запросам на решение" },
  clarify: { title: "Нужны уточнения", section: "tasks", empty: "Задач, ожидающих вашего уточнения, нет.", link: "К задачам, ожидающим уточнения" },
  execution: { title: "Очередь исполнения", section: "requests", empty: "Заявок на исполнение нет.", link: "К очереди исполнения" },
};
const filterStatuses = {
  tasks: ["open", "taken", "needs_input"], requests: ["open", "taken"], events: ["open"],
  history: ["done", "failed", "declined", "canceled", "approved", "denied", "answered", "acked", "cleared"],
};
const alarmTypes = {
  harness_failover: "Сменился исполнитель сессии", session_missing: "Сессия недоступна",
  stuck: "Сессия зависла", delivery_failing: "Сбой доставки", quota: "Лимит исчерпан",
  emergency_will: "Аварийное завещание", mind_outdated: "Устаревшая версия mind",
};
const actionSpecs = {
  reply: { label: "Ответить", required: true },
  clarify: { label: "Уточнить задачу", target: "taken", required: true, primary: true },
  approve: { label: "Одобрить", target: "approved", primary: true },
  edit: { label: "Одобрить с правкой", target: "approved" },
  deny: { label: "Отклонить", target: "denied", required: true, danger: true },
  respond: { label: "Дать решение", target: "answered", required: true },
  ack: { label: "Подтвердить получение", target: "acked", primary: true, noText: true },
  cancel: { label: "Отменить запись", target: "canceled", danger: true },
  revoke: { label: "Отозвать грант", danger: true, noText: true },
};

const dateFormat = new Intl.DateTimeFormat("ru-RU", {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
});
const detailDateFormat = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });
const activeFilter = ["lane", "status", "to"];
const state = {
  route: readRoute(), q: "", view: null, entry: null, seenAt: null,
  listFresh: false, detailFresh: false, monitorFresh: false, network: null,
  source: null, connected: false, loading: false, initialError: false, pending: false,
  listSeq: 0, detailSeq: 0, statusSeq: 0, pages: 1, busy: false,
  action: null, returnFocus: null, returnScroll: 0, targetLost: false,
};
history.scrollRestoration = "manual";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(label, className) {
  const node = el("button", className, label);
  node.type = "button";
  return node;
}
function report(node, message) {
  node.textContent = message || "";
  node.hidden = !message;
}
function say(message) { $("announcement").textContent = message; }
function formatDate(iso, format = dateFormat) {
  const value = new Date(iso || NaN);
  return Number.isFinite(value.getTime()) ? format.format(value) : "Время неизвестно";
}
function time(iso) {
  const node = el("time", "", formatDate(iso));
  if (iso) node.dateTime = iso;
  return node;
}
function serial(value) {
  return String(value).padStart(5, "0");
}
function actor(value) {
  const labels = { operator: "Оператор", "last-order": "LO", net: "Вся сеть", system: "Система", infra: "Исполнитель" };
  if (/^\d+$/.test(value)) return "Сестра " + serial(value);
  if (value?.startsWith("runner:")) return "Раннер " + value.slice(7);
  return Object.hasOwn(labels, value) ? labels[value] : String(value ?? "—");
}
function statusText(entry) {
  const node = el("span", "status-text", entry.kind === "message" ? "Сообщение" : statuses[entry.status] || entry.status);
  node.dataset.status = entry.status;
  return node;
}
function readRoute() {
  const query = new URLSearchParams(location.search);
  const name = query.get("section");
  const section = Object.hasOwn(sections, name) ? name : "overview";
  const lane = query.get("lane");
  const status = query.get("status");
  const to = query.get("to");
  const id = Number(query.get("entry"));
  return {
    section, lane: Object.hasOwn(lanes, lane) && lanes[lane].section === section ? lane : "",
    status: filterStatuses[section]?.includes(status) ? status : "",
    to: filterStatuses[section] && /^(?:net|last-order|operator|\d+)$/.test(to || "") ? to : "",
    id: Number.isSafeInteger(id) && id > 0 ? id : null,
  };
}
function address(route) {
  const query = new URLSearchParams();
  if (route.section !== "overview") query.set("section", route.section);
  for (const key of activeFilter) if (route[key]) query.set(key, route[key]);
  if (route.id) query.set("entry", route.id);
  return "/board" + (query.size ? "?" + query : "");
}
function listKey(route) {
  return [route.section, route.lane, route.status, route.to].join("|");
}
function navigate(route, { replace = false, pop = false, trigger = null } = {}) {
  const previous = state.route;
  const changedList = listKey(previous) !== listKey(route);
  const changedEntry = previous.id !== route.id;
  if (!pop && (changedList || changedEntry)) {
    history[replace ? "replaceState" : "pushState"](null, "", address(route));
  }
  state.route = route;
  $("menu").setAttribute("aria-expanded", "false");
  if (changedList) {
    if (route.section !== previous.section) {
      state.q = "";
      $("search").value = "";
    }
    state.pages = 1;
    state.view = null;
    state.listFresh = false;
    state.listSeq++;
    if ($("list").contains($("detail"))) $("inline-details").append($("detail"));
    $("list").replaceChildren();
    state.initialError = false;
    renderView();
    if (!route.id) {
      window.scrollTo(0, 0);
      $("section-heading").focus({ preventScroll: true });
    }
  }
  if (changedEntry) {
    if (!previous.id && route.id) {
      state.returnFocus = trigger || document.activeElement;
      state.returnScroll = window.scrollY;
    }
    state.entry = null;
    state.detailFresh = false;
    state.detailSeq++;
    closeAction();
    $("recovered").replaceChildren();
    report($("action-feedback"), "");
    report($("detail-state"), "");
    placeDetails(true);
    annotateSelection();
    if (!changedList && route.id) loadDetail();
    if (previous.id && !route.id && !changedList) {
      window.scrollTo(0, state.returnScroll);
      (state.returnFocus?.isConnected ? state.returnFocus : $("section-heading")).focus({ preventScroll: true });
    }
  }
  if (changedList) refresh();
}
function gotoSection(section, lane = "") {
  navigate({ section, lane, status: "", to: "", id: null });
}
function linkFor(section, lane = "") {
  const link = el("a", "", sections[section]);
  link.href = address({ section, lane });
  link.dataset.section = section;
  if (lane) link.dataset.lane = lane;
  return link;
}
function sameTab(event) {
  return event.button === 0 && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
}
function updateNav() {
  for (const link of $("navigation").querySelectorAll("[data-section]")) {
    if (link.dataset.section === state.route.section) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
    const count = link.querySelector(".nav-count");
    if (count) count.textContent = state.view?.counts?.[link.dataset.section] ?? "";
  }
  $("menu").textContent = sections[state.route.section];
}

async function api(path, { method = "GET", body, signal } = {}) {
  const timeout = AbortSignal.timeout(8000);
  let response;
  try {
    response = await fetch(path, {
      method, cache: "no-store", signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      ...(body !== undefined ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
  } catch (cause) {
    throw Object.assign(new Error("Недоступна связь с доской"), { cause, network: true });
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw Object.assign(new Error("Доска отклонила запрос"), {
      status: response.status, kind: payload.error?.kind, network: response.status >= 500,
    });
  }
  return response.json();
}

function openStream() {
  if (state.source) return;
  const source = new EventSource("/board/stream");
  state.source = source;
  source.onopen = () => {
    state.connected = true;
    refresh();
  };
  source.onerror = () => {
    state.connected = false;
    state.listFresh = state.detailFresh = state.monitorFresh = false;
    updateConnection();
    queueRefresh();
  };
  source.addEventListener("board", queueRefresh);
}
let refreshTimer;
function queueRefresh() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    refresh();
  }, 350);
}
function setBoardAvailable() {
  $("board").hidden = false;
  $("login").hidden = true;
  $("login-password").value = "";
}
function showLogin(message = "") {
  state.source?.close();
  state.source = null;
  state.connected = state.listFresh = state.detailFresh = state.monitorFresh = false;
  state.listSeq++;
  state.detailSeq++;
  state.statusSeq++;
  $("detail-dialog").close();
  $("composer").close();
  closeAction();
  $("board").hidden = true;
  $("detail").hidden = true;
  $("login").hidden = false;
  report($("login-error"), message);
  $("login-password").focus();
}
function updateConnection() {
  const current = state.listFresh;
  const header = $("connection");
  header.textContent = state.connected && current ? "Связь активна"
    : current ? "Связь: снимок обновлён" : state.seenAt || state.initialError ? "Связь прервана" : "Связь проверяется";
  header.dataset.state = current ? "live" : state.seenAt || state.initialError ? "offline" : "";
  $("ribbon").hidden = current || !state.seenAt;
  const offline = !current && state.seenAt
    ? "Связь прервана. Последнее обновление " + formatDate(state.seenAt) + "." : "";
  report($("ribbon-text"), offline);
  $("compose-connection").hidden = !offline;
  report($("compose-connection-text"), offline);
  $("detail-retry").hidden = !state.route.id || (current && (state.detailFresh || !state.entry));
  $("create").disabled = !current || state.pending;
  updateComposer();
  updateAction();
  updateInfraControls();
}

function viewPath(route, cursor) {
  const params = new URLSearchParams({ section: route.section === "network" ? "requests" : route.section });
  if (route.section === "network") params.set("lane", "execution");
  else for (const key of activeFilter) if (route[key]) params.set(key, route[key]);
  if (cursor) params.set("before", cursor);
  return "/board/view?" + params;
}
async function loadView({ more = false } = {}) {
  if (more && !state.view?.next_cursor) return false;
  const seq = ++state.listSeq;
  const route = { ...state.route };
  const q = state.q.trim();
  const search = q ? { method: "POST", body: { q } } : {};
  const depth = state.pages;
  let cursor = more ? state.view?.next_cursor : null;
  let entries = more ? [...state.view.entries] : [];
  let view;
  state.loading = !state.view;
  state.busy = true;
  $("more").disabled = true;
  if (seq === state.listSeq && state.loading) renderView();
  try {
    for (let page = 0; page < (more ? 1 : depth); page++) {
      view = await api(viewPath(route, cursor), search);
      if (seq !== state.listSeq) return false;
      entries.push(...view.entries);
      cursor = view.next_cursor;
      if (!cursor) break;
    }
    state.view = { ...view, entries: [...new Map(entries.map((entry) => [entry.id, entry])).values()] };
    if (more) state.pages++;
    state.listFresh = true;
    state.seenAt = new Date().toISOString();
    state.initialError = false;
    setBoardAvailable();
    if (state.route.id && $("detail").hidden) placeDetails(true);
    openStream();
    return true;
  } catch (error) {
    if (seq !== state.listSeq) return false;
    if (error.status === 401) showLogin(state.seenAt ? "Войдите снова, чтобы открыть доску." : "");
    state.listFresh = false;
    state.initialError = true;
    return false;
  } finally {
    if (seq === state.listSeq) {
      state.loading = state.busy = false;
      renderView();
      updateConnection();
    }
  }
}
async function refresh() {
  if (!$("login").hidden) return false;
  const tasks = [loadView()];
  if (state.route.id) tasks.push(loadDetail());
  if (state.route.section === "network") tasks.push(loadNetwork());
  const [view] = await Promise.all(tasks);
  return view;
}

function options(select, items, target) {
  const signature = JSON.stringify(items);
  if (select.dataset.options !== signature) {
    select.replaceChildren(...items.map(([value, label]) => new Option(label, value)));
    select.dataset.options = signature;
  }
  select.value = target;
}
function renderFilters() {
  const { section, lane, status, to } = state.route;
  const available = Object.entries(lanes).filter(([, item]) => item.section === section);
  $("lane-label").hidden = !available.length;
  options($("lane"), [["", "Все очереди"], ...available.map(([key, item]) => [key, item.title])], lane);
  options($("filter-status"), [["", "Все статусы"], ...(filterStatuses[section] || []).map((key) => [key, statuses[key]])], status);
  const nodes = (state.view?.alive || []).map((brief) => [String(brief.serial), actor(String(brief.serial))]);
  const toChoices = [["", "Все адресаты"], ["last-order", "LO"], ["net", "Вся сеть"], ["operator", "Оператор"], ...nodes];
  if (to && !toChoices.some(([value]) => value === to)) toChoices.push([to, actor(to)]);
  options($("filter-to"), toChoices, to);
}
function renderView() {
  const { section, lane, status, to } = state.route;
  const listing = section !== "overview" && section !== "network";
  $("section-heading").textContent = section === "overview" ? "Сейчас в сети" : sections[section];
  $("main").dataset.section = section;
  $("glance").hidden = section !== "overview";
  if (section === "overview" && state.view) {
    for (const key of ["tasks", "requests", "events"]) {
      const jump = $("glance").querySelector('[href="#stream-' + key + '"]');
      jump.textContent = sections[key] + " · " + state.view.counts[key];
    }
  }
  $("filters").hidden = !listing;
  $("network").hidden = section !== "network";
  $("list").hidden = section === "network";
  renderFilters();
  updateNav();
  renderRecipients();
  const current = state.view;
  let notice = "";
  if (!current) notice = state.initialError ? "Не удалось загрузить доску." : "Загружаем доску…";
  else if (listing && !current.entries.length) {
    notice = lane || status || to || state.q ? "По этим условиям ничего не найдено." : "В этом разделе пока нет записей.";
  }
  report($("list-state"), notice);
  if (state.initialError && !state.seenAt) {
    const retry = button("Повторить");
    retry.addEventListener("click", refresh);
    $("list-state").append(" ", retry);
  }
  const more = $("more");
  more.hidden = !listing || !current?.next_cursor;
  more.disabled = !state.listFresh || state.busy;
  if (!current) return;

  if (section === "overview") {
    $("list").querySelector('[data-all-entries]')?.remove();
    for (const key of ["tasks", "requests", "events"]) {
      renderQueue($("list"), key, { count: current.counts[key], entries: current.streams[key] });
    }
  } else if (section === "network") {
    renderQueue($("execution"), "execution", {
      entries: current.entries.slice(0, 2), count: current.attention.execution.count,
    });
  } else if (current.entries.length) {
    let wrap = $("list").querySelector('[data-all-entries]');
    if (!wrap) {
      wrap = el("div", "panel");
      wrap.dataset.allEntries = "true";
      wrap.append(el("ul", "entry-list"));
      $("list").append(wrap);
    }
    renderRows(wrap.firstElementChild, current.entries);
  } else {
    if ($("list").contains($("detail"))) $("inline-details").append($("detail"));
    $("list").replaceChildren();
  }
  annotateSelection();
  if (state.route.id) placeDetails();
}
function renderQueue(parent, key, data) {
  const stream = Object.hasOwn(sections, key);
  const group = stream ? { title: sections[key], section: key, empty: { tasks: "Активных задач нет.", requests: "Открытых запросов нет.", events: "Событий пока нет." }[key], link: sections[key] } : lanes[key];
  let panel = parent.querySelector('[data-queue="' + key + '"]');
  if (!panel) {
    panel = el("section", "panel");
    panel.dataset.queue = key;
    panel.id = "stream-" + key;
    const heading = el("div", "panel-heading");
    const title = el("h3", "", group.title);
    title.append(el("span"));
    const link = linkFor(group.section, stream ? "" : key);
    heading.append(title, link);
    panel.append(heading, el("ul", "entry-list"), el("p", "empty", group.empty));
    parent.append(panel);
  }
  panel.querySelector("h3 span").textContent = data.count;
  const link = panel.querySelector("a");
  link.textContent = "Все " + data.count;
  link.setAttribute("aria-label", group.link + ", всего " + data.count);
  link.hidden = data.count === 0;
  panel.lastElementChild.hidden = data.entries.length > 0;
  renderRows(panel.querySelector("ul"), data.entries);
}
function renderRows(parent, entries) {
  const old = new Map([...parent.children].map((row) => [Number(row.firstElementChild.dataset.entry), row]));
  for (const [index, entry] of entries.entries()) {
    const row = old.get(entry.id) || el("li");
    old.delete(entry.id);
    const link = row.firstElementChild || el("a", "entry");
    if (!row.firstElementChild) row.append(link);
    link.dataset.entry = entry.id;
    link.href = address({ ...state.route, id: entry.id });
    const metadata = el("div", "entry-meta");
    metadata.append(el("span", "entry-no", "#" + entry.id));
    if (entry.alarm) {
      const alarm = entry.alarm;
      metadata.append(el("span", "alarm-indicator", [
        alarmTypes[alarm.type] || alarm.type,
        alarm.slot ? "слот " + alarm.slot : "",
        alarm.serial ? "№ " + serial(alarm.serial) : "",
        "×" + alarm.count,
      ].filter(Boolean).join(" · ")));
    }
    metadata.append(el("span", "", actor(entry.from) + " → " + actor(entry.to)), statusText(entry),
      time(entry.alarm?.at || entry.updated_at), el("span", "", "Ответы: " + entry.reply_count));
    const copy = el("div", "entry-copy", entry.text);
    link.replaceChildren(copy, metadata);
    if (parent.children[index] !== row) {
      const focused = document.activeElement === link;
      parent.insertBefore(row, parent.children[index] || null);
      if (focused) link.focus({ preventScroll: true });
    }
  }
  for (const row of old.values()) {
    if (document.activeElement === row.firstElementChild) $("section-heading").focus({ preventScroll: true });
    if (row.contains($("detail"))) $("inline-details").append($("detail"));
    row.remove();
  }
}
function annotateSelection() {
  for (const link of $("workspace").querySelectorAll("[data-entry]")) {
    if (Number(link.dataset.entry) === state.route.id) link.setAttribute("aria-current", "true");
    else link.removeAttribute("aria-current");
  }
}

function placeDetails(focus = false) {
  const dialog = $("detail-dialog");
  const detail = $("detail");
  const visible = !!state.route.id && !$("board").hidden;
  $("workspace").classList.toggle("with-detail", visible);
  if (!visible) {
    dialog.close();
    detail.hidden = true;
    return;
  }
  const selected = $("workspace").querySelector('[data-entry="' + state.route.id + '"]');
  const holder = selected?.parentElement || $("inline-details");
  const focused = detail.contains(document.activeElement) && document.activeElement;
  if (detail.parentElement !== holder) holder.append(detail);
  detail.hidden = false;
  dialog.close();
  renderDetail();
  if (focus) $("detail-title").focus({ preventScroll: true });
  else if (focused) focused.focus({ preventScroll: true });
}
function dismissDetail() {
  navigate({ ...state.route, id: null }, { replace: true });
}
async function loadDetail() {
  const id = state.route.id;
  if (!id) return;
  const seq = ++state.detailSeq;
  try {
    const entry = await api("/board/entries/" + id);
    if (seq !== state.detailSeq) return;
    state.entry = entry;
    state.detailFresh = true;
    report($("detail-state"), "");
  } catch (error) {
    if (seq !== state.detailSeq) return;
    state.detailFresh = false;
    const absent = error.status === 404;
    if (absent) {
      state.entry = null;
      recoverAction("Эта запись больше недоступна.");
    }
    report($("detail-state"), absent ? "Запись не найдена." : "Не удалось обновить запись. Повторите загрузку.");
  }
  renderDetail();
  updateConnection();
}
function addFact(list, key, value) {
  if (value === undefined || value === null || value === "") return;
  const row = el("div", "fact");
  const content = el("dd");
  if (value instanceof Node) content.append(value);
  else content.textContent = String(value);
  row.append(el("dt", "", key), content);
  list.append(row);
}
function newFacts(items) {
  const list = el("dl", "facts");
  for (const [key, value] of items) addFact(list, key, value);
  return list;
}
function renderDetail() {
  const entry = state.entry;
  const body = $("detail-body");
  const thread = $("thread");
  if (!entry) {
    $("detail-title").textContent = "Запись #" + state.route.id;
    if ($("detail-state").hidden) report($("detail-state"), "Загружаем запись…");
    body.replaceChildren();
    delete body.dataset.entry;
    thread.hidden = true;
    $("entry-actions").replaceChildren();
    return;
  }
  const signature = JSON.stringify(entry);
  if (body.dataset.entry !== signature) {
    body.dataset.entry = signature;
    $("detail-title").textContent = kinds[entry.kind] + " #" + entry.id;
    const facts = newFacts([
      ["Статус", statusText(entry)], ["От кого", actor(entry.from)], ["Кому", actor(entry.to)],
      ["Исполнитель", entry.claimed_by ? actor(entry.claimed_by) : null],
      ["Создано", formatDate(entry.created_at, detailDateFormat)],
      ["Обновлено", entry.updated_at !== entry.created_at ? formatDate(entry.updated_at, detailDateFormat) : null],
    ]);
    if (entry.alarm) {
      addFact(facts, "Тревога", alarmTypes[entry.alarm.type] || entry.alarm.type);
      addFact(facts, "Слот", entry.alarm.slot);
      addFact(facts, "Серийный номер", entry.alarm.serial ? serial(entry.alarm.serial) : null);
      addFact(facts, "Срабатывания", entry.alarm.count);
      addFact(facts, "Последнее срабатывание", entry.alarm.at ? formatDate(entry.alarm.at, detailDateFormat) : null);
    }
    const grant = entry.grant;
    const request = entry.request;
    const items = [facts, el("p", "full-text", entry.text)];
    if (request) {
      const labels = { slot_up: "Запуск слота", slot_restart: "Перезапуск mind", slot_down: "Остановка слота" };
      const params = [["Действие", labels[request.action] || request.action], ["Причина", request.why]];
      params.push(...Object.entries(request.args || {}));
      if (grant) {
        params.push(["Получатель доступа", actor(entry.from)],
          ["Доступ", (grant.kind === "secret" ? "Секрет " : "Сетевой ресурс ") + grant.what]);
      }
      items.push(el("h3", "", "Параметры запроса"), newFacts(params));
    }
    body.replaceChildren(...items);
    const comments = entry.thread || [];
    thread.hidden = !comments.length;
    $("thread-heading").textContent = "Обсуждение · " + comments.length;
    $("thread-body").replaceChildren(...comments.map((item) => {
      const reply = el("article", "reply");
      const meta = el("div", "entry-meta");
      meta.append(el("strong", "", actor(item.from)), time(item.at));
      if (item.status_change) {
        const { from, to } = item.status_change;
        meta.append(el("span", "", (statuses[from] || from) + " → " + (statuses[to] || to)));
      }
      reply.append(meta, el("p", "reply-text", item.text));
      return reply;
    }));
  }
  if (state.action && !state.pending) validateAction();
  renderActions();
  updateAction();
}
function renderActions() {
  const holder = $("entry-actions");
  const allowed = state.entry?.operator_actions || [];
  const signature = allowed.join("|");
  if (holder.dataset.actions !== signature || holder.childElementCount !== allowed.length) {
    holder.dataset.actions = signature;
    const focus = holder.contains(document.activeElement) ? document.activeElement.dataset.action : null;
    holder.replaceChildren(...allowed.map((key) => {
      const spec = actionSpecs[key];
      const control = button(spec.label, spec.primary ? "primary" : spec.danger ? "danger" : "");
      control.dataset.action = key;
      control.addEventListener("click", () => openAction(key));
      return control;
    }));
    if (focus) holder.querySelector('[data-action="' + focus + '"]')?.focus({ preventScroll: true });
  }
}
function field(label, control, suffix) {
  const wrapper = el("label", "", label);
  wrapper.append(control);
  if (suffix) wrapper.append(suffix);
  return wrapper;
}
function limit(area, count, max) {
  const chars = Array.from(area.value);
  if (chars.length > max) area.value = chars.slice(0, max).join("");
  count.textContent = Math.min(chars.length, max) + " из " + max;
}
function closeAction(restoreFocus = false) {
  const action = state.action;
  if (!action) return;
  if (action.secret) action.secret.value = "";
  state.action = null;
  $("action-form").replaceChildren();
  if (restoreFocus) $("entry-actions").querySelector('[data-action="' + action.key + '"]')?.focus();
}
function recoverAction(message) {
  if (!state.action) return;
  const hadFocus = $("action-form").contains(document.activeElement);
  const draft = state.action.area?.value || "";
  closeAction();
  if (draft) {
    const recovered = el("textarea");
    recovered.value = draft;
    recovered.readOnly = true;
    $("recovered").replaceChildren(field("Сохранённый текст для копирования", recovered));
    if (hadFocus) recovered.focus({ preventScroll: true });
  } else if (hadFocus) $("detail-title").focus({ preventScroll: true });
  report($("action-feedback"), message);
}
function validateAction() {
  const action = state.action;
  const entry = state.entry;
  if (!entry?.operator_actions.includes(action.key) ||
      (action.key !== "reply" && action.updated !== entry.updated_at)) {
    recoverAction("Запись изменилась: " + (entry ? statuses[entry.status] || entry.status : "недоступна") + ".");
  }
}
function openAction(key) {
  if (state.pending || !state.detailFresh || !state.listFresh || !state.entry?.operator_actions.includes(key)) return;
  if (state.action?.key === key) {
    state.action.form.querySelector("textarea, input, button").focus();
    return;
  }
  closeAction();
  report($("action-feedback"), "");
  const entry = state.entry;
  const spec = actionSpecs[key];
  const form = el("form", "stack action-form");
  const action = { key, form, updated: entry.updated_at, args: new Map() };
  form.append(el("h3", "", spec.label));
  if (key === "edit") {
    for (const [name, value] of Object.entries(entry.request.args || {})) {
      const input = el("input");
      input.value = value;
      action.args.set(name, input);
      form.append(field(name, input));
    }
  }
  if ((key === "approve" || key === "edit") && entry.grant) {
    const grant = entry.grant;
    form.append(el("p", "", actor(entry.from) + " получит доступ к " +
      (grant.kind === "secret" ? "секрету " : "ресурсу ") + grant.what + "."));
    if (grant.kind === "secret") {
      action.secret = el("input");
      action.secret.type = "password";
      action.secret.autocomplete = "new-password";
      action.secret.required = true;
      action.secret.addEventListener("input", updateAction);
      form.append(field("Значение секрета", action.secret));
    }
  }
  if (!spec.noText) {
    action.area = el("textarea");
    action.area.rows = 3;
    action.area.required = !!spec.required;
    const count = el("span", "count", "0 из 400");
    count.id = "reply-count";
    action.area.setAttribute("aria-describedby", count.id);
    action.area.addEventListener("input", () => {
      limit(action.area, count, 400);
      updateAction();
    });
    form.append(field(spec.required ? "Текст" : "Комментарий (необязательно)", action.area, count));
  }
  const controls = el("div", "button-row");
  action.send = button(spec.noText ? spec.label : "Отправить", "primary");
  action.send.type = "submit";
  const dismiss = button("Закрыть форму");
  dismiss.addEventListener("click", () => closeAction(true));
  controls.append(action.send, dismiss);
  form.append(controls);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAction(entry, action);
  });
  state.action = action;
  $("action-form").replaceChildren(form);
  updateAction();
  (action.secret || action.area || action.send).focus();
}
function updateAction() {
  const enabled = state.detailFresh && state.listFresh && !state.pending;
  for (const control of $("entry-actions").querySelectorAll("button")) {
    control.disabled = !enabled;
  }
  const action = state.action;
  if (!action) return;
  const spec = actionSpecs[action.key];
  action.send.disabled = !enabled || (spec.required && !action.area?.value.trim()) ||
    (!!action.secret && !action.secret.value.trim());
}
async function submitAction(entry, action) {
  if (state.action !== action || action.send.disabled) return;
  const base = "/board/entries/" + entry.id;
  const text = action.area?.value || "";
  const spec = actionSpecs[action.key];
  let path = base + "/transition";
  let body = { to_status: spec.target, text, expected_update: action.updated };
  if (action.key === "reply") {
    path = base + "/reply";
    body = { text };
  } else if (action.key === "revoke") {
    path = "/board/grants/revoke";
    body = { kind: entry.grant.kind, what: entry.grant.what, entry_id: entry.id };
  } else {
    if (action.key === "edit") body.args = Object.fromEntries([...action.args].map(([key, input]) => [key, input.value]));
    if (action.secret) body.secret = action.secret.value;
  }
  await sendMutation(path, body, {
    success: () => {
      if (state.action === action) closeAction();
      if (state.route.id === entry.id) {
        $("detail-title").focus({ preventScroll: true });
        report($("action-feedback"), "");
      }
      say("Действие выполнено.");
    },
    failure: (message, error) => {
      if (state.action !== action) return;
      if (error.status === 409) recoverAction("Запись изменилась. " + (state.entry ?
        "Текущий статус — " + (statuses[state.entry.status] || state.entry.status) + "." : ""));
      else {
        validateAction();
        if (state.action === action) report($("action-feedback"), message);
      }
    },
  });
}
async function sendMutation(path, body, { success, failure }) {
  if (state.pending || !state.listFresh) return;
  state.pending = true;
  updateConnection();
  try {
    const result = await api(path, { method: "POST", body });
    success(result);
    await refresh();
  } catch (error) {
    if (error.status === 401) {
      showLogin("Войдите снова для продолжения.");
      return;
    }
    state.listFresh = state.detailFresh = state.monitorFresh = false;
    updateConnection();
    await refresh();
    const message = error.network
      ? "Не удалось подтвердить отправку. Проверьте обновлённый список перед повтором."
      : error.status === 409 ? "Состояние изменилось. Проверьте текущие данные."
      : error.status === 422 ? "Проверьте введённые данные." : "Действие недоступно.";
    failure(message, error);
  } finally {
    state.pending = false;
    updateConnection();
  }
}

function renderRecipients() {
  const select = $("new-to");
  const message = $("new-kind").value === "message";
  if (message) {
    options(select, [["net", "Вся сеть"]], "net");
    return;
  }
  const original = select.value;
  const nodes = (state.view?.alive || []).map((brief) => [
    String(brief.serial), actor(String(brief.serial)) + " · " + brief.slot,
  ]);
  const choices = [["", "Выберите адресата"], ["last-order", "LO"], ["net", "Вся сеть"], ...nodes];
  let target = original;
  if (state.view && original && !choices.some(([key]) => key === original)) {
    target = "";
    state.targetLost = true;
    report($("compose-feedback"), "Адресат вышел из реестра. Выберите нового. Текст сохранён.");
  } else if (!state.view && original && !choices.some(([key]) => key === original)) choices.push([original, actor(original)]);
  options(select, choices, target);
  select.options[0].disabled = true;
}
function updateComposer() {
  const message = $("new-kind").value === "message";
  const submit = $("compose-form").querySelector('[type="submit"]');
  submit.textContent = message ? "Отправить сообщение" : "Создать задачу";
  submit.disabled = !state.listFresh || state.pending || !$("new-to").value || !$("new-text").value.trim();
}
function openComposer() {
  if (!state.listFresh || $("composer").open) return;
  renderRecipients();
  updateComposer();
  $("main").prepend($("composer"));
  $("composer").show();
  $("composer").scrollIntoView({block: "nearest"});
}
async function createEntry(event) {
  event.preventDefault();
  if ($("compose-form").querySelector('[type="submit"]').disabled) return;
  const body = { kind: $("new-kind").value, to: $("new-to").value, text: $("new-text").value };
  await sendMutation("/board/entries", body, {
    success: (entry) => {
      $("composer").close();
      $("compose-form").reset();
      $("new-to").value = "";
      limit($("new-text"), $("new-count"), 800);
      report($("compose-feedback"), "");
      state.targetLost = false;
      const section = entry.kind === "task" ? "tasks" : "events";
      navigate({ section, lane: "", status: "", to: "", id: entry.id }, { trigger: $("create") });
      say(kinds[entry.kind] + " #" + entry.id + " создана на доске.");
    },
    failure: (message) => {
      if (!state.targetLost) report($("compose-feedback"), message);
    },
  });
}

function renderComponent(title, facts, controls) {
  const panel = el("section", "panel");
  const heading = el("div", "panel-heading");
  heading.append(el("h3", "", title));
  const body = el("div", "panel-body");
  body.append(newFacts(facts));
  if (controls) body.append(controls);
  panel.append(heading, body);
  return panel;
}
function tier(value, yes, no) {
  return value === true ? el("span", "healthy", yes) : value === false ? no : "Нет данных";
}
function dockerHealth(value) {
  return { healthy: "Проверка пройдена", starting: "Проверка запускается", unhealthy: "Проверка не пройдена" }[value];
}
function slotControls(slot) {
  const holder = el("div", "button-row slot-actions");
  const slots = { slot_up: "Поднять", slot_restart: "Перезапустить mind", slot_down: "Остановить" };
  for (const [key, label] of Object.entries(slots)) {
    const control = button(label, key === "slot_down" ? "danger" : "");
    control.dataset.slot = slot;
    control.dataset.slotAction = key;
    control.setAttribute("aria-label", label + " · слот " + slot);
    control.addEventListener("click", () => {
      if (control.disabled) return;
      if (key === "slot_down" && !window.confirm("Остановить слот " + slot +
        "? Остановятся сестра, mind и тело. Живая сестра умрёт без завещания.")) return;
      sendMutation("/board/entries", {
        kind: "request", to: "operator", text: label + " · слот " + slot,
        request: { action: key, args: { slot }, why: "Запрос оператора с доски" },
      }, {
        success: (entry) => navigate({ ...state.route, id: entry.id }, { trigger: control }),
        failure: (message) => report($("infra-feedback"), message),
      });
    });
    holder.append(control);
  }
  return holder;
}
function renderNetwork() {
  const box = $("snapshot");
  const status = state.network;
  const data = status?.components;
  const freshness = !data ? "Нет данных" : status.freshness === "stale" || !state.monitorFresh ? "Устарели" : "Снимок обновлён";
  const focus = document.activeElement?.dataset;
  const focusKey = focus?.slot ? '[data-slot="' + focus.slot + '"][data-slot-action="' + focus.slotAction + '"]' : "";
  const summary = el("div", "monitor-summary");
  summary.append(el("p", state.monitorFresh && status?.freshness === "fresh" ? "healthy" : "muted",
    "Данные мониторинга: " + freshness + (status?.observed_at ? " · " + formatDate(status.observed_at) : "")));
  if (!state.monitorFresh && state.network) summary.append(el("p", "muted", "Не удалось обновить состояние сети."));
  if (data?.docker === false) summary.append(el("p", "muted", "Docker недоступен для проверки."));
  const grid = el("div", "monitor-grid");
  grid.append(
    renderComponent("LO", [["Процесс", tier(data?.lo.process, "Процесс LO запущен", "Процесс LO остановлен")],
      ["Контейнер", dockerHealth(data?.lo.health)]]),
    renderComponent("Голос", [
      ["TTS", data?.voice.reachable === false ? "Сервис недоступен" : tier(data?.voice.tts_ready, "Готов", "Не готов")],
      ["STT", data?.voice.reachable === false ? "Сервис недоступен" : tier(data?.voice.stt_ready, "Готов", "Не готов")],
      ["Бэкенд TTS", data?.voice.backend], ["Модель TTS", data?.voice.model], ["GPU", data?.voice.gpu],
    ]),
    renderComponent("Инфраструктура", [
      ["Исполнитель", data?.executor.busy && data.executor.running ? "Выполняет заявку" :
        tier(data?.executor.running, "Запущен", "Остановлен")],
      ["Контейнер", dockerHealth(data?.executor.health)],
    ]),
  );
  for (const slot of ["1", "2", "3"]) {
    const item = data?.slots?.[slot];
    const registry = status?.registry?.filter((brief) => brief.slot === "LastOrder0" + slot);
    const stopped = item?.mind.running === false && item?.node.running === false && item?.body.running === false;
    const runner = item?.runner;
    const body = runner?.body === "attached" ? el("span", "healthy", "Подключено") :
      runner?.body === "none" ? "Не подключено" : stopped ? "Остановлено" : "Нет данных";
    grid.append(renderComponent("Слот " + slot, [
      ["Сестра в реестре", !registry ? "Нет данных" : registry.length
        ? registry.map((brief) => actor(String(brief.serial))).join(", ") : "Отсутствует"],
      ["Состояние слота", stopped ? "Остановлен" :
        item?.mind.running || item?.node.running ? "Запущен" : item?.body.running ? "Запущено только тело" : "Нет данных"],
      ["mind", tier(item?.mind.running, "Запущен", "Остановлен")],
      ["Сестра", tier(item?.node.running, "Контейнер запущен", "Контейнер остановлен")],
      ["Тело", body],
      ["Жизнь в mind", runner?.term ? "Срок завершён" : runner?.dead ? "Тело погибло" :
        runner?.serial ? "#" + serial(runner.serial) : null],
    ], slotControls(slot)));
  }
  box.replaceChildren(summary, grid);
  if (focusKey) box.querySelector(focusKey)?.focus({ preventScroll: true });
  renderInfraSwitch();
}
async function loadNetwork() {
  const seq = ++state.statusSeq;
  try {
    const status = await api("/board/status");
    if (seq !== state.statusSeq) return;
    state.network = status;
    state.monitorFresh = true;
  } catch {
    if (seq !== state.statusSeq) return;
    state.monitorFresh = false;
  }
  renderNetwork();
}
function renderInfraSwitch() {
  const value = state.network?.executor_switch;
  $("infra-switch-state").textContent = !value ? "Настройка недоступна" :
    value.on ? "Приём заявок включён" : "Приём заявок выключен";
  report($("infra-switch-reason"), value?.reason);
  if (value) $("infra-toggle").textContent = value.on ? "Выключить приём" : "Включить приём";
  if (!value?.on && !$("infra-off-form").hidden) {
    $("infra-off-form").hidden = true;
    report($("infra-feedback"), "Приём заявок уже выключен.");
  }
  updateInfraControls();
}
function updateInfraControls() {
  const value = state.network?.executor_switch;
  const up = !!value && state.monitorFresh && state.listFresh && !state.pending;
  $("infra-toggle").disabled = !up;
  $("infra-off-form").querySelector('[type="submit"]').disabled = !up;
  for (const control of $("snapshot").querySelectorAll("[data-slot-action]")) {
    control.disabled = !up || state.network.freshness !== "fresh" || !state.network.components?.docker;
  }
}
async function toggleInfra(on) {
  if ($("infra-toggle").disabled) return;
  const reason = on ? "" : $("infra-reason").value;
  await sendMutation("/board/infra/switch", { on, reason }, {
    success: () => {
      $("infra-off-form").hidden = true;
      $("infra-reason").value = "";
      report($("infra-feedback"), "");
      say(on ? "Приём заявок включён." : "Приём заявок выключен.");
    },
    failure: (message) => report($("infra-feedback"), message),
  });
}

function init() {
  for (const key of ["overview", "history"]) {
    const link = linkFor(key);
    link.append(el("span", "nav-count"));
    $("sections").append(link);
  }
  document.addEventListener("click", (event) => {
    if (!sameTab(event)) return;
    const link = event.target.closest("a[data-section], a[data-entry]");
    if (!link) return;
    event.preventDefault();
    if (link.dataset.entry) {
      navigate({ ...state.route, id: Number(link.dataset.entry) }, { trigger: link });
    } else gotoSection(link.dataset.section, link.dataset.lane);
  });
  $("menu").addEventListener("click", () => {
    const menu = $("menu");
    menu.setAttribute("aria-expanded", String(menu.getAttribute("aria-expanded") !== "true"));
  });
  for (const [id, key] of [["lane", "lane"], ["filter-status", "status"], ["filter-to", "to"]]) {
    $(id).addEventListener("change", () => navigate({ ...state.route, [key]: $(id).value }));
  }
  $("filters").addEventListener("submit", (event) => event.preventDefault());
  let searchTimer;
  $("search").addEventListener("input", () => {
    state.q = $("search").value;
    state.listSeq++;
    state.pages = 1;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadView, 240);
  });
  $("more").addEventListener("click", () => {
    if (state.view?.next_cursor && !state.busy) loadView({ more: true });
  });
  $("retry").addEventListener("click", refresh);
  $("detail-retry").addEventListener("click", refresh);
  $("compose-retry").addEventListener("click", refresh);
  $("create").addEventListener("click", openComposer);
  $("compose-close").addEventListener("click", () => $("composer").close());
  $("compose-form").addEventListener("submit", createEntry);
  $("new-kind").addEventListener("change", () => {
    $("new-to").value = "";
    renderRecipients();
    updateComposer();
  });
  $("new-to").addEventListener("change", () => {
    state.targetLost = false;
    report($("compose-feedback"), "");
    updateComposer();
  });
  $("new-text").addEventListener("input", () => {
    limit($("new-text"), $("new-count"), 800);
    updateComposer();
  });
  $("detail-close").addEventListener("click", dismissDetail);
  $("detail-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    dismissDetail();
  });

  window.addEventListener("popstate", () => navigate(readRoute(), { pop: true }));
  window.addEventListener("online", refresh);
  window.addEventListener("offline", () => {
    state.connected = state.listFresh = state.detailFresh = state.monitorFresh = false;
    updateConnection();
  });
  $("infra-toggle").addEventListener("click", () => {
    if (state.network?.executor_switch?.on) {
      $("infra-off-form").hidden = false;
      $("infra-reason").focus();
    } else toggleInfra(true);
  });
  $("infra-off-form").addEventListener("submit", (event) => {
    event.preventDefault();
    toggleInfra(false);
  });
  $("infra-off-cancel").addEventListener("click", () => {
    $("infra-off-form").hidden = true;
    $("infra-toggle").focus();
  });
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const control = $("login-form").querySelector('button[type="submit"]');
    control.disabled = true;
    try {
      await api("/board/login", { method: "POST", body: { password: $("login-password").value } });
      setBoardAvailable();
      refresh();
    } catch (error) {
      report($("login-error"), error.status === 401 ? "Пароль не принят." : "Не удалось связаться с доской.");
    } finally {
      control.disabled = false;
    }
  });
  $("logout").addEventListener("click", async () => {
    try {
      await api("/board/logout", { method: "POST" });
      state.entry = state.view = state.network = state.seenAt = null;
      $("compose-form").reset();
      limit($("new-text"), $("new-count"), 800);
      $("recovered").replaceChildren();
      showLogin();
    } catch {
      say("Не удалось выйти. Повторите попытку.");
    }
  });
  // Older releases stored the password in the tab. Authentication now uses only the login cookie.
  try { sessionStorage.removeItem("lo_board_key"); } catch { /* Storage is optional. */ }
  setInterval(() => {
    if (!$("login").hidden || document.hidden) return;
    if (!state.connected || $("composer").open) refresh();
    else if (state.route.section === "network") loadNetwork();
  }, 10000);
  $("board").hidden = false;
  renderView();
  refresh();
}
init();
