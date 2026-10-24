/* Boxganizer web UI.
 *
 * Left pane: search, the list, and the New box button. Main pane: the box that
 * is selected, drawn by the view registered for its type — a type without a view
 * of its own inherits the nearest ancestor's, which mirrors the class hierarchy
 * in boxganizer/boxtypes.
 *
 * Display mode (icon or content) and every other view setting live in the box's
 * own `config` column, so they come back in another browser.
 */

const el = (id) => document.getElementById(id);

const state = {
  types: new Map(), // type key -> descriptor from /api/types
  boxes: [], // what the left pane shows right now
  current: null, // the box on screen
  trail: [], // boxes walked through to get here, oldest first
  mode: "icon", // "icon" | "content" for the current box
};

/* ------------------------------------------------------------------ api ---- */

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      const detail = body && body.detail;
      reason = (detail && (detail.reason || detail.error)) || (typeof detail === "string" ? detail : reason);
    } catch {
      /* a body that is not JSON says nothing more useful than the status */
    }
    throw new Error(reason);
  }
  return response.status === 204 ? null : response.json();
}

const api = {
  types: () => request("/api/types"),
  boxes: () => request("/api/boxes"),
  search: (term) => request(`/api/search?q=${encodeURIComponent(term)}`),
  box: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}`),
  children: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/children`),
  create: (payload) => request("/api/boxes", { method: "POST", body: JSON.stringify(payload) }),
  setConfig: (ref, config) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/config`, {
      method: "PUT",
      body: JSON.stringify({ config }),
    }),
  link: (parent, child) =>
    request(`/api/boxes/${encodeURIComponent(parent)}/children`, {
      method: "POST",
      body: JSON.stringify({ child }),
    }),
  rawUrl: (ref) => `/api/boxes/${encodeURIComponent(ref)}/raw`,
};

/* ------------------------------------------------------------- type info --- */

const typeOf = (box) => state.types.get(box.box_type);

/** This type and its ancestors, nearest first — the view lookup order. */
const lineage = (box) => [box.box_type, ...((typeOf(box) || {}).ancestors || [])];

const setting = (box, key, fallback) => {
  const value = (box.config || {})[key];
  return value === undefined || value === null ? fallback : value;
};

/* ------------------------------------------------------------- registry ---- */

const views = new Map();
const configForms = new Map();

function registerView(typeKey, view) {
  views.set(typeKey, view);
}

/** Style rules a view needs at runtime, added once when it registers. */
function addStyle(css) {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.append(style);
}

function viewFor(box) {
  for (const key of lineage(box)) {
    if (views.has(key)) return views.get(key);
  }
  return views.get("*");
}

function formFor(typeKey) {
  for (const key of [typeKey, ...((state.types.get(typeKey) || {}).ancestors || [])]) {
    if (configForms.has(key)) return configForms.get(key);
  }
  return null;
}

/* ------------------------------------------------------------- feedback ---- */

let statusTimer = null;

function status(message, isError = false) {
  const target = el("status");
  target.textContent = message || "";
  target.classList.toggle("error", isError);
  clearTimeout(statusTimer);
  if (message && !isError) statusTimer = setTimeout(() => (target.textContent = ""), 4000);
}

/* ------------------------------------------------------------ navigation --- */

async function openBox(ref, options = {}) {
  try {
    const box = await api.box(ref);
    // A child opened from a view extends the path; anything else starts a new one.
    state.trail = options.trail ? [...options.trail, box] : [box];
    state.current = box;
    state.mode = options.mode || setting(box, "display", "icon");
    renderMain();
    renderBreadcrumb();
    markSelected();
  } catch (error) {
    status(`${error.message}`, true);
  }
}

/** Write a view setting into the box's config column and re-render. */
async function saveSetting(key, value, { rerender = true } = {}) {
  const config = { ...(state.current.config || {}), [key]: value };
  state.current = { ...state.current, config };
  try {
    await api.setConfig(state.current.uuid, config);
    if (rerender) renderMain();
  } catch (error) {
    status(`could not save ${key}: ${error.message}`, true);
  }
}

async function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll("#mode-toggle button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  });
  renderMain();
  await saveSetting("display", mode, { rerender: false });
}

/* ------------------------------------------------------------ main pane ---- */

const context = {
  open: openBox,
  status,
  saveSetting,
  mode: () => state.mode,
  reload: () => openBox(state.current.uuid, { mode: state.mode }),
  showMenu,
};

function renderMain() {
  const canvas = el("canvas");
  canvas.replaceChildren();
  const box = state.current;
  if (!box) {
    canvas.append(emptyMessage("Nothing selected. Pick a box on the left."));
    return;
  }
  document.querySelectorAll("#mode-toggle button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
  });
  const view = viewFor(box);
  if (!view) {
    canvas.append(emptyMessage(`No view for type ${box.box_type}.`));
    return;
  }
  view.render(canvas, box, context, state.mode);
}

function emptyMessage(text) {
  const paragraph = document.createElement("p");
  paragraph.className = "muted empty-message";
  paragraph.textContent = text;
  return paragraph;
}

function renderBreadcrumb() {
  const nav = el("breadcrumb");
  nav.replaceChildren();
  state.trail.forEach((box, index) => {
    if (index) nav.append(document.createTextNode("›"));
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${box.icon} ${box.name}`;
    button.dataset.uuid = box.uuid;
    const last = index === state.trail.length - 1;
    button.setAttribute("aria-current", String(last));
    if (!last) button.addEventListener("click", () => openTo(index));
    nav.append(button);
  });
  el("up").disabled = !canGoUp();
}

