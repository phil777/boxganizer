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
  pendingCell: null, // cell the modal should place the new box in
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
  unlink: (ref, child) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/children/${encodeURIComponent(child)}`, {
      method: "DELETE",
    }),
  gridPlace: (ref, child, cell) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/grid/place`, {
      method: "POST",
      body: JSON.stringify({ child, cell }),
    }),
  gridUnplace: (ref, child) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/grid/unplace`, {
      method: "POST",
      body: JSON.stringify({ child }),
    }),
  gridResize: (ref, rows, columns) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/grid/resize`, {
      method: "POST",
      body: JSON.stringify({ rows, columns }),
    }),
  rawText: async (ref) => {
    const response = await fetch(api.rawUrl(ref));
    if (!response.ok) {
      let reason = `HTTP ${response.status}`;
      try {
        const detail = (await response.json()).detail;
        reason = detail?.reason || detail?.error || reason;
      } catch {
        /* nothing structured to say */
      }
      throw new Error(reason);
    }
    return response.text();
  },
};

/* ------------------------------------------------------------- type info --- */

const typeOf = (box) => state.types.get(box.box_type);

const isRootType = (box) => !!(typeOf(box) || {}).is_root;

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
    // Content first: what a box shows is its content, and its icon is the
    // alternative. A saved preference wins, then the mode we are browsing in.
    // The root box is always its content — it exists to hold what is inside it.
    state.mode = isRootType(box)
      ? "content"
      : setting(box, "display", options.mode || "content");
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
  saveSection,
  newBox: openNewBoxModal,
  trail: () => state.trail,
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
  const forced = isRootType(box);
  document.querySelectorAll("#mode-toggle button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
    button.disabled = forced && button.dataset.mode === "icon";
    button.title =
      forced && button.dataset.mode === "icon"
        ? "The root box always shows what is inside it"
        : `Show the box as its ${button.dataset.mode}`;
  });
  const view = viewFor(box);
  if (!view) {
    canvas.append(emptyMessage(`No view for type ${box.box_type}.`));
    return;
  }
  view.render(canvas, box, context, state.mode, { embedded: false, depth: 0 });
}

/**
 * How deep content is rendered inside content. A grid inside a grid inside a
 * grid would otherwise fetch the library to pieces; past this depth a child is
 * shown as its icon instead.
 */
const MAX_EMBED_DEPTH = 2;

/**
 * Draw one box's content into `container`, as used by grid cells and by the root
 * view. The caller owns the container; this only fills it.
 */
