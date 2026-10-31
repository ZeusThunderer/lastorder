"use strict";
// The operator's board (T-049, docs/architecture/TASKBOARD.md section 12).
// Vanilla JS, no build step, no external resources, same-origin relative paths.
// Agent-supplied text reaches the DOM through textContent alone — never through
// a markup sink — so a hostile entry can only render as plain text.

const ENTRIES_LIMIT = 200;

const state = { entries: [], alive: [], filter: "" };

const KIND_LABEL = { task: "задача", request: "запрос", message: "сообщение", alarm: "тревога" };
const TERMINAL = new Set([
  "done", "failed", "declined", "canceled", "approved", "denied", "answered", "acked",
]);

// --- small DOM helpers -----------------------------------------------------------------

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function liveSerials() {
  return new Set(state.alive.map((brief) => String(brief.serial)));
}

// A board name is `operator` | `last-order` | `net` | `system` | `runner:<slot>`
// | a plain digit string (a node serial). Serials carry no name preset (ADR-018);
// the slot label from /registry/alive is infrastructure, not the persona name.
function whoTag(who) {
  if (who === "operator") return { label: "оператор", dead: false };
  if (who === "last-order") return { label: "Last Order", dead: false };
  if (who === "net") return { label: "net", dead: false };
  if (who === "system") return { label: "system", dead: false };
  if (typeof who === "string" && who.startsWith("runner:")) {
    return { label: who, dead: false };
  }
  if (typeof who === "string" && /^\d+$/.test(who)) {
    const brief = state.alive.find((b) => String(b.serial) === who);
    return { label: brief ? who + " · " + brief.slot : who, dead: !brief };
  }
  return { label: String(who), dead: false };
}

function nameSpan(who) {
  const tag = whoTag(who);
  return el("span", tag.dead ? "dead" : null, tag.label);
}

function fmtTime(iso) {
  const at = new Date(iso);
  if (isNaN(at.getTime())) return "";
  return at.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

// --- API --------------------------------------------------------------------------------

async function api(path, method, body) {
  const options = method === "GET"
    ? {}
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
  const response = await fetch(path, options);
  if (!response.ok) {
    let note = response.status + " " + response.statusText;
    try {
      const data = await response.json();
      if (data && data.error) note = data.error.kind + ": " + data.error.message;
    } catch (notJson) {
      // keep the status line
    }
    throw new Error(note);
  }
  return response.json();
}

async function doAction(path, body) {
  try {
    await api(path, "POST", body);
    await refresh(); // the SSE event will call it again; better twice than late
    return true;
  } catch (error) {
    toast(String(error.message || error));
    return false; // the caller keeps the form with its typed text
  }
}

let toastTimer = null;

function toast(message) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 7000);
}

// --- data -------------------------------------------------------------------------------

async function refresh() {
  try {
    const [entries, alive] = await Promise.all([
      api("/board/entries?limit=" + ENTRIES_LIMIT, "GET"),
      api("/registry/alive", "GET"),
    ]);
    state.entries = entries;
    state.alive = alive;
    await loadThreads(visibleEntries());
    render();
  } catch (error) {
    toast(String(error.message || error));
  }
}

// TASKBOARD.md section 9: the listing carries no threads — only the one-entry call
// does. The page fetches the thread of every visible entry in parallel (loopback,
// one debounced burst per update); a `with_thread` param on the list would spare
// the round-trips, but runtime/hive/board.py is outside this task's scope.
async function loadThreads(entries) {
  const threads = await Promise.all(entries.map((entry) =>
    api("/board/entries/" + entry.id, "GET")
      .then((full) => full.thread || [])
      .catch(() => [])
  ));
  entries.forEach((entry, i) => { entry.thread = threads[i]; });
}

function visibleEntries() {
  return state.entries.filter((entry) => !state.filter || entry.to === state.filter);
}

let refreshTimer = null;

function scheduleRefresh() {
  if (refreshTimer) return; // one refetch per SSE burst
  refreshTimer = setTimeout(() => { refreshTimer = null; refresh(); }, 250);
}

function openStream() {
  const conn = document.getElementById("conn");
  const source = new EventSource("/board/stream");
  source.onopen = () => { conn.textContent = "живое обновление"; conn.classList.remove("off"); };
  source.onerror = () => {
    conn.textContent = "связь потеряна, переподключение…";
    conn.classList.add("off");
  };
  source.addEventListener("board", scheduleRefresh);
}