/** True when there is somewhere above the current box to go. */
function canGoUp() {
  return state.trail.length > 1 || !!(state.current && (state.current.parents || []).length);
}

function openTo(index) {
  const target = state.trail[index];
  openBox(target.uuid, { trail: state.trail.slice(0, index), mode: state.mode });
}

async function goUp() {
  if (state.trail.length > 1) {
    openTo(state.trail.length - 2);
    return;
  }
  const parents = (state.current && state.current.parents) || [];
  if (parents.length) await openBox(parents[0].uuid, { mode: state.mode });
}

/* ------------------------------------------------------------ left pane ---- */

async function refreshSide() {
  const term = el("search").value.trim();
  try {
    state.boxes = term ? await api.search(term) : await api.boxes();
  } catch (error) {
    status(error.message, true);
    return;
  }
  renderSide();
}

function renderSide() {
  const list = el("box-list");
  list.replaceChildren();
  state.boxes.forEach((box) => {
    const item = document.createElement("li");
    item.dataset.uuid = box.uuid;
    item.draggable = true; // a box can be dragged onto a grid cell
    item.setAttribute("role", "option");
    const glyph = document.createElement("span");
    glyph.textContent = box.icon;
    const name = document.createElement("span");
    name.textContent = box.name;
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = box.box_type;
    item.append(glyph, name, kind);
    item.addEventListener("click", () => openBox(box.uuid));
    item.addEventListener("dblclick", () => openBox(box.uuid, { mode: "content" }));
    item.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", box.uuid);
      event.dataTransfer.effectAllowed = "move";
    });
    item.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      showMenu(event.clientX, event.clientY, [
        { label: "Open", run: () => openBox(box.uuid, { mode: "content" }) },
        { label: "Show as icon", run: () => openBox(box.uuid, { mode: "icon" }) },
      ]);
    });
    list.append(item);
  });
  el("side-status").textContent = `${state.boxes.length} box${state.boxes.length === 1 ? "" : "es"}`;
  markSelected();
}

function markSelected() {
  document.querySelectorAll("#box-list li").forEach((item) => {
    item.setAttribute("aria-selected", String(state.current && item.dataset.uuid === state.current.uuid));
  });
}

/* ---------------------------------------------------------- context menu --- */

function showMenu(x, y, items) {
  const menu = el("context-menu");
  menu.replaceChildren();
  items.filter(Boolean).forEach((item) => {
    const entry = document.createElement("li");
    if (item.separator) {
      entry.setAttribute("role", "separator");
    } else {
      entry.setAttribute("role", "menuitem");
      entry.textContent = item.label;
      entry.addEventListener("click", () => {
        hideMenu();
        item.run();
      });
    }
    menu.append(entry);
  });
  menu.hidden = false;
  menu.style.left = `${Math.min(x, window.innerWidth - menu.offsetWidth - 8)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - menu.offsetHeight - 8)}px`;
}

function hideMenu() {
  el("context-menu").hidden = true;
}

/* ------------------------------------------------------------- new box ----- */

function fillTypeChoices() {
  const select = el("nb-type");
  select.replaceChildren();
  const selectable = [...state.types.values()].filter((type) => !type.abstract);
  const groups = new Map();
  selectable.forEach((type) => {
    const branch = (type.ancestors || [])[0] || "box";
    if (!groups.has(branch)) groups.set(branch, []);
    groups.get(branch).push(type);
  });
  groups.forEach((types, branch) => {
    const group = document.createElement("optgroup");
    group.label = branch;
    types.forEach((type) => {
      const option = document.createElement("option");
      option.value = type.key;
      option.textContent = `${type.icon} ${type.label}`;
      group.append(option);
    });
    select.append(group);
  });
}

function fillParentChoices() {
  const select = el("nb-parent");
  select.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "(no parent)";
  select.append(none);
  state.boxes.forEach((box) => {
    const option = document.createElement("option");
    option.value = box.uuid;
    option.textContent = `${box.icon} ${box.name}`;
    select.append(option);
  });
  const root = state.boxes.find((box) => box.box_type === "root");
  if (root) select.value = root.uuid;
}