async function renderContent(container, box, { depth, embedded = true }) {
  const view = viewFor(box) || views.get("*");
  await view.render(container, box, context, "content", { embedded, depth });
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

/**
 * Merge one view's settings into `config.<section>` and save the box.
 * Each view owns a namespace, so the grid's cells never collide with a text
 * view's font size.
 */
async function saveSection(section, patch) {
  const current = { ...(state.current.config || {}) };
  current[section] = { ...(current[section] || {}), ...patch };
  state.current = { ...state.current, config: current };
  try {
    await api.setConfig(state.current.uuid, current);
  } catch (error) {
    status(`could not save ${section}: ${error.message}`, true);
  }
  renderMain();
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

/** Root first, then the rest as the server ordered them. */
function orderedForSide(boxes) {
  const roots = boxes.filter((box) => (state.types.get(box.box_type) || {}).is_root);
  const others = boxes.filter((box) => !roots.includes(box));
  return [...roots, ...others];
}

function renderSide() {
  const list = el("box-list");
  list.replaceChildren();
  orderedForSide(state.boxes).forEach((box) => {
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
    kind.textContent = (state.types.get(box.box_type) || {}).is_root ? "root of the graph" : box.box_type;
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
/** Open the create-box modal; `parentUuid` presets where the new box goes. */
function openNewBoxModal(parentUuid = null, cell = null) {
  state.pendingCell = cell;
  fillParentChoices();
  if (parentUuid) el("nb-parent").value = parentUuid;
  renderConfigFields();
  el("new-box-modal").showModal();
  el("nb-name").focus();
}

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
    cell: state.pendingCell || null,
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
    state.pendingCell = null;
    el("new-box-modal").close();
    el("new-box-form").reset();
    await refreshSide();
    await openBox(created.uuid, { mode: "content" });
    status(`created ${created.name}`);
  } catch (error) {
    status(error.message, true);
  }
}

/**
 * Jump to the root box. It is normally in the list, but the list may be filtered
 * by a search, so fall back to the name `box init` gives it.
 */
async function goToRoot() {
  const known = state.boxes.find((box) => isRootType(box));
  if (known) return openBox(known.uuid);
  try {
    return await openBox("root");
  } catch {
    status("no root box in this library — run `box init`", true);
    return null;
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
  el("new-box").addEventListener("click", () => openNewBoxModal());
  el("go-root").addEventListener("click", goToRoot);
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

/**
 * Everything the row knows about a box: description, resource, metadata, config,
 * and the boxes around it. This is what "content" means for a plain box.
 */
function detailPanel(box, ctx) {
  const panel = document.createElement("div");
  panel.className = "detail-view";

  const title = document.createElement("h2");
  title.className = "detail-title";
  title.textContent = `${box.icon} ${box.name}`;
  panel.append(title);

  const type = state.types.get(box.box_type);
  const list = document.createElement("dl");
  list.className = "detail-fields";
  const add = (label, value) => {
    if (value === null || value === undefined || value === "") return;
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    if (typeof value === "object" && value.node) {
      detail.append(value.node);
    } else {
      detail.textContent = String(value);
    }
    list.append(term, detail);
  };

  add("type", type ? `${type.label} (${type.key})` : `${box.box_type} (unknown)`);
  if (box.description) add("description", box.description);
  if (box.resource_uri) add("resource", resourceNode(box));
  add("uuid", box.uuid);
  panel.append(list);

  if (box.metadata && Object.keys(box.metadata).length) {
    panel.append(section("metadata", () => keyValueList(box.metadata)));
  }
  if (box.config && Object.keys(box.config).length) {
    panel.append(section("config", () => jsonBlock(box.config)));
  }
  for (const [label, items] of [
    ["in", box.parents || []],
    ["contains", box.children || []],
  ]) {
    if (items.length) panel.append(section(label, () => chipList(items, ctx)));
  }
  return panel;
}

/** A resource shown as a link when it is a URL, as text when it is a path. */
function resourceNode(box) {
  const value = box.resource_uri;
  if (/^https?:\/\//i.test(value)) {
    const link = document.createElement("a");
    link.href = value;
    link.textContent = value;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    return { node: link };
  }
  const code = document.createElement("code");
  code.textContent = value;
  return { node: code };
}

function section(label, build) {
  const wrapper = document.createElement("section");
  wrapper.className = "detail-section";
  const heading = document.createElement("h3");
  heading.textContent = label;
  wrapper.append(heading, build());
  return wrapper;
}

function keyValueList(entries) {
  const list = document.createElement("ul");
  list.className = "key-values";
  Object.entries(entries)
    .sort(([left], [right]) => left.localeCompare(right))
    .forEach(([key, value]) => {
      const item = document.createElement("li");
      const name = document.createElement("strong");
      name.textContent = key;
      item.append(name, document.createTextNode(` ${value}`));
      list.append(item);
    });
  return list;
}

function jsonBlock(value) {
  const pre = document.createElement("pre");
  pre.className = "json";
  pre.textContent = JSON.stringify(value, null, 2);
  return pre;
}

/** Boxes you can jump to, drawn from a `{uuid, name, icon}` list. */
function chipList(boxes, ctx) {
  const holder = document.createElement("div");
  holder.className = "chips";
  boxes.forEach((brief) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.dataset.uuid = brief.uuid;
    chip.textContent = `${brief.icon} ${brief.name}`;
    chip.addEventListener("click", () => ctx.open(brief.uuid));
    holder.append(chip);
  });
  return holder;
}

/** The big glyph, with the name and description: what every type falls back to. */
function iconPane(box, note = "") {
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
  if (note) {
    const line = document.createElement("p");
    line.className = "caption muted";
    line.textContent = note;
    wrap.append(line);
  }
  return wrap;
}

/* A plain box: its icon, or everything the row knows. */
registerView("box", {
  render(canvas, box, ctx, mode, opts = {}) {
    if (mode !== "content") {
      canvas.append(iconPane(box));
      return;
    }
    // A cell or a root block is too small for the whole detail panel.
    canvas.append(opts.embedded ? iconPane(box) : detailPanel(box, ctx));
  },
});

/* An image: scaled into whatever space the screen has left, ratio untouched. */
registerView("image", {
  render(canvas, box, ctx, mode, opts = {}) {
    if (mode !== "content") {
      canvas.append(iconPane(box));
      return;
    }
    const stage = document.createElement("div");
    stage.className = "image-view";
    const image = document.createElement("img");
    image.src = api.rawUrl(box.uuid);
    image.alt = box.name;
    image.addEventListener("error", () => {
      stage.replaceChildren(
        emptyMessage(`Could not load the image at ${box.resource_uri || "(no resource)"}.`)
      );
      stage.classList.add("failed");
    });
    stage.append(image);
    canvas.append(stage);
  },
});

/* ---------------------------------------------------------------- grid ---- */

const GRID_DEFAULTS = { rows: 3, columns: 4 };

function gridOptions(box) {
  const section = (box.config || {}).grid || {};
  return { ...GRID_DEFAULTS, ...section };
}

/**
 * A grid of cells. Each cell holds one child: containment lives in the links
 * table, the cell a child sits in lives in `config.grid.cells` keyed by uuid.
 * Drag to move, onto an occupied cell to swap, double-click to open.
 */
registerView("grid", {
  async render(canvas, box, ctx, mode, opts = {}) {
    if (ctx.mode() !== "content") {
      canvas.append(iconPane(box));
      return;
    }
    const options = gridOptions(box);
    let children = [];
    try {
      children = await api.children(box.uuid);
    } catch (error) {
      canvas.append(emptyMessage(`Cannot list the children: ${error.message}`));
      return;
    }
    const byUuid = new Map(children.map((child) => [child.uuid, child]));
    const cells = new Map(
      Object.entries(options.cells || {}).map(([where, uuid]) => [where, uuid])
    );

    if (!opts.embedded) canvas.append(gridToolbar(box, options, ctx));

    const board = document.createElement("div");
    board.className = "grid-board";
    board.style.gridTemplateRows = `repeat(${options.rows}, minmax(4rem, 1fr))`;
    board.style.gridTemplateColumns = `repeat(${options.columns}, minmax(4rem, 1fr))`;
    canvas.append(board);

    for (let row = 0; row < options.rows; row += 1) {
      for (let column = 0; column < options.columns; column += 1) {
        board.append(
          gridCell(box, ctx, row, column, byUuid.get(cells.get(`${row},${column}`)), opts.depth || 0)
        );
      }
    }

    const orphans = children.filter((child) => ![...cells.values()].includes(child.uuid));
    if (orphans.length) {
      const strip = document.createElement("div");
      strip.className = "grid-unplaced";
      const label = document.createElement("span");
      label.className = "muted";
      label.textContent = `Not placed (${orphans.length}): drag one onto a cell`;
      strip.append(label);
      orphans.forEach((child) => strip.append(chipFor(child, ctx)));
      canvas.append(strip);
    }
  },
});

function chipFor(child, ctx) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "chip";
  chip.draggable = true;
  chip.textContent = `${child.icon} ${child.name}`;
  chip.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", child.uuid);
    event.dataTransfer.effectAllowed = "move";
  });
  chip.addEventListener("dblclick", () => ctx.open(child.uuid, { trail: ctx.trail(), mode: ctx.mode() }));
  return chip;
}

function gridToolbar(box, options, ctx) {
  const bar = document.createElement("div");
  bar.className = "grid-toolbar";

  const stepper = (labelText, value, actionPrefix, max) => {
    const group = document.createElement("span");
    group.className = "stepper";
    const label = document.createElement("span");
    label.textContent = labelText;
    const minus = document.createElement("button");
    minus.type = "button";
    minus.dataset.action = `${actionPrefix}-minus`;
    minus.textContent = "−";
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.max = String(max);
    input.value = String(value);
    input.dataset.action = `${actionPrefix}-value`;
    const plus = document.createElement("button");
    plus.type = "button";
    plus.dataset.action = `${actionPrefix}-plus`;
    plus.textContent = "＋";
    group.append(label, minus, input, plus);
    return group;
  };

  const rows = stepper("rows", options.rows, "grid-rows", 24);
  const columns = stepper("columns", options.columns, "grid-columns", 24);

  const add = document.createElement("button");
  add.type = "button";
  add.textContent = "＋ New box here";
  add.addEventListener("click", () => ctx.newBox(box.uuid));

  bar.append(rows, columns, add);

  async function resize(nextRows, nextColumns) {
    const clampedRows = Math.min(24, Math.max(1, nextRows));
    const clampedColumns = Math.min(24, Math.max(1, nextColumns));
    try {
      await api.gridResize(box.uuid, clampedRows, clampedColumns);
      ctx.reload();
    } catch (error) {
      ctx.status(`cannot resize: ${error.message}`, true);
    }
  }

  bar.addEventListener("click", (event) => {
    const action = event.target.dataset.action;
    if (!action || action.endsWith("-value")) return;
    const isRows = action.startsWith("grid-rows");
    const delta = action.endsWith("-minus") ? -1 : 1;
    if (isRows) resize(options.rows + delta, options.columns);
    else resize(options.rows, options.columns + delta);
  });
  const applyTyped = (event) => {
    const action = event.target.dataset.action;
    if (action === "grid-rows-value") resize(Number(event.target.value), options.columns);
    else if (action === "grid-columns-value") resize(options.rows, Number(event.target.value));
    else return false;
    return true;
  };
  // Apply on blur (change) and on Enter: typing a number and hitting Return is
  // how people expect a size box to behave.
  bar.addEventListener("change", applyTyped);
  bar.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && applyTyped(event)) event.preventDefault();
  });
  return bar;
}