// SSE carries board changes alone: a closed life (retire) changes liveness — who may
// be addressed, which cards go gray — without touching the board, so no event comes.
// A light poll re-renders only when the alive set actually changed.
function watchLiveness() {
  setInterval(async () => {
    try {
      const alive = await api("/registry/alive", "GET");
      if (JSON.stringify(alive) !== JSON.stringify(state.alive)) {
        state.alive = alive;
        render();
      }
    } catch (error) {
      toast(String(error.message || error)); // the next tick tries again
    }
  }, 10000);
}

// --- render -----------------------------------------------------------------------------

function render() {
  // An update must not eat the operator's open inline forms: the typed deny reason,
  // a needs_input answer, edited args. The form elements are carried into the fresh
  // cards (moving a DOM node keeps its textarea state).
  const openForms = new Map();
  for (const form of document.querySelectorAll(".entry .inline-form")) {
    const card = form.closest(".entry");
    if (card) openForms.set(Number(card.dataset.id), form);
  }
  buildFilter();
  buildAddressees();
  const visible = visibleEntries();
  const columns = { task: [], request: [], info: [] };
  for (const entry of visible) {
    if (entry.kind === "task") columns.task.push(entry);
    else if (entry.kind === "request") columns.request.push(entry);
    else columns.info.push(entry);
  }
  fillColumn("col-task", columns.task, openForms);
  fillColumn("col-request", columns.request, openForms);
  fillColumn("col-info", columns.info, openForms);
}

function fillColumn(id, entries, openForms) {
  const cards = document.querySelector("#" + id + " .cards");
  const nodes = entries.map((entry) => entryCard(entry, openForms));
  if (!nodes.length) nodes.push(el("div", "empty", "пусто"));
  cards.replaceChildren(...nodes);
}

function entryCard(entry, openForms) {
  const card = el("article", "entry");
  card.dataset.id = entry.id;
  const live = liveSerials();
  // the whole card goes gray when a node it names is dead (TASKBOARD.md 12);
  // the individual name spans gray out the same way inside the route line
  const namesDead = [entry.from, entry.to, entry.claimed_by].some(
    (who) => typeof who === "string" && /^\d+$/.test(who) && !live.has(who)
  );
  if (namesDead) card.classList.add("dead");
  if (TERMINAL.has(entry.status)) card.classList.add("terminal");

  const head = el("div", "head");
  head.append(el("span", "id", "#" + entry.id));
  head.append(el("span", null, KIND_LABEL[entry.kind] || entry.kind));
  head.append(el("span", "badge " + entry.status, entry.status));
  head.append(el("span", "time", fmtTime(entry.created_at)));
  card.append(head);

  const route = el("div", "route");
  route.append(nameSpan(entry.from), document.createTextNode(" → "), nameSpan(entry.to));
  if (entry.readdressed_from) {
    route.append(document.createTextNode(" (был адресат: "), nameSpan(entry.readdressed_from),
      document.createTextNode(")"));
  }
  card.append(route);

  if (entry.claimed_by) {
    card.append(el("div", "claimer", "взята: " + whoTag(entry.claimed_by).label));
  }

  card.append(el("p", "text", entry.text));

  if (entry.kind === "request" && entry.request) {
    card.append(requestBox(entry.request));
  }
  if (entry.kind === "alarm" && entry.alarm) {
    card.append(alarmBox(entry.alarm));
  }
  if (entry.thread && entry.thread.length) {
    card.append(threadBox(entry.thread));
  }
  // the carried form (an update re-rendered the cards around the operator's typing)
  // rides along only while its action still exists on the card (review R2-1)
  const actions = actionsRow(entry, openForms ? openForms.get(entry.id) : null);
  card.append(actions.row);
  if (actions.form) card.append(actions.form);
  return card;
}

function requestBox(request) {
  const box = el("div", "args");
  box.append(el("span", "arg", "action: " + (request.action || "")));
  for (const key of Object.keys(request.args || {})) {
    const arg = el("span", "arg");
    arg.append(el("span", "k", key + ": "), document.createTextNode(String(request.args[key])));
    box.append(arg);
  }
  if (request.grant) {
    box.append(el("span", "arg grant-line",
      "grant: " + request.grant.kind + " " + request.grant.what));
  }
  if (request.why) box.append(el("span", "arg why", "почему: " + request.why));
  return box;
}