/** Type-specific fields. Views add theirs with `registerConfigForm`. */
function registerConfigForm(typeKey, form) {
  configForms.set(typeKey, form);
}

function renderConfigFields() {
  const holder = el("nb-config");
  holder.replaceChildren();
  const typeKey = el("nb-type").value;
  el("nb-summary").textContent = (state.types.get(typeKey) || {}).summary || "";
  const form = formFor(typeKey);
  if (form) {
    form.fields.forEach((field) => {
      const label = document.createElement("label");
      label.textContent = field.label;
      const input = document.createElement("input");
      input.name = `config:${field.key}`;
      input.type = field.type || "number";
      if (field.default !== undefined) input.value = field.default;
      label.append(input);
      holder.append(label);
    });
  }
  const extra = document.createElement("label");
  extra.textContent = "More config (JSON)";
  const area = document.createElement("textarea");
  area.name = "config:json";
  area.value = "{}";
  extra.append(area);
  holder.append(extra);
}

function collectConfig() {
  const config = {};
  const form = formFor(el("nb-type").value);
  (form ? form.keys : []).forEach((key) => {
    const input = el("nb-config").querySelector(`[name="config:${key}"]`);
    if (input && input.value !== "") {
      config[key] = input.type === "number" ? Number(input.value) : input.value;
    }
  });
  const json = el("nb-config").querySelector('[name="config:json"]').value.trim();
  if (json) {
    let parsed;
    try {
      parsed = JSON.parse(json);
    } catch {
      throw new Error("the config JSON is not valid");
    }
    Object.assign(config, parsed);
  }
  return config;
}

async function submitNewBox(event) {
  event.preventDefault();
  const value = (id) => el(id).value.trim();
  const payload = {
    name: value("nb-name"),
    box_type: el("nb-type").value,
    description: value("nb-description"),
    resource_uri: value("nb-resource") || null,
    parent: value("nb-parent") || null,
    config: {},
  };
  try {
    payload.config = collectConfig();
  } catch (error) {
    status(error.message, true);
    return;
  }
  try {
    const created = await api.create(payload);
    el("new-box-modal").close();
    el("new-box-form").reset();
    await refreshSide();
    await openBox(created.uuid, { mode: "content" });
    status(`created ${created.name}`);
  } catch (error) {
    status(error.message, true);
  }
}

/* ---------------------------------------------------------------- init ----- */

async function init() {
  try {
    const types = await api.types();
    state.types = new Map(types.map((type) => [type.key, type]));
  } catch (error) {
    status(`cannot reach the server: ${error.message}`, true);
    return;
  }

  fillTypeChoices();
  el("search").addEventListener("input", () => refreshSide());
  el("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    refreshSide();
  });
  el("mode-toggle").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-mode]");
    if (button && state.current) setMode(button.dataset.mode);
  });
  el("up").addEventListener("click", goUp);
  el("new-box").addEventListener("click", () => {
    fillParentChoices();
    renderConfigFields();
    el("new-box-modal").showModal();
    el("nb-name").focus();
  });
  el("nb-cancel").addEventListener("click", () => el("new-box-modal").close());
  el("nb-type").addEventListener("change", renderConfigFields);
  el("new-box-form").addEventListener("submit", submitNewBox);
  document.addEventListener("click", hideMenu);
  window.addEventListener("blur", hideMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideMenu();
  });

  await refreshSide();
  const start = state.boxes.find((box) => box.box_type === "root") || state.boxes[0];
  if (start) await openBox(start.uuid);
}

/* The icon view is the fallback for every type that has nothing better yet. */
registerView("*", {
  render(canvas, box, ctx, mode) {
    const wrap = document.createElement("div");
    wrap.className = "icon-view";
    wrap.dataset.boxType = box.box_type;
    const glyph = document.createElement("div");
    glyph.className = "glyph";
    glyph.textContent = box.icon;
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = box.name;
    wrap.append(glyph, name);
    if (box.description) {
      const caption = document.createElement("p");
      caption.className = "caption muted";
      caption.textContent = box.description;
      wrap.append(caption);
    }
    if (mode === "content") {
      // A type without a content view still says which box you are looking at.
      const note = document.createElement("p");
      note.className = "caption muted";
      note.textContent = `No content view for type ${box.box_type}; showing its icon.`;
      wrap.append(note);
    }
    canvas.append(wrap);
  },
});

/* Extension points, reachable from the console and from tests. */
const boxganizer = { api, state, registerView, registerConfigForm, addStyle, openBox, goUp, status };
window.boxganizer = boxganizer;

init();