/**
 * One cell. The child's *content* is drawn inside it — the picture, the text,
 * the nested listing — with a small caption for identity. Beyond
 * MAX_EMBED_DEPTH a child falls back to its icon so a grid of grids cannot
 * fetch the whole library at once.
 */
function gridCell(box, ctx, row, column, child, depth) {
  const cell = document.createElement("div");
  cell.className = "grid-cell";
  cell.dataset.row = String(row);
  cell.dataset.column = String(column);

  if (child) {
    cell.classList.add("occupied");
    cell.dataset.uuid = child.uuid;
    cell.draggable = true;
    const content = document.createElement("div");
    content.className = "cell-content";
    cell.append(content);
    const childDepth = depth + 1;
    if (childDepth <= MAX_EMBED_DEPTH) {
      renderContent(content, child, { depth: childDepth }).catch((error) => {
        content.replaceChildren(emptyMessage(error.message));
      });
    } else {
      content.append(iconPane(child));
    }
    const caption = document.createElement("span");
    caption.className = "cell-caption muted";
    caption.textContent = `${child.icon} ${child.name}`;
    cell.append(caption);
    cell.title = `Drag to move · double-click to open ${child.name}`;
  } else {
    const mark = document.createElement("span");
    mark.className = "cell-coords muted";
    mark.textContent = `${row},${column}`;
    cell.append(mark);
  }

  cell.addEventListener("dragstart", (event) => {
    if (!child) return;
    event.dataTransfer.setData("text/plain", child.uuid);
    event.dataTransfer.effectAllowed = "move";
  });
  cell.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    cell.classList.add("drop-target");
  });
  cell.addEventListener("dragleave", () => cell.classList.remove("drop-target"));
  cell.addEventListener("drop", async (event) => {
    event.preventDefault();
    cell.classList.remove("drop-target");
    const dragged = event.dataTransfer.getData("text/plain");
    if (!dragged) return;
    try {
      await api.gridPlace(box.uuid, dragged, [row, column]);
      ctx.reload();
    } catch (error) {
      ctx.status(`cannot place that box here: ${error.message}`, true);
    }
  });
  cell.addEventListener("dblclick", () => {
    if (child) ctx.open(child.uuid, { trail: ctx.trail(), mode: ctx.mode() });
  });
  cell.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const here = [row, column];
    ctx.showMenu(event.clientX, event.clientY, [
      child && { label: `Open ${child.name}`, run: () => ctx.open(child.uuid, { trail: ctx.trail(), mode: ctx.mode() }) },
      child && {
        label: "Empty this cell",
        run: async () => {
          await api.gridUnplace(box.uuid, child.uuid);
          ctx.reload();
        },
      },
      child && { separator: true },
      { label: child ? "New box in this cell" : "New box here", run: () => ctx.newBox(box.uuid, here) },
    ]);
  });
  return cell;
}