function alarmBox(alarm) {
  const bits = [String(alarm.type || "")];
  if (alarm.count > 1) bits.push("×" + alarm.count);
  if (alarm.slot) bits.push("slot " + alarm.slot);
  if (alarm.serial) bits.push("serial " + alarm.serial);
  return el("div", "alarm-meta", bits.join(" · "));
}

function threadBox(thread) {
  const box = el("div", "thread");
  for (const reply of thread) {
    const row = el("div", "reply");
    const meta = el("span", "meta");
    meta.append(document.createTextNode(whoTag(reply.from).label + " · " + fmtTime(reply.at) + "  "));
    row.append(meta);
    if (reply.status_change) {
      row.append(el("span", "change",
        reply.status_change.from + " → " + reply.status_change.to + "  "));
    }
    row.append(el("span", "rtext", reply.text));
    box.append(row);
  }
  return box;
}

// --- actions (one request each; the server decides the rights) --------------------------

function actionsRow(entry, carriedForm) {
  const row = el("div", "actions");

  const button = (label, cls, opens) => {
    const node = el("button", cls, label);
    node.type = "button";
    node.dataset.opens = opens; // a carried form survives while its action does (R2-1)
    row.append(node);
    return node;
  };

  let openForm = null;

  const toggle = (form) => {
    if (openForm) openForm.remove();
    if (openForm === form) { openForm = null; return; }
    openForm = form;
    row.after(form);
  };

  const wire = (node, buildForm) =>
    node.addEventListener("click", () => toggle(buildForm()));

  if (entry.kind === "task") {
    if (["open", "taken", "needs_input"].includes(entry.status)) {
      wire(button("отменить", "danger", "canceled"),
        () => transitionForm(entry, "canceled", "отменить задачу"));
    }
    if (entry.status === "needs_input") {
      wire(button("ответить", "primary", "taken"),
        () => transitionForm(entry, "taken", "ответить и вернуть во взятые", { needText: true }));
    }
  }
  if (entry.kind === "request" && entry.status === "open") {
    const allowed = (entry.request && entry.request.allowed)
      || ["approve", "deny", "respond", "edit"];
    if (allowed.includes("approve")) {
      wire(button("одобрить", "primary", "approved"),
        () => transitionForm(entry, "approved", "одобрить"));
    }
    if (allowed.includes("edit")) {
      wire(button("правка и одобрить", "primary", "approved"),
        () => transitionForm(entry, "approved", "одобрить с правкой", { editArgs: true }));
    }
    if (allowed.includes("deny")) {
      wire(button("отклонить", "danger", "denied"),
        () => transitionForm(entry, "denied", "отклонить", { needText: true }));
    }
    if (allowed.includes("respond")) {
      wire(button("ответить", null, "answered"),
        () => transitionForm(entry, "answered", "ответить", { needText: true }));
    }
  }
  if (entry.kind === "alarm" && entry.status === "open") {
    const ack = el("button", "primary", "снять");
    ack.type = "button";
    ack.addEventListener("click", () => {
      doAction("/board/entries/" + entry.id + "/transition", { to_status: "acked" });
    });
    row.append(ack);
  }
  const grant = entry.request && entry.request.grant;
  if (entry.kind === "request" && entry.status === "approved" && grant) {
    // TASKBOARD.md 5.3: the entry keeps `approved`, the grant record flips to revoked
    const revoke = el("button", "danger", "отозвать грант");
    revoke.type = "button";
    revoke.addEventListener("click", () => {
      doAction("/grants/revoke", { kind: grant.kind, what: grant.what });
    });
    row.append(revoke);
  }
  wire(button("реплика", null, "reply"), () => replyForm(entry));
  // the carried form stays open on the fresh card only if the entry still offers
  // its move: a status change may have removed the button that opened it (R2-1)
  if (carriedForm && row.querySelector(`[data-opens="${carriedForm.dataset.opens}"]`)) {
    openForm = carriedForm;
  }
  return { row, form: openForm };
}