/* ----------------------------------------------------------------- root ---- */

/**
 * The root box: never an icon, and one child slot. What it shows is that
 * child's content, filling the pane. Dropping a box from the left panel puts it
 * in the slot and takes it from whatever was there; the displaced box is not
 * deleted, it just stops being the way in.
 */
registerView("root", {
  async render(canvas, box, ctx) {
    let children = [];
    try {
      children = await api.children(box.uuid);
    } catch (error) {
      canvas.append(emptyMessage(`Cannot read the root: ${error.message}`));
      return;
    }
    const child = children[0] || null;

    const holder = document.createElement("div");
    holder.className = "root-view";
    holder.dataset.uuid = box.uuid;
    canvas.append(holder);

    const bar = document.createElement("div");
    bar.className = "root-bar";
    const label = document.createElement("span");
    label.className = "muted";
    label.textContent = child ? `root › ${child.icon} ${child.name}` : "root — nothing filed in it";
    bar.append(label);
    if (child) {
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "Open";
      open.addEventListener("click", () => ctx.open(child.uuid, { trail: ctx.trail(), mode: "content" }));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove from root";
      remove.addEventListener("click", async () => {
        try {
          await api.unlink(box.uuid, child.uuid);
          ctx.reload();
        } catch (error) {
          ctx.status(error.message, true);
        }
      });
      bar.append(open, remove);
    }
    holder.append(bar);

    const body = document.createElement("div");
    body.className = "root-child-body";
    holder.append(body);

    if (child) {
      const block = document.createElement("section");
      block.className = "root-child";
      block.dataset.uuid = child.uuid;
      body.append(block);
      renderContent(block, child, { depth: 1 }).catch((error) => {
        block.replaceChildren(emptyMessage(error.message));
      });
    } else {
      const hint = document.createElement("div");
      hint.className = "root-empty";
      hint.textContent = "Drag a box here from the panel on the left to make it the way in.";
      body.append(hint);
    }

    // The slot accepts a drop anywhere in the root view.
    holder.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      holder.classList.add("drop-target");
    });
    holder.addEventListener("dragleave", () => holder.classList.remove("drop-target"));
    holder.addEventListener("drop", async (event) => {
      event.preventDefault();
      holder.classList.remove("drop-target");
      const dragged = event.dataTransfer.getData("text/plain");
      if (!dragged) return;
      try {
        await api.link(box.uuid, dragged);
        ctx.status("the root now shows that box");
        ctx.reload();
      } catch (error) {
        ctx.status(`cannot file it in the root: ${error.message}`, true);
      }
    });
  },
});