// A transition form: optional or required text, optionally the corrected args
// (approve-with-edit, TASKBOARD.md 5.2 — the agent receives exactly these args).
function transitionForm(entry, toStatus, label, options) {
  const opts = options || {};
  const form = el("form", "inline-form");
  form.dataset.opens = toStatus; // matched against the fresh card's buttons (R2-1)
  const inputs = { text: null, args: {}, secret: null };

  if (opts.editArgs && entry.request) {
    for (const key of Object.keys(entry.request.args || {})) {
      const row = el("label", "arg-row");
      row.append(el("span", "k", key + ":"));
      const input = document.createElement("input");
      input.type = "text";
      input.value = String(entry.request.args[key]);
      inputs.args[key] = input;
      row.append(input);
      form.append(row);
    }
  }
  const grant = entry.request && entry.request.grant;
  if (toStatus === "approved" && grant && grant.kind === "secret") {
    // T-046: the operator types the secret here; it reaches the vault alone
    const row = el("label", "arg-row");
    row.append(el("span", "k", "значение секрета:"));
    const input = document.createElement("input");
    input.type = "password";
    input.autocomplete = "new-password";
    input.required = true;
    inputs.secret = input;
    row.append(input);
    form.append(row);
  }
  if (toStatus !== "acked") {
    const area = document.createElement("textarea");
    area.maxLength = 400;
    if (opts.needText) area.required = true;
    area.placeholder = opts.needText ? "обязательный текст" : "текст (необязательно)";
    inputs.text = area;
    form.append(area);
  }

  const send = el("button", "primary", label);
  send.type = "submit";
  const hide = el("button", "cancel-inline", "скрыть");
  hide.type = "button";
  hide.addEventListener("click", () => form.remove());
  form.append(send, hide);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = { to_status: toStatus };
    if (inputs.text && inputs.text.value.trim()) body.text = inputs.text.value;
    if (inputs.secret) body.secret = inputs.secret.value;
    if (opts.editArgs) {
      body.args = {};
      for (const key of Object.keys(inputs.args)) body.args[key] = inputs.args[key].value;
    }
    if (await doAction("/board/entries/" + entry.id + "/transition", body)) {
      form.remove(); // sent: closed; on a refusal the typed text stays (R2-1)
    }
  });
  return form;
}

function replyForm(entry) {
  const form = el("form", "inline-form");
  form.dataset.opens = "reply"; // the reply button lives on every card (R2-1)
  const area = document.createElement("textarea");
  area.maxLength = 400;
  area.required = true;
  area.placeholder = "реплика (до 400 символов)";
  form.append(area);
  const send = el("button", "primary", "отправить");
  send.type = "submit";
  const hide = el("button", "cancel-inline", "скрыть");
  hide.type = "button";
  hide.addEventListener("click", () => form.remove());
  form.append(send, hide);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (await doAction("/board/entries/" + entry.id + "/reply", { text: area.value })) {
      form.remove(); // sent: closed; on a refusal the typed text stays (R2-1)
    }
  });
  return form;
}

// --- the top bar: filter and the new entry form -----------------------------------------

function buildFilter() {
  const select = document.getElementById("filter");
  const keep = state.filter;
  const options = [{ value: "", label: "все адресаты" }];
  for (const brief of state.alive) {
    options.push({ value: String(brief.serial), label: brief.serial + " · " + brief.slot });
  }
  options.push({ value: "net", label: "net" });
  options.push({ value: "last-order", label: "Last Order" });
  select.replaceChildren(...options.map((o) => new Option(o.label, o.value)));
  state.filter = options.some((o) => o.value === keep) ? keep : "";
  select.value = state.filter;
}

function buildAddressees() {
  const toSelect = document.getElementById("new-to");
  const previous = toSelect.value; // an update while the operator types must not retarget it
  const options = [];
  if (document.getElementById("new-kind").value === "task") {
    for (const brief of state.alive) {
      options.push({ value: String(brief.serial), label: brief.serial + " · " + brief.slot });
    }
    options.push({ value: "net", label: "net" });
    options.push({ value: "last-order", label: "Last Order" });
  } else {
    // the rights matrix (section 3.3): an operator message goes to the net alone
    options.push({ value: "net", label: "net" });
  }
  toSelect.replaceChildren(...options.map((o) => new Option(o.label, o.value)));
  if (options.some((o) => o.value === previous)) toSelect.value = previous;
}

// --- init (the script tag is deferred: the DOM is ready) --------------------------------

function init() {
  document.getElementById("filter").addEventListener("change", (event) => {
    state.filter = event.target.value;
    render();
  });
  document.getElementById("new-kind").addEventListener("change", buildAddressees);
  document.getElementById("new-entry").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = {
      kind: document.getElementById("new-kind").value,
      to: document.getElementById("new-to").value,
      text: document.getElementById("new-text").value,
    };
    try {
      await api("/board/entries", "POST", body);
      document.getElementById("new-text").value = "";
      await refresh();
    } catch (error) {
      toast(String(error.message || error));
    }
  });
  refresh();
  openStream();
  watchLiveness();
}

init();