/* -------------------------------------------------------------- folders ---- */

const FOLDER_DEFAULTS = { layout: "list" };

function folderOptions(box) {
  return { ...FOLDER_DEFAULTS, ...((box.config || {}).folder || {}) };
}

/**
 * A container: its children as a list or as icons in a grid, like a file
 * explorer. Double-click opens a child in this pane, replacing the box that
 * contained it.
 */
const folderView = {
  async render(canvas, box, ctx, mode, opts = {}) {
    if (ctx.mode() !== "content") {
      canvas.append(iconPane(box));
      return;
    }
    const options = folderOptions(box);
    const wrap = document.createElement("div");
    wrap.className = "children-view";
    wrap.dataset.layout = options.layout;
    canvas.append(wrap);

    const toolbar = document.createElement("div");
    toolbar.className = "children-toolbar";
    if (opts.embedded) toolbar.classList.add("compact");
    const title = document.createElement("strong");
    title.textContent = `${box.icon} ${box.name}`;
    const count = document.createElement("span");
    count.className = "muted";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.dataset.action = "toggle-layout";
    toggle.textContent = options.layout === "list" ? "▦ Icons" : "☰ List";
    toggle.addEventListener("click", () => {
      const next = options.layout === "list" ? "grid" : "list";
      ctx.saveSection("folder", { layout: next });
    });
    const add = document.createElement("button");
    add.type = "button";
    add.textContent = "＋ New box here";
    add.addEventListener("click", () => ctx.newBox(box.uuid));
    // Inside a cell or a root block there is no room, and no reason, to change
    // what the contained folder is showing.
    toolbar.append(title, count, ...(opts.embedded ? [] : [toggle, add]));

    const holder = document.createElement("div");
    holder.className = "children";
    wrap.append(toolbar, holder);

    let children = [];
    try {
      children = await api.children(box.uuid);
    } catch (error) {
      holder.append(emptyMessage(`Cannot list the children: ${error.message}`));
      return;
    }
    count.textContent = ` ${children.length} inside`;
    if (!children.length) {
      holder.append(emptyMessage("Nothing in here yet."));
      return;
    }
    children.forEach((child) => holder.append(childTile(child, box, ctx)));
  },
};

function childTile(child, parent, ctx) {
  const item = document.createElement("div");
  item.className = "child";
  item.dataset.uuid = child.uuid;
  item.draggable = true;
  const glyph = document.createElement("span");
  glyph.className = "child-icon";
  glyph.textContent = child.icon;
  const name = document.createElement("span");
  name.className = "child-name";
  name.textContent = child.name;
  item.append(glyph, name);
  // One click selects, a double click replaces this pane's box with the child.
  item.addEventListener("click", () => {
    ctx.status(`${child.name} selected — double-click to open it`);
    document
      .querySelectorAll(".child")
      .forEach((other) => other.classList.toggle("selected", other === item));
  });
  item.addEventListener("dblclick", () =>
    ctx.open(child.uuid, { trail: ctx.trail(), mode: ctx.mode() })
  );
  item.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", child.uuid);
    event.dataTransfer.effectAllowed = "move";
  });
  item.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    event.stopPropagation();
    ctx.showMenu(event.clientX, event.clientY, [
      { label: "Open", run: () => ctx.open(child.uuid, { trail: ctx.trail(), mode: ctx.mode() }) },
      { label: "Show as icon", run: () => ctx.open(child.uuid, { mode: "icon" }) },
      { separator: true },
      { label: "New box in " + parent.name, run: () => ctx.newBox(parent.uuid) },
    ]);
  });
  return item;
}

registerView("folder", folderView);
registerView("directory", folderView); // a directory on disk is explored the same way

/* ---------------------------------------------------------------- text ---- */

const TEXT_DEFAULTS = { wrap: true, crop: false, font_size: 14 };

/** How the text should look: saved settings over the defaults. */
function textOptions(box) {
  return { ...TEXT_DEFAULTS, ...((box.config || {}).text || {}) };
}

registerView("text", {
  async render(canvas, box, ctx, mode, opts = {}) {
    if (ctx.mode() !== "content") {
      canvas.append(iconPane(box));
      return;
    }
    const options = textOptions(box);
    const stage = document.createElement("div");
    stage.className = "text-view";
    stage.dataset.wrap = options.wrap ? "on" : "off";
    stage.dataset.crop = options.crop ? "on" : "off";
    stage.style.setProperty("--text-size", `${options.font_size}px`);

    const body = document.createElement("pre");
    body.className = "text-body";
    stage.append(body);
    canvas.append(stage);

    try {
      body.textContent = await api.rawText(box.uuid);
    } catch (error) {
      stage.replaceChildren(emptyMessage(`Cannot read ${box.resource_uri}: ${error.message}`));
      stage.classList.add("failed");
      return;
    }

    stage.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      ctx.showMenu(event.clientX, event.clientY, [
        {
          label: `${options.wrap ? "✕" : "✓"} Wrap lines`,
          run: () => ctx.saveSection("text", { wrap: !options.wrap }),
        },
        {
          label: `${options.crop ? "✕" : "✓"} Crop to the pane`,
          run: () => ctx.saveSection("text", { crop: !options.crop }),
        },
        { separator: true },
        {
          label: "A+ Larger text",
          run: () => ctx.saveSection("text", { font_size: Math.min(40, options.font_size + 2) }),
        },
        {
          label: "A- Smaller text",
          run: () => ctx.saveSection("text", { font_size: Math.max(8, options.font_size - 2) }),
        },
        { separator: true },
        { label: "Reload", run: ctx.reload },
      ]);
    });
  },
});

/* The fallback for every type that has nothing better yet. */
registerView("*", {
  render(canvas, box, ctx, mode, opts = {}) {
    const note =
      mode === "content" && !opts.embedded
        ? `No content view for type ${box.box_type}; showing its icon.`
        : "";
    canvas.append(iconPane(box, note));
  },
});

/* Extension points, reachable from the console and from tests. */
const boxganizer = {
  api,
  state,
  registerView,
  registerConfigForm,
  addStyle,
  openBox,
  goUp,
  status,
  goToRoot,
  iconPane,
  detailPanel,
  setting,
  saveSection,
  textOptions,
  folderOptions,
  viewFor,
};
window.boxganizer = boxganizer;

init();
