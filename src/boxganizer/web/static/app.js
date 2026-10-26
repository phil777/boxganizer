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
  pendingPlace: null, // spot on a desktop the modal should place the new box at
  searchExpanded: "", // what the last search widened the typed words into
};

/* ------------------------------------------------------------------ api ---- */

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    let detail;
    try {
      const body = await response.json();
      detail = body && body.detail;
      reason =
        (detail && (detail.reason || detail.error)) ||
        (typeof detail === "string" ? detail : reason);
    } catch {
      /* a body that is not JSON says nothing more useful than the status */
    }
    const error = new Error(reason);
    // Callers that can say more (the file picker names the confine) read this.
    error.status = response.status;
    error.detail = typeof detail === "string" ? { error: detail } : detail;
    throw error;
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
  thumbnailUrl: (ref) => `/api/boxes/${encodeURIComponent(ref)}/thumbnail`,
  root: () => request("/api/root"),
  filter: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/filter`),
  setFilter: (ref, value) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/filter`, {
      method: "PUT",
      body: JSON.stringify({ filter: value }),
    }),
  resolveFolder: (ref) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/resolve`, { method: "POST" }),
  mqtt: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/mqtt`),
  mqttStreamUrl: (ref) => `/api/boxes/${encodeURIComponent(ref)}/mqtt/stream`,
  contentUrl: (ref, sub) =>
    `/api/boxes/${encodeURIComponent(ref)}/content?sub=${encodeURIComponent(sub || "")}`,
  listing: (ref, sub) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/listing?sub=${encodeURIComponent(sub || "")}`),
  archive: (ref, sub) => request(api.archiveUrl(ref, sub)),
  archiveUrl: (ref, sub) =>
    `/api/boxes/${encodeURIComponent(ref)}/archive?sub=${encodeURIComponent(sub || "")}`,
  browse: (path, showHidden) =>
    request(
      `/api/fs/browse?path=${encodeURIComponent(path || "")}&show_hidden=${showHidden ? "true" : "false"}`
    ),
  textAt: async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.text();
  },
  updateBox: (ref, payload) =>
    request(`/api/boxes/${encodeURIComponent(ref)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }),
  unlink: (ref, child) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/children/${encodeURIComponent(child)}`, {
      method: "DELETE",
    }),
  suggestType: (resourceUri, current) =>
    request(
      `/api/types/suggest?resource_uri=${encodeURIComponent(resourceUri)}&current=${encodeURIComponent(current || "box")}`
    ),
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
  desktopMove: (ref, child, x, y) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/desktop/move`, {
      method: "POST",
      body: JSON.stringify({ child, x, y }),
    }),
  mindmap: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/mindmap`),
  mindmapSide: (ref, child, body) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/mindmap/side`, {
      method: "POST",
      body: JSON.stringify({ child, ...body }),
    }),
  mindmapOrder: (ref, child, delta) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/mindmap/order`, {
      method: "POST",
      body: JSON.stringify({ child, delta }),
    }),
  mindmapHierarchy: (ref, child, direction) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/mindmap/hierarchy`, {
      method: "POST",
      body: JSON.stringify({ child, direction }),
    }),
  mindmapNode: (ref, parent, after) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/mindmap/node`, {
      method: "POST",
      body: JSON.stringify({ parent: parent || null, after: after || null }),
    }),
  mindmapFill: (ref, node, withBox) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/mindmap/fill`, {
      method: "POST",
      body: JSON.stringify({ node, with: withBox }),
    }),
  ssh: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/ssh`),
  sshRun: (ref, command, timeout) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/ssh/run`, {
      method: "POST",
      body: JSON.stringify({command, ...(timeout ? {timeout} : {})}),
    }),
  desktopResize: (ref, child, width, height) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/desktop/resize`, {
      method: "POST",
      body: JSON.stringify({ child, width, height }),
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

/* ----------------------------------------------------------- live views ---- */

/**
 * How many times the main pane has been handed to a new box.
 *
 * A view that waits for the server must ask, when the answer arrives, whether the
 * pane still shows the box it was asked to draw. Two picks in quick succession
 * otherwise both finish and both append, and the page ends up holding two of
 * whatever the box shows — two desktops, two folders, one of them invisible.
 *
 * Something a view keeps open — an event stream, a socket — is released by the
 * same number, because a connection the page no longer shows is a leak. A view
 * that opens one hands the way to close it to `holdLive`, and the pane calls
 * `releaseLive` as it moves on.
 */
const liveReleases = new Set();
let paneGeneration = 0;

/** Whether the render that asked is still the render the pane is showing. */
function stillOnScreen(token) {
  return token === paneGeneration;
}

function holdLive(release) {
  liveReleases.add(release);
  return () => liveReleases.delete(release);
}

function releaseLive() {
  paneGeneration += 1;
  const releases = [...liveReleases];
  liveReleases.clear();
  releases.forEach((release) => {
    try {
      release();
    } catch {
      /* a connection already closed is the answer we were asking for */
    }
  });
}

/* ------------------------------------------------------------- feedback ---- */

let statusTimer = null;

function ctx_free_status(message, isError = false) {
  status(message, isError);
}

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

/* ------------------------------------------------------------ main pane ---- */

const context = {
  open: openBox,
  status,
  saveSetting,
  saveSection,
  newBox: openNewBoxModal,
  trail: () => state.trail,
  reload: () => openBox(state.current.uuid),
  showMenu,
  removeFrom,
  edit: openEditBoxModal,
};

function renderMain() {
  const canvas = el("canvas");
  canvas.replaceChildren();
  // Whatever the box that was on screen had open, it is not on screen now.
  releaseLive();
  const box = state.current;
  if (!box) {
    canvas.append(emptyMessage("Nothing selected. Pick a box on the left."));
    return;
  }
  const view = viewFor(box);
  if (!view) {
    canvas.append(emptyMessage(`No view for type ${box.box_type}.`));
    return;
  }
  view.render(canvas, box, context, "content", { embedded: false, depth: 0 });
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
 *
 * `cell` says the container is one square of a grid: small, and one of many. A
 * view that holds a connection open reads it and stays a still number there,
 * because a wall of twelve live boxes would otherwise be twelve subscriptions.
 * The root view shows its one child at full size, so it does not set the flag.
 */
async function renderContent(container, box, { depth, embedded = true, cell = false }) {
  const view = viewFor(box) || views.get("*");
  await view.render(container, box, context, "content", { embedded, depth, cell });
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
  openBox(target.uuid, { trail: state.trail.slice(0, index) });
}

async function goUp() {
  if (state.trail.length > 1) {
    openTo(state.trail.length - 2);
    return;
  }
  const parents = (state.current && state.current.parents) || [];
  if (parents.length) await openBox(parents[0].uuid);
}

/**
 * Merge one view's settings into `config.<section>` and save the box.
 * Each view owns a namespace, so the grid's cells never collide with a text
 * view's font size.
 */
async function saveSection(section, patch, { rerender = true } = {}) {
  const current = { ...(state.current.config || {}) };
  current[section] = { ...(current[section] || {}), ...patch };
  state.current = { ...state.current, config: current };
  try {
    await api.setConfig(state.current.uuid, current);
  } catch (error) {
    status(`could not save ${section}: ${error.message}`, true);
  }
  // Some settings belong to a control the page is still holding — a search box
  // mid-word — and redrawing the pane would rebuild it and lose what is in it.
  if (rerender) renderMain();
}

/* ------------------------------------------------------------ left pane ---- */

/**
 * The search field's answer, with the one thing a JSON body cannot carry: what the
 * words were widened to on the way. A list that quietly also searched for synonyms
 * and near-miss spellings would leave someone wondering why their box did not turn
 * up, so the panel says what it looked for.
 */
async function searchBoxes(term) {
  const response = await fetch(`/api/search?q=${encodeURIComponent(term)}`);
  if (!response.ok) {
    throw new Error(`search failed: HTTP ${response.status}`);
  }
  return {
    boxes: await response.json(),
    expanded: response.headers.get("X-Search-Expanded") || "",
  };
}

async function refreshSide() {
  const term = el("search").value.trim();
  try {
    if (term) {
      const answer = await searchBoxes(term);
      state.boxes = answer.boxes;
      state.searchExpanded = answer.expanded;
    } else {
      state.boxes = await api.boxes();
      state.searchExpanded = "";
    }
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
    const glyph = iconElement(box);
    const name = document.createElement("span");
    name.textContent = box.name;
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = (state.types.get(box.box_type) || {}).is_root ? "root of the graph" : box.box_type;
    item.append(glyph, name, kind);
    // A box found in its file says so, and shows the words that answered — otherwise
    // it looks like the search got it wrong.
    if (box.match && box.match.how === "content" && box.match.passage) {
      const passage = document.createElement("span");
      passage.className = "hit-passage muted";
      passage.textContent = box.match.passage;
      item.append(passage);
      item.classList.add("content-hit");
    }
    item.addEventListener("click", () => openBox(box.uuid));
    item.addEventListener("dblclick", () => openBox(box.uuid));
    item.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", box.uuid);
      event.dataTransfer.effectAllowed = "move";
    });
    item.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      showMenu(event.clientX, event.clientY, boxMenuItems(box));
    });
    list.append(item);
  });
  el("side-status").textContent = `${state.boxes.length} box${state.boxes.length === 1 ? "" : "es"}`;
  const note = el("search-note");
  if (note) {
    // The server sends this only when it actually added something to the words, so a
    // search that needed no widening says nothing — a note repeating the search back is
    // noise, and noise teaches people to stop reading the notes.
    note.hidden = !state.searchExpanded;
    note.textContent = state.searchExpanded ? `searched: ${state.searchExpanded}` : "";
  }
  markSelected();
}

function markSelected() {
  document.querySelectorAll("#box-list li").forEach((item) => {
    item.setAttribute("aria-selected", String(state.current && item.dataset.uuid === state.current.uuid));
  });
}

/* ---------------------------------------------------------- context menu --- */

/** The items every box gets, wherever it is shown, plus the caller's own. */
function boxMenuItems(box, extra = []) {
  return [
    { label: "Open", run: () => openBox(box.uuid) },
    { separator: true },
    { label: "Modify…", run: () => openEditBoxModal(box) },
    ...extra,
  ];
}

/** Unlink a child from one parent. The box itself and its other parents survive. */
async function removeFrom(parent, child) {
  try {
    await api.unlink(parent.uuid, child.uuid);
    await refreshSide();
    renderMain();
    status(`removed ${child.name} from ${parent.name}`);
  } catch (error) {
    status(error.message, true);
  }
}


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

/**
 * The type menu, narrowed by what has been typed.
 *
 * Forty-one kinds do not fit a glance, so each dialog has a filter in front of its
 * menu: a few letters cut the list to the types whose key, label, one-line summary
 * or branch match. The select stays the source of truth — every other part of the
 * dialog reads its value — so filtering can never desynchronise the form from what
 * will be created. The type already chosen is always kept among the options even
 * when the letters exclude it: narrowing the list is not a way to lose a choice.
 */
const TYPE_CONTROLS = [
  { select: "nb-type", filter: "nb-type-filter", count: "nb-type-count" },
  { select: "eb-type", filter: "eb-type-filter", count: "eb-type-count" },
];

function typeMatchesTerm(type, term) {
  if (!term) return true;
  const branch = ((type.ancestors || [])[0] || "box").toLowerCase();
  return (
    type.key.toLowerCase().includes(term) ||
    type.label.toLowerCase().includes(term) ||
    (type.summary || "").toLowerCase().includes(term) ||
    branch.includes(term)
  );
}

function renderTypeOptions(select, term) {
  const chosen = select.value;
  const groups = new Map();
  let matches = 0;
  [...state.types.values()]
    .filter((type) => !type.abstract)
    .forEach((type) => {
      const hit = typeMatchesTerm(type, term);
      if (hit) matches += 1;
      // the chosen type rides along even when the letters reject it
      if (!hit && type.key !== chosen) return;
      const branch = (type.ancestors || [])[0] || "box";
      if (!groups.has(branch)) groups.set(branch, []);
      groups.get(branch).push(type);
    });

  select.replaceChildren();
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
  if (chosen) select.value = chosen;
  return matches;
}

function fillTypeChoices() {
  TYPE_CONTROLS.forEach(({ select: id, filter: filterId, count: countId }) => {
    const select = el(id);
    if (!select) return;
    const filter = el(filterId);
    const term = filter ? filter.value.trim().toLowerCase() : "";
    const matches = renderTypeOptions(select, term);
    const count = el(countId);
    if (count) count.textContent = term ? `— ${matches} matching` : "";
    // One kind left is the answer: take it, so typing a name lands on it and the
    // summary and the per-type fields follow immediately.
    if (term && matches === 1) {
      const only = [...state.types.values()].find((type) => !type.abstract && typeMatchesTerm(type, term));
      if (only && select.value !== only.key) {
        select.value = only.key;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  });
}

/**
 * Clear the letters so the whole menu is there again, and put a chosen type in the
 * select. Opening a dialog must never inherit the leftovers of the last one.
 */
function resetTypeFilter(selectId) {
  const control = TYPE_CONTROLS.find((entry) => entry.select === selectId);
  if (!control) return;
  const filter = el(control.filter);
  if (filter) filter.value = "";
  const count = el(control.count);
  if (count) count.textContent = "";
  renderTypeOptions(el(selectId), "");
}

function wireTypeFilters() {
  TYPE_CONTROLS.forEach(({ select: selectId, filter: filterId }) => {
    const filter = el(filterId);
    const select = el(selectId);
    if (!filter || !select) return;
    filter.addEventListener("input", () => fillTypeChoices());
    // Escape clears the letters, which brings the whole menu back.
    filter.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && filter.value) {
        event.stopPropagation();
        filter.value = "";
        fillTypeChoices();
      }
      if (event.key === "Enter") {
        event.preventDefault();
        select.focus();
      }
    });
  });
}

function parentOption(select, box) {
  const option = document.createElement("option");
  option.value = box.uuid;
  option.textContent = `${box.icon} ${box.name}`;
  select.append(option);
  return option;
}

async function fillParentChoices(preferred) {
  const select = el("nb-parent");
  select.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "(no parent)";
  select.append(none);
  state.boxes.forEach((box) => parentOption(select, box));

  // The panel holds one page of the library, and may be narrowed by a search, so
  // the box someone said "New box in" is not necessarily among these. Asking for
  // that one is what makes the preselection mean something in a big library: setting
  // a select to a value it has never heard of does nothing at all, and the new box
  // would quietly be filed somewhere else.
  if (preferred && !state.boxes.some((box) => box.uuid === preferred)) {
    try {
      parentOption(select, await api.box(preferred));
    } catch {
      /* the value will not stick; the status line says where the box went */
    }
  }

  const root = state.boxes.find((box) => box.box_type === "root");
  if (root) select.value = root.uuid;
}

let suggestTimer = null;

/* The two dialogs share their field machinery; only the element ids differ. */
const CREATE_MODAL = {
  id: "new-box-modal",
  form: "new-box-form",
  name: "nb-name",
  type: "nb-type",
  summary: "nb-summary",
  description: "nb-description",
  resource: "nb-resource",
  suggest: "nb-suggest",
  config: "nb-config",
  icon: null,
};

const EDIT_MODAL = {
  id: "edit-box-modal",
  form: "edit-box-form",
  name: "eb-name",
  type: "eb-type",
  summary: "eb-summary",
  description: "eb-description",
  resource: "eb-resource",
  suggest: "eb-suggest",
  config: "eb-config",
  icon: "eb-icon",
};

/**
 * Ask the server whether the resource suits a more specific type than the one
 * picked, and offer it. A proposal only: nothing changes until it is clicked.
 */
async function checkResourceType(modal = CREATE_MODAL) {
  const resource = el(modal.resource).value.trim();
  const holder = el(modal.suggest);
  if (!resource) {
    holder.hidden = true;
    holder.replaceChildren();
    return;
  }
  let payload;
  try {
    payload = await api.suggestType(resource, el(modal.type).value);
  } catch (error) {
    ctx_free_status(`cannot check the resource type: ${error.message}`, true);
    return;
  }
  const suggestion = payload.suggestion;
  holder.replaceChildren();
  if (!suggestion) {
    holder.hidden = true;
    return;
  }
  holder.hidden = false;
  holder.append(document.createTextNode(`${suggestion.icon} ${suggestion.reason} — ${suggestion.label}? `));
  const accept = document.createElement("button");
  accept.type = "button";
  accept.className = "primary";
  accept.dataset.action = "accept-suggestion";
  accept.textContent = `Use ${suggestion.icon} ${suggestion.label}`;
  accept.addEventListener("click", () => {
    el(modal.type).value = suggestion.key;
    renderConfigFields(modal);
    holder.hidden = true;
  });
  holder.append(accept);
}

/** Type-specific fields. Views add theirs with `registerConfigForm`. */
/** Open the create-box modal; `parentUuid` presets where the new box goes. */
async function openNewBoxModal(options = {}) {
  // A container may ask for a box to be filed in it by handing over its uuid
  // (`ctx.newBox(box.uuid)`) or an object of choices. Both mean the same thing, so
  // both preselect the parent: the user said where the box belongs.
  const choices = typeof options === "string" ? { parent: options } : options || {};
  resetTypeFilter(CREATE_MODAL.type);
  state.pendingCell = choices.cell || null;
  // A desktop menu hands over the spot its right-click fell on.
  state.pendingPlace = choices.place || null;
  await fillParentChoices(choices.parent);
  if (choices.parent) el("nb-parent").value = choices.parent;
  if (choices.boxType) el(CREATE_MODAL.type).value = choices.boxType;
  renderConfigFields(CREATE_MODAL);
  if (choices.name) el(CREATE_MODAL.name).value = choices.name;
  if (choices.resource) el(CREATE_MODAL.resource).value = choices.resource;
  el(CREATE_MODAL.id).showModal();
  el(CREATE_MODAL.name).focus();
}

/**
 * Open the modify dialog for an existing box. Everything the box owns is here,
 * including its type: the type select is prefilled and a resource that fits a
 * more specific type offers to switch to it.
 */
function openEditBoxModal(box) {
  state.editing = box.uuid;
  resetTypeFilter(EDIT_MODAL.type);
  el(EDIT_MODAL.name).value = box.name;
  el(EDIT_MODAL.description).value = box.description || "";
  el(EDIT_MODAL.resource).value = box.resource_uri || "";
  if (EDIT_MODAL.icon) el(EDIT_MODAL.icon).value = box.icon || "";
  el(EDIT_MODAL.type).value = box.box_type;
  renderConfigFields(EDIT_MODAL, box.config || {});
  el(EDIT_MODAL.suggest).hidden = true;
  checkResourceType(EDIT_MODAL);
  el(EDIT_MODAL.id).showModal();
  el(EDIT_MODAL.name).focus();
}

async function submitEditBox(event) {
  event.preventDefault();
  if (!state.editing) return;
  const payload = {
    name: el(EDIT_MODAL.name).value.trim(),
    description: el(EDIT_MODAL.description).value.trim() || null,
    box_type: el(EDIT_MODAL.type).value,
    resource_uri: el(EDIT_MODAL.resource).value.trim() || null,
  };
  if (EDIT_MODAL.icon) payload.icon = el(EDIT_MODAL.icon).value.trim() || null;
  try {
    payload.config = collectConfig(EDIT_MODAL);
  } catch (error) {
    ctx_free_status(error.message, true);
    return;
  }
  try {
    const saved = await api.updateBox(state.editing, payload);
    el(EDIT_MODAL.id).close();
    state.editing = null;
    await refreshSide();
    if (state.current && state.current.uuid === saved.uuid) {
      await openBox(saved.uuid);
    } else {
      renderMain(); // a child in the current view may have been renamed or retyped
    }
    status(`saved ${saved.name}`);
  } catch (error) {
    status(error.message, true);
  }
}

/* --------------------------------------------------------- file picker ---- */

const picker = { target: null, from: "", path: "", root: null, selected: null };

/**
 * A selector over the machine the server runs on. This cannot be
 * `<input type=file>`: a browser hands back a blob and hides the real path, and
 * a resource URI has to be the path the server will read. What is browsable is
 * the same confine the file endpoints use.
 */
async function openFilePicker(target, startValue) {
  // `target` is the id of the field to fill, or a function to hand the choice to.
  // A context menu has no field of its own, so the desktop's background picks with
  // a function and writes the box directly.
  picker.target = target;
  picker.from = typeof target === "function" ? String(startValue || "") : "";
  picker.selected = null;
  picker.path = "";
  el("fp-show-hidden").checked = false;
  el("fp-filter").value = "";
  el("fp-path").value = "";
  el("fp-note").textContent = "opening\u2026";
  el("file-picker").showModal();

  // Start where the field points. A directory resource opens on itself; a file
  // opens on the folder holding it; a path the confine refuses falls back to the
  // confine, with the refusal said out loud rather than left as a stale listing.
  const start = (typeof target === "function" ? picker.from : el(target).value || "").trim();
  if (start && (await showIn(start))) return;
  if (start.includes("/")) {
    const parent = start.replace(/\/[^/]*\/?$/, "");
    if (parent && (await showIn(parent))) return;
  }
  await showIn("");
}

async function showIn(path) {
  let payload;
  try {
    payload = await api.browse(path, el("fp-show-hidden").checked);
  } catch (error) {
    const outside =
      error.detail && error.detail.error === "path_out_of_range"
        ? ` \u2014 the picker is confined to ${error.detail.root || "its root"}`
        : "";
    el("fp-note").textContent = `cannot list ${path || "here"}: ${error.message}${outside}`;
    return false;
  }
  picker.path = payload.path;
  picker.root = payload.root;
  picker.selected = null;
  el("fp-path").value = payload.path;
  el("fp-note").textContent =
    payload.root === payload.path
      ? `browsing from ${payload.root}`
      : `within ${payload.root}`;
  el("fp-up").disabled = payload.parent === null;
  el("fp-choose").textContent = "Choose this folder";
  const filter = el("fp-filter").value.trim().toLowerCase();
  const list = el("fp-list");
  list.replaceChildren();
  const entries = payload.entries.filter(
    (entry) => !filter || entry.name.toLowerCase().includes(filter)
  );
  if (!entries.length) {
    list.append(emptyMessage(filter ? "Nothing here matches the filter." : "No files here."));
    return true;
  }
  entries.forEach((entry) => {
    const row = document.createElement("div");
    row.className = `fp-row ${entry.kind === "dir" ? "dir" : "file"}`;
    row.setAttribute("role", "option");
    const glyph = document.createElement("span");
    glyph.textContent = entry.kind === "dir" ? "📁" : entry.icon || "\u{1f4c4}";
    const name = document.createElement("span");
    name.className = "fp-name";
    name.textContent = entry.name;
    const size = document.createElement("span");
    size.className = "fp-size";
    size.textContent = entry.kind === "dir" ? "" : formatSize(entry.size);
    row.append(glyph, name, size);
    row.addEventListener("click", () => {
      list.querySelectorAll(".fp-row").forEach((other) => other.classList.toggle("selected", other === row));
      picker.selected = entry;
      el("fp-choose").textContent =
        entry.kind === "dir" ? `Choose ${entry.name}/` : `Choose ${entry.name}`;
    });
    row.addEventListener("dblclick", () =>
      entry.kind === "dir" ? showIn(`${entry.path}/`) : pick(entry.path)
    );
    list.append(row);
  });
  return true;
}

/**
 * Put the chosen path where it belongs: in the resource field, or in the hands of
 * whoever opened the picker from somewhere that has no field.
 */
function pick(chosen) {
  el("file-picker").close();
  if (typeof picker.target === "function") {
    picker.target(chosen);
    return;
  }
  const input = el(picker.target);
  input.value = chosen;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  status(`resource set to ${chosen}`);
}

function registerConfigForm(typeKey, form) {
  configForms.set(typeKey, form);
}

function renderConfigFields(modal = CREATE_MODAL, values = {}) {
  const holder = el(modal.config);
  holder.replaceChildren();
  const typeKey = el(modal.type).value;
  if (modal.summary) el(modal.summary).textContent = (state.types.get(typeKey) || {}).summary || "";
  const form = formFor(typeKey);
  if (form) {
    form.fields.forEach((field) => {
      const label = document.createElement("label");
      label.textContent = field.label;
      const input = document.createElement("input");
      input.name = `config:${field.key}`;
      input.type = field.type || "number";
      const preset = values[field.key];
      input.value = preset !== undefined ? preset : field.default !== undefined ? field.default : "";
      label.append(input);
      holder.append(label);
    });
  }
  const extra = document.createElement("label");
  extra.textContent = "More config (JSON)";
  const area = document.createElement("textarea");
  area.name = "config:json";
  const rest = { ...values };
  (form ? form.keys : []).forEach((key) => delete rest[key]);
  area.value = JSON.stringify(rest, null, 2).replace("{}", "{}");
  extra.append(area);
  holder.append(extra);
}

function collectConfig(modal = CREATE_MODAL) {
  const config = {};
  const form = formFor(el(modal.type).value);
  (form ? form.keys : []).forEach((key) => {
    const input = el(modal.config).querySelector(`[name="config:${key}"]`);
    if (input && input.value !== "") {
      config[key] = input.type === "number" ? Number(input.value) : input.value;
    }
  });
  const json = el(modal.config).querySelector('[name="config:json"]').value.trim();
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
    place: state.pendingPlace || null,
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
    const filedIn = payload.parent;
    state.pendingCell = null;
    state.pendingPlace = null;
    el("new-box-modal").close();
    el("new-box-form").reset();
    await refreshSide();
    if (filedIn) {
      // The box was made *from* another one, so the view stays on that one and the
      // new box appears inside it. Jumping to the child would leave the container
      // the user was working in, which is the place they wanted to see the result.
      await openBox(filedIn);
      status(`created ${created.name} in it`);
    } else {
      await openBox(created.uuid);
      status(`created ${created.name}`);
    }
  } catch (error) {
    status(error.message, true);
  }
}

/**
 * The box the library starts from, from the server that knows the graph.
 *
 * It is not looked for in the left panel: that panel holds one page of the library
 * sorted by name, so in a library big enough to spill over a page, or filtered by
 * a search, the root is simply not in it — and the page would start on whichever
 * box sorts first instead. Asked of the server, the root is found by its type
 * whoever it is named and however many boxes sort ahead of it.
 */
async function findRoot() {
  try {
    return await api.root();
  } catch (error) {
    // A library with no root box is an answer, not a failure to work around; any
    // other failure falls back to what the page already has in hand.
    if (error.status === 404) return null;
    return state.boxes.find((box) => isRootType(box)) || null;
  }
}

/** Jump to the root box. */
async function goToRoot() {
  const root = await findRoot();
  if (!root) {
    status("no root box in this library — run `box init`", true);
    return null;
  }
  return openBox(root.uuid);
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
  wireTypeFilters();
  el("search").addEventListener("input", () => refreshSide());
  el("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    refreshSide();
  });
  el("up").addEventListener("click", goUp);
  el("new-box").addEventListener("click", () => openNewBoxModal());
  el("go-root").addEventListener("click", goToRoot);
  el("nb-cancel").addEventListener("click", () => el("new-box-modal").close());
  el(CREATE_MODAL.type).addEventListener("change", () => {
    renderConfigFields(CREATE_MODAL);
    checkResourceType(CREATE_MODAL);
  });
  el("nb-resource").addEventListener("input", () => {
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => checkResourceType(CREATE_MODAL), 300);
  });
  el("new-box-form").addEventListener("submit", submitNewBox);
  // Cancelling the dialog is a second way out of the modal, and it must forget the
  // pending spot too: a leftover one would drop the next unrelated box in its path.
  el("new-box-modal").addEventListener("close", () => {
    state.pendingCell = null;
    state.pendingPlace = null;
  });
  el(EDIT_MODAL.type).addEventListener("change", () => {
    renderConfigFields(EDIT_MODAL, {});
    checkResourceType(EDIT_MODAL);
  });
  el(EDIT_MODAL.resource).addEventListener("input", () => {
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => checkResourceType(EDIT_MODAL), 300);
  });
  el("edit-box-form").addEventListener("submit", submitEditBox);
  el("eb-cancel").addEventListener("click", () => el("edit-box-modal").close());

  document.querySelectorAll(".browse").forEach((button) =>
    button.addEventListener("click", () => openFilePicker(button.dataset.resource))
  );
  el("fp-go").addEventListener("click", () => showIn(el("fp-path").value.trim()));
  el("fp-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      showIn(el("fp-path").value.trim());
    }
  });
  el("fp-up").addEventListener("click", () => {
    const parent = picker.path.replace(/\/[^/]*\/?$/, "");
    showIn(parent || "");
  });
  el("fp-home").addEventListener("click", () => showIn(""));
  el("fp-filter").addEventListener("input", () => showIn(picker.path));
  el("fp-show-hidden").addEventListener("change", () => showIn(picker.path));
  el("fp-cancel").addEventListener("click", () => el("file-picker").close());
  el("fp-choose").addEventListener("click", () =>
    pick(picker.selected ? picker.selected.path : picker.path)
  );
  document.addEventListener("click", hideMenu);
  window.addEventListener("blur", hideMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideMenu();
  });

  await refreshSide();
  // The way in is the root box, not the first box the list happens to show.
  const root = await findRoot();
  if (root) await openBox(root.uuid);
  else if (state.boxes[0]) await openBox(state.boxes[0].uuid);
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

/**
 * A box's icon. Image and PDF boxes carry a miniature of their own file, drawn
 * in a thin frame; everything else is the type's emoji. If the miniature cannot
 * be served (the file moved), the emoji comes back instead of a broken image.
 */
function iconElement(box, className = "") {
  const usesThumb = (box.config || {}).icon_source === "thumbnail";
  if (usesThumb && box.uuid) {
    const img = document.createElement("img");
    const pageIcon = (state.types.get(box.box_type) || {}).thumbnail_kind === "pdf-page";
    img.className = `box-icon box-icon--thumb ${pageIcon ? "box-icon--page" : ""} ${className}`.trim();
    img.src = api.thumbnailUrl(box.uuid);
    img.alt = box.name;
    img.draggable = false;
    img.addEventListener("error", () => {
      const fallback = document.createElement("span");
      fallback.className = `box-icon ${className}`.trim();
      fallback.textContent = box.icon;
      img.replaceWith(fallback);
    });
    return img;
  }
  const span = document.createElement("span");
  span.className = `box-icon ${className}`.trim();
  span.textContent = box.icon;
  return span;
}

/** The big glyph, with the name and description: what every type falls back to. */
function iconPane(box, note = "") {
  const wrap = document.createElement("div");
  wrap.className = "icon-view";
  wrap.dataset.boxType = box.box_type;
  const glyph = iconElement(box, "glyph");
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
    // A cell or a root block is too small for the whole detail panel.
    canvas.append(opts.embedded ? iconPane(box) : detailPanel(box, ctx));
  },
});

/* An image: scaled into whatever space the screen has left, ratio untouched. */
registerView("image", {
  render(canvas, box, ctx, mode, opts = {}) {
    const src = opts.src || api.rawUrl(box.uuid);
    const stage = document.createElement("div");
    stage.className = "image-view";
    const image = document.createElement("img");
    image.src = src;
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
    const token = paneGeneration;
    const options = gridOptions(box);
    let children = [];
    try {
      children = await api.children(box.uuid);
    } catch (error) {
      canvas.append(emptyMessage(`Cannot list the children: ${error.message}`));
      return;
    }
    if (!stillOnScreen(token)) return; // the pane moved on while the list was coming
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
  chip.append(iconElement(child), document.createTextNode(` ${child.name}`));
  chip.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", child.uuid);
    event.dataTransfer.effectAllowed = "move";
  });
  chip.addEventListener("dblclick", () => ctx.open(child.uuid, { trail: ctx.trail() }));
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
  add.addEventListener("click", () => ctx.newBox({ parent: box.uuid }));

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
      renderContent(content, child, { depth: childDepth, cell: true }).catch((error) => {
        content.replaceChildren(emptyMessage(error.message));
      });
    } else {
      content.append(iconPane(child));
    }
    const caption = document.createElement("span");
    caption.className = "cell-caption muted";
    caption.append(iconElement(child), document.createTextNode(` ${child.name}`));
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
    if (child) ctx.open(child.uuid, { trail: ctx.trail() });
  });
  cell.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const here = [row, column];
    ctx.showMenu(event.clientX, event.clientY, [
      child && { label: `Open ${child.name}`, run: () => ctx.open(child.uuid, { trail: ctx.trail() }) },
      child && {
        label: "Empty this cell",
        run: async () => {
          await api.gridUnplace(box.uuid, child.uuid);
          ctx.reload();
        },
      },
      child && { label: "Modify…", run: () => ctx.edit(child) },
      child && { separator: true },
      {
        label: child ? "New box in this cell" : "New box here",
        run: () => ctx.newBox({ parent: box.uuid, cell: here }),
      },
    ]);
  });
  return cell;
}



/* -------------------------------------------------------------- desktop ---- */

const DESKTOP_DEFAULTS = { grid: 32, snap: false, tile: 96 };

function desktopOptions(box) {
  // `canvas` is the size the icons were arranged on, recorded by a full-size view.
  // It has no default: until a desktop has been seen at full size there is nothing
  // to scale relative to, and a smaller pane shows it as it is.
  return { ...DESKTOP_DEFAULTS, ...((box.config || {}).desktop || {}) };
}

/**
 * How large the canvas under this node is being drawn: 1 when the desktop fills its
 * pane, less when a grid cell shows the whole of it shrunk. A pointer moves in screen
 * pixels while an icon is placed in canvas pixels, so a gesture has to divide by this
 * or the icon runs ahead of the finger and lands off the picture it was sitting on.
 */
function surfaceScale(node) {
  const holder = node && node.closest ? node.closest(".desktop-surface") : null;
  const value = Number(holder ? holder.dataset.scale : 1);
  return Number.isFinite(value) && value > 0 ? value : 1;
}

/** The same rounding the core applies in `boxganizer.desktop`, so the icon the
 * page drags to ends up where the database will put it. */
function magnetise(value, options) {
  if (!options.snap) return value;
  const cell = Number(options.grid) || DESKTOP_DEFAULTS.grid;
  return Math.floor(value / cell + 0.5) * cell;
}

/**
 * A `desktop` box: its children as icons where the user put them, on a surface
 * that can carry a background picture. Drag an icon to move it, its corner to
 * size it, double-click to open it in place of the desktop. The magnetic grid is
 * a mode, off until it is asked for, and both the size and the mode are kept in
 * the box, so the desktop is the same in another browser tomorrow.
 */
registerView("desktop", {
  async render(canvas, box, ctx, mode, opts = {}) {
    const token = paneGeneration;
    const options = desktopOptions(box);
    let children = [];
    try {
      children = await api.children(box.uuid);
    } catch (error) {
      canvas.append(emptyMessage(`Cannot list the children: ${error.message}`));
      return;
    }
    if (!stillOnScreen(token)) return; // otherwise a quick second pick draws two desktops
    const positions = options.positions || {};

    if (!opts.embedded) canvas.append(desktopToolbar(box, options, ctx, children.length));

    const wrap = document.createElement("div");
    wrap.className = "desktop-view";
    wrap.dataset.uuid = box.uuid;
    wrap.dataset.snap = String(!!options.snap);
    wrap.style.setProperty("--desk-grid", `${Number(options.grid) || DESKTOP_DEFAULTS.grid}px`);
    canvas.append(wrap);

    const surface = document.createElement("div");
    surface.className = "desktop-surface";
    wrap.append(surface);

    // The resource field may point at a picture to work on. It is tried before it
    // is announced, so a resource that is not an image leaves a plain desktop.
    // The picture is painted on the surface, not on the pane around it: the surface
    // is what gets scaled, and a picture that scales with its pane while the icons
    // do not is how an icon ends up somewhere else on the wallpaper.
    if (box.resource_uri) {
      const probe = new Image();
      probe.onload = () => {
        surface.classList.add("has-background");
        surface.style.setProperty("--desk-bg", `url(${JSON.stringify(api.rawUrl(box.uuid))})`);
      };
      probe.src = api.rawUrl(box.uuid);
    }

    /*
     * The surface is a canvas of one fixed size, and the pane showing it can be any
     * size at all — the main pane, or one square of a grid. So the whole canvas is
     * scaled by a single factor: the picture on it and every icon on it together.
     * That is the only way an icon stays on the same spot of the picture when the
     * desktop is shown small; scaling the two apart is what let them drift.
     *
     * The size the canvas was laid out at is kept in the box (`desktop.canvas`),
     * recorded when the desktop is shown at full size. A cell cannot guess it: the
     * desktop was arranged in the main pane, and that is the scale the positions are
     * written in.
     */
    const cell = Number(options.grid) || DESKTOP_DEFAULTS.grid;
    let widest = 0;
    let tallest = 0;
    Object.values(positions).forEach((rect) => {
      widest = Math.max(widest, (rect.x || 0) + (rect.width || DESKTOP_DEFAULTS.tile));
      tallest = Math.max(tallest, (rect.y || 0) + (rect.height || DESKTOP_DEFAULTS.tile));
    });
    const extent = { width: widest ? widest + cell : 0, height: tallest ? tallest + cell : 0 };
    const recorded = options.canvas || {};
    let laidOut =
      Number(recorded.width) > 0 && Number(recorded.height) > 0
        ? { width: Number(recorded.width), height: Number(recorded.height) }
        : null;

    function fitSurface() {
      const availW = wrap.clientWidth;
      const availH = wrap.clientHeight;
      if (!availW || !availH) return;
      const target = laidOut || {
        width: Math.max(availW, extent.width),
        height: Math.max(availH, extent.height),
      };
      const ratio = Math.min(availW / target.width, availH / target.height);
      // Never enlarged — a desktop with three icons does not blow them up to fill
      // the window — and never shrunk past the point where an icon's label is
      // unreadable, which a grid cell can otherwise reach.
      const scale = Math.max(0.2, Math.min(1, ratio));
      surface.style.width = `${target.width}px`;
      surface.style.height = `${target.height}px`;
      surface.style.transform = scale === 1 ? "" : `scale(${scale})`;
      surface.dataset.scale = String(scale);
    }

    fitSurface();
    // The pane changes size when a grid gets more rows, when the window is dragged,
    // when the left panel is resized: the canvas follows it, asking nothing of the
    // server. The observer is released with the pane, like any other live thing.
    const watcher = new ResizeObserver(() => fitSurface());
    watcher.observe(wrap);
    holdLive(() => watcher.disconnect());

    if (!opts.embedded) {
      // Record the size this desktop is arranged at, so a smaller pane can scale the
      // same canvas instead of inventing one. Only when it really changed: writing
      // the config on every paint would keep the database busy for nothing.
      const wanted = {
        width: Math.round(surface.getBoundingClientRect().width),
        height: Math.round(surface.getBoundingClientRect().height),
      };
      if (
        wanted.width > 0 &&
        wanted.height > 0 &&
        (Math.abs(wanted.width - Number(recorded.width || 0)) > 2 ||
          Math.abs(wanted.height - Number(recorded.height || 0)) > 2)
      ) {
        const config = box.config || {};
        const next = { ...config, desktop: { ...(config.desktop || {}), canvas: wanted } };
        api
          .setConfig(box.uuid, next)
          .then(() => {
            box.config = next;
          })
          .catch(() => {
            /* the desktop still shows; the next full-size view asks again */
          });
        laidOut = wanted;
      }
    }

    children.forEach((child) => {
      const rect = positions[child.uuid];
      if (rect) surface.append(desktopIcon(box, child, rect, options, ctx));
    });
    if (!children.length) {
      const hint = document.createElement("p");
      hint.className = "desktop-hint muted";
      hint.textContent = "Drag a box here from the panel on the left to put an icon on the desktop.";
      surface.append(hint);
    }

    const loose = children.filter((child) => !positions[child.uuid]);
    if (loose.length) {
      const strip = document.createElement("div");
      strip.className = "desktop-unplaced";
      const label = document.createElement("span");
      label.className = "muted";
      label.textContent = `Not placed (${loose.length}): drag one onto the surface`;
      strip.append(label);
      loose.forEach((child) => strip.append(chipFor(child, ctx)));
      wrap.append(strip);
    }

    // Anything dropped on the surface lands where it was dropped — or on the
    // nearest grid line, when the magnet is on.
    surface.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      surface.classList.add("drop-target");
    });
    surface.addEventListener("dragleave", () => surface.classList.remove("drop-target"));
    surface.addEventListener("drop", async (event) => {
      event.preventDefault();
      surface.classList.remove("drop-target");
      const dragged = event.dataTransfer.getData("text/plain");
      if (!dragged) return;
      const frame = surface.getBoundingClientRect();
      // The rect is the scaled box; the position the server keeps is in canvas
      // pixels, so the pointer's distance from the corner is divided back out.
      const scale = surfaceScale(surface);
      await placeOnDesktop(
        box,
        ctx,
        dragged,
        (event.clientX - frame.left) / scale,
        (event.clientY - frame.top) / scale
      );
    });

    // Right-clicking the desktop itself — not one of its icons, which answer for
    // themselves — is where the surface's own business lives: a box to put on it,
    // and the picture it works on. That picture is the box's resource, nowhere else.
    surface.addEventListener("contextmenu", (event) => {
      if (event.target.closest(".desktop-icon")) return;
      event.preventDefault();
      // Remember where on the canvas the click fell, so a box made from this menu
      // lands there instead of wherever the next free spot happens to be. The
      // canvas may be drawn smaller than it is, so the screen distance is divided
      // back out; the tile is centred under the pointer, and the magnet, if the
      // desktop has it on, still pulls the landing onto a line.
      const frame = surface.getBoundingClientRect();
      const scale = surfaceScale(surface);
      const spot = {
        x: Math.max(0, Math.round((event.clientX - frame.left) / scale - DESKTOP_DEFAULTS.tile / 2)),
        y: Math.max(0, Math.round((event.clientY - frame.top) / scale - DESKTOP_DEFAULTS.tile / 2)),
      };
      ctx.showMenu(
        event.clientX,
        event.clientY,
        boxMenuItems(box, [
          { label: `New box on ${box.name}`, run: () => ctx.newBox({ parent: box.uuid, place: spot }) },
          { separator: true },
          { label: "\ud83d\uddbc Set a background image\u2026", run: () => chooseBackground(box, ctx) },
          box.resource_uri
            ? {
                label: "Remove the background image",
                run: () => writeBackground(box, ctx, ""),
              }
            : null,
        ])
      );
    });
  },
});

/** Ask the file selector for a picture, and file what it answers with as the resource. */
function chooseBackground(box, ctx) {
  openFilePicker((chosen) => writeBackground(box, ctx, chosen), box.resource_uri || "");
}

/**
 * Write the desktop's background, which is simply its `resource_uri`. An empty
 * path takes it away. The view probes the picture before it claims one, so a
 * resource that turns out not to be an image leaves a plain desktop.
 */
async function writeBackground(box, ctx, chosen) {
  try {
    await api.updateBox(box.uuid, { resource_uri: chosen });
    box.resource_uri = chosen;
    ctx.status(chosen ? `${box.name} now works on ${chosen}` : `${box.name} has no background`);
    ctx.reload();
  } catch (error) {
    ctx.status(`cannot change the background: ${error.message}`, true);
  }
}

/**
 * Tell the server where an icon went, and redraw from what it answered with.
 * The server holds the magnet, so it, not the page, decides the final spot.
 */
async function placeOnDesktop(box, ctx, childUuid, x, y) {
  const edge = (value) => Math.round(Math.max(0, value));
  try {
    await api.desktopMove(box.uuid, childUuid, edge(x), edge(y));
    ctx.reload();
  } catch (error) {
    ctx.status(`cannot place that icon here: ${error.message}`, true);
  }
}

function desktopToolbar(box, options, ctx, count) {
  const bar = document.createElement("div");
  bar.className = "desktop-toolbar";

  const title = document.createElement("strong");
  title.textContent = `${box.icon} ${box.name}`;
  const tally = document.createElement("span");
  tally.className = "muted";
  tally.textContent = ` ${count} on the desktop`;

  const magnet = document.createElement("button");
  magnet.type = "button";
  magnet.dataset.action = "desktop-snap";
  magnet.setAttribute("aria-pressed", String(!!options.snap));
  magnet.textContent = options.snap ? "🧲 Snap: on" : "🧲 Snap: off";
  magnet.title = "Stick icons to the grid when they are dropped";
  magnet.addEventListener("click", () => ctx.saveSection("desktop", { snap: !options.snap }));

  const cell = Number(options.grid) || DESKTOP_DEFAULTS.grid;
  const group = document.createElement("span");
  group.className = "stepper";
  const label = document.createElement("span");
  label.textContent = "grid";
  const minus = document.createElement("button");
  minus.type = "button";
  minus.dataset.action = "desktop-grid-minus";
  minus.textContent = "−";
  const size = document.createElement("input");
  size.type = "number";
  size.min = "4";
  size.max = "256";
  size.step = "4";
  size.value = String(cell);
  size.dataset.action = "desktop-grid-value";
  size.setAttribute("aria-label", "Grid size in pixels");
  const plus = document.createElement("button");
  plus.type = "button";
  plus.dataset.action = "desktop-grid-plus";
  plus.textContent = "+";
  group.append(label, minus, size, plus);

  const add = document.createElement("button");
  add.type = "button";
  add.dataset.action = "desktop-new";
  add.textContent = "＋ New box here";
  add.addEventListener("click", () => ctx.newBox(box.uuid));

  bar.append(title, tally, magnet, group, add);

  const commit = (value) => {
    const next = Math.min(256, Math.max(4, Math.round(Number(value) || cell)));
    ctx.saveSection("desktop", { grid: next });
  };
  minus.addEventListener("click", () => commit(cell - 4));
  plus.addEventListener("click", () => commit(cell + 4));
  size.addEventListener("change", () => commit(size.value));
  size.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit(size.value);
    }
  });
  return bar;
}

/**
 * One icon on the surface. Pointer events do the dragging — not HTML5 drag and
 * drop, which cannot follow a move — while `draggable` stays off so the tile
 * itself is never dragged. Double-click opens the box; the corner grip sizes it.
 */
function desktopIcon(box, child, rect, options, ctx) {
  const tile = document.createElement("div");
  tile.className = "desktop-icon";
  tile.dataset.uuid = child.uuid;
  tile.tabIndex = 0;
  tile.style.left = `${rect.x}px`;
  tile.style.top = `${rect.y}px`;
  tile.style.width = `${rect.width}px`;
  tile.style.height = `${rect.height}px`;
  tile.title = `Drag to move · double-click to open ${child.name}`;

  tile.append(iconElement(child, "desktop-glyph"));
  const caption = document.createElement("span");
  caption.className = "desktop-caption";
  caption.textContent = child.name;
  tile.append(caption);

  const grip = document.createElement("span");
  grip.className = "desktop-grip";
  grip.title = "Drag to resize";
  tile.append(grip);

  /**
   * Both gestures start the same way: remember where the pointer and the tile
   * began, then write the tile as the pointer moves, magnetised, and commit once
   * on release. A click that never moved is left to the browser as a click.
   */
  function follow(event, onMove, onCommit) {
    const origin = {
      x: event.clientX,
      y: event.clientY,
      left: tile.offsetLeft,
      top: tile.offsetTop,
      width: tile.offsetWidth,
      height: tile.offsetHeight,
      // The surface may be drawn smaller than the canvas it is. The pointer travels
      // in screen pixels; the tile is placed in canvas pixels, so the two are only
      // the same when the canvas is drawn at one.
      scale: surfaceScale(tile),
    };
    let latest = null;
    tile.setPointerCapture(event.pointerId);
    tile.classList.add("handling");

    const move = (next) => {
      latest = onMove(origin, next);
    };
    const release = () => {
      tile.removeEventListener("pointermove", move);
      tile.removeEventListener("pointerup", release);
      tile.removeEventListener("pointercancel", release);
      tile.classList.remove("handling");
      if (latest) onCommit(latest);
    };
    tile.addEventListener("pointermove", move);
    tile.addEventListener("pointerup", release);
    tile.addEventListener("pointercancel", release);
  }

  tile.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    follow(
      event,
      (origin, next) => ({
        x: Math.max(0, magnetise(origin.left + (next.clientX - origin.x) / origin.scale, options)),
        y: Math.max(0, magnetise(origin.top + (next.clientY - origin.y) / origin.scale, options)),
      }),
      (where) => {
        tile.style.left = `${where.x}px`;
        tile.style.top = `${where.y}px`;
        if (where.x === rect.x && where.y === rect.y) return; // put back where it was
        placeOnDesktop(box, ctx, child.uuid, where.x, where.y);
      }
    );
  });

  grip.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    follow(
      event,
      (origin, next) => ({
        width: Math.min(
          4096,
          Math.max(32, Math.round(origin.width + (next.clientX - origin.x) / origin.scale))
        ),
        height: Math.min(
          4096,
          Math.max(32, Math.round(origin.height + (next.clientY - origin.y) / origin.scale))
        ),
      }),
      (size) => {
        tile.style.width = `${size.width}px`;
        tile.style.height = `${size.height}px`;
        if (size.width === rect.width && size.height === rect.height) return;
        api
          .desktopResize(box.uuid, child.uuid, size.width, size.height)
          .then(() => ctx.reload())
          .catch((error) => ctx.status(`cannot size that icon: ${error.message}`, true));
      }
    );
  });

  tile.addEventListener("click", () => {
    ctx.status(`${child.name} selected — double-click to open it`);
    document
      .querySelectorAll(".desktop-icon")
      .forEach((other) => other.classList.toggle("selected", other === tile));
  });
  tile.addEventListener("dblclick", () =>
    ctx.open(child.uuid, { trail: ctx.trail() })
  );
  tile.addEventListener("keydown", (event) => {
    const cell = Number(options.grid) || DESKTOP_DEFAULTS.grid;
    const step = {
      ArrowLeft: [-cell, 0],
      ArrowRight: [cell, 0],
      ArrowUp: [0, -cell],
      ArrowDown: [0, cell],
    }[event.key];
    if (step) {
      event.preventDefault();
      placeOnDesktop(box, ctx, child.uuid, tile.offsetLeft + step[0], tile.offsetTop + step[1]);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      ctx.open(child.uuid, { trail: ctx.trail() });
    }
  });
  tile.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    event.stopPropagation();
    ctx.showMenu(
      event.clientX,
      event.clientY,
      boxMenuItems(child, [
        { label: `New box in ${box.name}`, run: () => ctx.newBox({ parent: box.uuid }) },
        { separator: true },
        { label: `Remove from ${box.name}`, run: () => ctx.removeFrom(box, child) },
      ])
    );
  });
  return tile;
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
    const token = paneGeneration;
    let children = [];
    try {
      children = await api.children(box.uuid);
    } catch (error) {
      canvas.append(emptyMessage(`Cannot read the root: ${error.message}`));
      return;
    }
    if (!stillOnScreen(token)) return;
    // The list gives the brief of each child — enough to draw a tile, not enough to
    // be the subject of the page: it carries no children, no metadata, no parents.
    // The one box on screen is fetched whole.
    let child = children[0] || null;
    if (child) {
      try {
        child = await api.box(child.uuid);
      } catch (error) {
        // The brief is still worth showing; say what could not be read rather than
        // leaving the slot blank.
        console.warn(`the root could not read its child whole: ${error.message}`);
      }
      if (!stillOnScreen(token)) return;
    }

    const holder = document.createElement("div");
    holder.className = "root-view";
    holder.dataset.uuid = box.uuid;
    canvas.append(holder);

    const bar = document.createElement("div");
    bar.className = "root-bar";
    const label = document.createElement("span");
    label.className = "muted";
    label.append(
      child ? "root › " : "root — nothing filed in it",
      ...(child ? [iconElement(child), document.createTextNode(` ${child.name}`)] : [])
    );
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
      const modify = document.createElement("button");
      modify.type = "button";
      modify.textContent = "Modify…";
      modify.addEventListener("click", () => ctx.edit(child));
      bar.append(open, modify, remove);
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
      // Not embedded: the root's child is the subject of the page, not a tile in
      // somebody else's layout. Asking it the way a cell does makes a plain box
      // answer with its icon and a desktop answer without its toolbar.
      renderContent(block, child, { depth: 1, embedded: false }).catch((error) => {
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
      holder.classList.remove("drop-target");
      // The view of the child sits inside this holder, so its drops bubble here.
      // One that the child already took — an icon onto a desktop, a box into a
      // folder — is not the root's to answer; taking it would replace the very box
      // the drop landed on.
      if (event.defaultPrevented) return;
      event.preventDefault();
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

const FOLDER_DEFAULTS = { layout: "list", history: [] };

/** How many searches a folder remembers. Long enough to be useful, short enough to read. */
const FOLDER_HISTORY = 8;

function folderOptions(box) {
  const saved = (box.config || {}).folder || {};
  // A config written by hand or by an older build may hold anything under `history`.
  const history = Array.isArray(saved.history)
    ? saved.history
        .filter((entry) => typeof entry === "string" && entry.trim() !== "")
        .slice(0, FOLDER_HISTORY)
    : [];
  return { ...FOLDER_DEFAULTS, ...saved, history };
}

/**
 * A container: its children as a list or as icons, like a file explorer. Double-click
 * opens a child in this pane, replacing the box that contained it.
 *
 * Three things live above the list. A search box, which narrows what is shown by
 * name or by type — it starts empty every time the folder is opened, because it is a
 * look at the shelf, not a saved view; what is kept is the list of searches already
 * tried. A drop target, so a box dragged out of the left panel lands in this folder.
 * And, for a dynamic folder, the filter that decides its members: membership is the
 * filter's doing, so the folder says what it asks for and lets you change it.
 */
const folderView = {
  async render(canvas, box, ctx, mode, opts = {}) {
    const token = paneGeneration;
    const options = folderOptions(box);
    const kind = typeOf(box);
    const dynamic = !!kind.resolves_membership;
    const fixed = !!kind.filter_is_fixed;

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

    const search = document.createElement("input");
    search.type = "search";
    search.className = "child-search";
    search.placeholder = "name or type";
    search.setAttribute("aria-label", `Filter the boxes inside ${box.name}`);
    const historyId = `folder-history-${box.uuid}`;
    search.setAttribute("list", historyId);
    const datalist = document.createElement("datalist");
    datalist.id = historyId;

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
    add.addEventListener("click", () => ctx.newBox({ parent: box.uuid }));
    // Inside a cell or a root block there is no room, and no reason, to change
    // what the contained folder is showing. The search box is shown everywhere:
    // finding one child among many is exactly what a small pane needs.
    toolbar.append(title, count, search, ...(opts.embedded ? [] : [toggle, add]));

    const holder = document.createElement("div");
    holder.className = "children";
    wrap.append(toolbar, holder, datalist);

    // Anything dropped in this folder is filed in it. Preventing the default is
    // also what claims the drop: without it the root, whose holder wraps this
    // view, would take the box as its own child instead.
    wrap.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      wrap.classList.add("drop-target");
    });
    wrap.addEventListener("dragleave", () => wrap.classList.remove("drop-target"));
    wrap.addEventListener("drop", async (event) => {
      event.preventDefault();
      wrap.classList.remove("drop-target");
      const dragged = event.dataTransfer.getData("text/plain");
      if (!dragged || dragged === box.uuid) return;
      try {
        await api.link(box.uuid, dragged);
        ctx.status(`filed in ${box.name}`);
        ctx.reload();
      } catch (error) {
        ctx.status(`cannot file it in ${box.name}: ${error.message}`, true);
      }
    });

    let children = [];
    try {
      children = await api.children(box.uuid);
    } catch (error) {
      holder.append(emptyMessage(`Cannot list the children: ${error.message}`));
      return;
    }
    if (!stillOnScreen(token)) return;

    function childMatches(child, term) {
      if (!term) return true;
      const childKind = state.types.get(child.box_type) || {};
      return (
        child.name.toLowerCase().includes(term) ||
        String(child.box_type).toLowerCase().includes(term) ||
        String(childKind.label || "").toLowerCase().includes(term)
      );
    }

    function draw() {
      const term = search.value.trim().toLowerCase();
      const shown = children.filter((child) => childMatches(child, term));
      count.textContent = term
        ? ` ${shown.length} of ${children.length} inside`
        : ` ${children.length} inside`;
      holder.replaceChildren();
      if (!children.length) {
        holder.append(emptyMessage("Nothing in here yet — drag a box from the panel on the left."));
        return;
      }
      if (!shown.length) {
        holder.append(emptyMessage(`Nothing inside matches “${search.value.trim()}”.`));
        return;
      }
      shown.forEach((child) => holder.append(childTile(child, box, ctx)));
    }

    function paintHistory() {
      datalist.replaceChildren();
      options.history.forEach((entry) => {
        const option = document.createElement("option");
        option.value = entry;
        datalist.append(option);
      });
    }

    function rememberSearch() {
      const term = search.value.trim();
      if (!term) return;
      const rest = options.history.filter(
        (entry) => entry.toLowerCase() !== term.toLowerCase()
      );
      options.history = [term, ...rest].slice(0, FOLDER_HISTORY);
      paintHistory();
      // Saved without redrawing the pane: a redraw would rebuild this search box
      // empty, and the term being typed would vanish under the fingers.
      ctx.saveSection("folder", { history: options.history }, { rerender: false });
    }

    search.addEventListener("input", draw);
    search.addEventListener("change", rememberSearch);
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        rememberSearch();
      }
      // Escape empties the box, which is the way out of a filter.
      if (event.key === "Escape" && search.value) {
        event.stopPropagation();
        search.value = "";
        draw();
      }
    });
    paintHistory();
    draw();

    if (dynamic) {
      const bar = document.createElement("div");
      bar.className = "filter-bar";
      const says = document.createElement("span");
      says.className = "filter-says";
      bar.append(says);
      wrap.prepend(bar);

      if (fixed) {
        // A cabinet is defined as the whole library: no filter to read, none to set.
        says.textContent = "holds every box in the library, and this one";
      } else {
        await renderFilterEditor(bar, says, box, ctx, token);
      }
    }
  },
};

/**
 * The line above a dynamic folder's list: what its filter asks for, in the words
 * the server uses, and the way to change it. The wording comes from the API because
 * which types count as which belongs to the type hierarchy, not to this page.
 */
async function renderFilterEditor(bar, says, box, ctx, token) {
  let info = null;
  try {
    info = await api.filter(box.uuid);
  } catch (error) {
    says.textContent = `filter could not be read: ${error.message}`;
    return;
  }
  if (!stillOnScreen(token)) return;

  says.textContent = info.usable ? `holds every box where ${info.describes}` : info.describes;
  says.dataset.usable = String(info.usable);

  const nameInput = document.createElement("input");
  nameInput.type = "search";
  nameInput.placeholder = "name contains";
  nameInput.value = info.filter.name || "";
  nameInput.setAttribute("aria-label", "Names must contain");

  const typeSelect = document.createElement("select");
  typeSelect.setAttribute("aria-label", "Type must be");
  const anyType = document.createElement("option");
  anyType.value = "";
  anyType.textContent = "any type";
  typeSelect.append(anyType);
  [...state.types.values()]
    .filter((entry) => !entry.abstract)
    .forEach((entry) => {
      const option = document.createElement("option");
      option.value = entry.key;
      option.textContent = `${entry.icon} ${entry.label}`;
      typeSelect.append(option);
    });
  typeSelect.value = info.filter.type || "";

  const apply = document.createElement("button");
  apply.type = "button";
  apply.dataset.action = "apply-filter";
  apply.textContent = "Apply filter";
  apply.addEventListener("click", async () => {
    const wanted = {};
    if (nameInput.value.trim()) wanted.name = nameInput.value.trim();
    if (typeSelect.value) wanted.type = typeSelect.value;
    try {
      const answer = await api.setFilter(box.uuid, wanted);
      ctx.status(`filter set: ${answer.describes}`);
      ctx.reload();
    } catch (error) {
      ctx.status(`filter refused: ${error.message}`, true);
    }
  });

  const again = document.createElement("button");
  again.type = "button";
  again.dataset.action = "resolve-folder";
  again.textContent = "Re-apply now";
  again.title =
    "Write this folder's members again from its filter. Needed after a box was filed in here by hand.";
  again.addEventListener("click", async () => {
    try {
      const answer = await api.resolveFolder(box.uuid);
      ctx.status(`filter re-applied: ${answer.members} box${answer.members === 1 ? "" : "es"}`);
      ctx.reload();
    } catch (error) {
      ctx.status(`could not re-apply the filter: ${error.message}`, true);
    }
  });

  bar.append(nameInput, typeSelect, apply, again);
}

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
    ctx.open(child.uuid, { trail: ctx.trail() })
  );
  item.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", child.uuid);
    event.dataTransfer.effectAllowed = "move";
  });
  item.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    event.stopPropagation();
    ctx.showMenu(
      event.clientX,
      event.clientY,
      boxMenuItems(
        { ...child, uuid: child.uuid },
        [
          { label: "New box in " + parent.name, run: () => ctx.newBox({ parent: parent.uuid }) },
          { separator: true },
          // work_to_do.md § FolderResource: drop the parent/child link, keep the box.
          { label: `Remove from ${parent.name}`, run: () => ctx.removeFrom(parent, child) },
        ]
      )
    );
  });
  return item;
}

registerView("folder", folderView);

/* ------------------------------------------------------------- explorer ---- */

const dirNav = new Map(); // box uuid -> { sub, preview }, so a re-render keeps the place

/**
 * A `directory` box, browsed for real: the files on disk, in place of the box's
 * own children. Double-click a directory to go deeper — still the same box's
 * content — and double-click a file to open it here in whichever viewer fits:
 * image, text, markdown, PDF, video, or an archive listing. Right-click offers
 * to make a box out of it, with the type already inferred.
 */
const directoryView = {
  async render(canvas, box, ctx, opts = {}) {
    const nav = dirNav.get(box.uuid) || { sub: "", preview: null };
    dirNav.set(box.uuid, nav);

    const wrap = document.createElement("div");
    wrap.className = "fs-view";
    canvas.append(wrap);

    const bar = document.createElement("div");
    bar.className = "fs-bar";
    wrap.append(bar);

    const pane = document.createElement("div");
    pane.className = "fs-pane";
    wrap.append(pane);

    function drawBar(title, actions) {
      bar.replaceChildren();
      const label = document.createElement("span");
      label.className = "fs-path";
      label.textContent = title;
      bar.append(label);
      actions.forEach((action) => bar.append(action));
    }

    function previewOf(entry) {
      const synthetic = {
        uuid: null,
        name: entry.name,
        icon: entry.icon,
        box_type: entry.box_type,
        resource_uri: entry.path || entry.sub,
        config: {},
        metadata: {},
      };
      const VIEW_BY_KIND = {
        image: "image",
        text: entry.box_type === "markdown" ? "markdown" : "text",
        pdf: "pdf",
        video: "video",
        archive: "archive",
      };
      const viewKey = VIEW_BY_KIND[entry.kind] || entry.box_type || "file";
      const view = views.get(viewKey) || viewFor({ ...synthetic, box_type: viewKey }) || views.get("*");
      synthetic.box_type = viewKey;
      const src = api.contentUrl(box.uuid, entry.sub);
      pane.replaceChildren();
      view.render(pane, synthetic, ctx, "content", {
        embedded: true,
        depth: 1,
        src,
        archiveUrl: api.archiveUrl(box.uuid, entry.sub),
      });
      drawBar(`${entry.icon} ${entry.name}`, [
        button("← Back to files", () => {
          nav.preview = null;
          draw();
        }),
      ]);
    }

    function button(text, run) {
      const el2 = document.createElement("button");
      el2.type = "button";
      el2.textContent = text;
      el2.addEventListener("click", run);
      return el2;
    }

    function menuFor(entry, event) {
      const items = [
        entry.kind === "dir"
          ? { label: "Open folder", run: () => enter(entry) }
          : { label: `Open as ${entry.box_type}`, run: () => openEntry(entry) },
        { separator: true },
        {
          label: `New ${entry.box_type} box from this ${entry.kind === "dir" ? "folder" : "file"}`,
          run: () =>
            ctx.newBox({
              parent: box.uuid,
              boxType: entry.box_type,
              name: entry.name.replace(/\.[^.]+$/, ""),
              resource: entry.path || "",
            }),
        },
      ];
      ctx.showMenu(event.clientX, event.clientY, items);
    }

    function openEntry(entry) {
      nav.preview = entry;
      previewOf(entry);
    }

    function enter(entry) {
      nav.sub = entry.sub;
      nav.preview = null;
      draw();
    }

    async function draw() {
      if (nav.preview) {
        previewOf(nav.preview);
        return;
      }
      let payload;
      try {
        payload = await api.listing(box.uuid, nav.sub);
      } catch (error) {
        drawBar(box.name, []);
        pane.replaceChildren(emptyMessage(`Cannot list ${box.resource_uri}: ${error.message}`));
        return;
      }
      const up = button("↑", () => {
        nav.sub = payload.parent || "";
        draw();
      });
      up.disabled = payload.parent === null;
      up.title = "Up one folder";
      const root = button("▣ box folder", () => {
        nav.sub = "";
        draw();
      });
      drawBar(`📁 ${payload.at}`, [up, root]);
      pane.replaceChildren();
      if (!payload.entries.length) {
        pane.append(emptyMessage("No files here."));
        return;
      }
      payload.entries.forEach((entry) => {
        const row = document.createElement("div");
        row.className = `fs-row ${entry.kind}`;
        row.dataset.sub = entry.sub;
        const glyph = iconElement({ uuid: null, icon: entry.icon, config: {}, box_type: entry.box_type });
        const name = document.createElement("span");
        name.className = "fs-name";
        name.textContent = entry.name;
        const kind = document.createElement("span");
        kind.className = "fs-kind muted";
        kind.textContent = entry.kind === "dir" ? "folder" : entry.box_type;
        const size = document.createElement("span");
        size.className = "fs-size muted";
        size.textContent = entry.kind === "dir" ? "" : formatSize(entry.size);
        row.append(glyph, name, kind, size);
        row.addEventListener("dblclick", () => (entry.kind === "dir" ? enter(entry) : openEntry(entry)));
        row.addEventListener("click", () => {
          pane.querySelectorAll(".fs-row").forEach((other) => other.classList.toggle("selected", other === row));
        });
        row.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          event.stopPropagation();
          menuFor(entry, event);
        });
        pane.append(row);
      });
    }

    await draw();
  },
};

registerView("directory", directoryView);

/* ---------------------------------------------------------------- text ---- */

const TEXT_DEFAULTS = { wrap: true, crop: false, font_size: 14 };

/** How the text should look: saved settings over the defaults. */
function textOptions(box) {
  return { ...TEXT_DEFAULTS, ...((box.config || {}).text || {}) };
}

registerView("text", {
  async render(canvas, box, ctx, mode, opts = {}) {
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
      body.textContent = await api.textAt(opts.src || api.rawUrl(box.uuid));
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

/* ----------------------------------------------------------------- pdf ---- */

/** A PDF is shown in the browser's own viewer, opened on the file. */
registerView("pdf", {
  render(canvas, box, ctx, mode, opts = {}) {
    const src = opts.src || api.rawUrl(box.uuid);
    if (!box.resource_uri) {
      canvas.append(emptyMessage("This PDF box has no resource to open."));
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "pdf-view";
    const frame = document.createElement("iframe");
    frame.className = "pdf-frame";
    frame.src = `${src}#view=FitH`;
    frame.title = box.name;
    frame.addEventListener("error", () => {
      wrap.replaceChildren(emptyMessage(`The viewer could not load ${box.resource_uri}.`));
    });
    const bar = document.createElement("div");
    bar.className = "pdf-bar muted";
    const open = document.createElement("a");
    open.href = src;
    open.target = "_blank";
    open.rel = "noreferrer";
    open.textContent = box.resource_uri;
    bar.append(document.createTextNode("PDF · "), open);
    wrap.append(frame, bar);
    canvas.append(wrap);
    if (opts.embedded) {
      // A cell is small: give the frame a fair shot at being usable.
      frame.setAttribute("scrolling", "auto");
    }
  },
});

/* ------------------------------------------------------- video, archive ---- */

registerView("video", {
  render(canvas, box, ctx, mode, opts = {}) {
    const wrap = document.createElement("div");
    wrap.className = "video-view";
    const video = document.createElement("video");
    video.controls = true;
    video.src = opts.src || api.rawUrl(box.uuid);
    video.addEventListener("error", () =>
      wrap.replaceChildren(
        unableMessage(box, opts, "This browser cannot play the video.")
      )
    );
    wrap.append(video);
    canvas.append(wrap);
  },
});

/** A zip or tar, listed like a folder. Members are read-only here. */
registerView("archive", {
  async render(canvas, box, ctx, mode, opts = {}) {
    const token = paneGeneration;
    let payload;
    try {
      payload = opts.archiveUrl
        ? await request(opts.archiveUrl)
        : await api.archive(box.uuid, opts.sub || "");
    } catch (error) {
      canvas.append(emptyMessage(`Cannot open the archive: ${error.message}`));
      return;
    }
    if (!stillOnScreen(token)) return;
    const wrap = document.createElement("div");
    wrap.className = "archive-view";
    const head = document.createElement("p");
    head.className = "muted";
    head.textContent = `${payload.entries.length} members of ${box.name}`;
    wrap.append(head);
    if (!payload.entries.length) {
      wrap.append(emptyMessage("This archive is empty."));
    }
    payload.entries.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "fs-row";
      const glyph = document.createElement("span");
      glyph.className = "fs-icon";
      glyph.textContent = entry.icon;
      const name = document.createElement("span");
      name.className = "fs-name";
      name.textContent = entry.name;
      const size = document.createElement("span");
      size.className = "fs-size muted";
      size.textContent = entry.kind === "dir" ? "-" : formatSize(entry.size);
      row.append(glyph, name, size);
      wrap.append(row);
    });
    canvas.append(wrap);
  },
});

function formatSize(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** A file with no viewer: what it is, how big, and a link to take it away. */
registerView("file", {
  render(canvas, box, ctx, mode, opts = {}) {
    canvas.append(
      unableMessage(box, opts, `Boxganizer has no viewer for ${box.name || "this file"}.`)
    );
  },
});

/** A card explaining that the file cannot be shown, with the file still reachable. */
function unableMessage(box, opts, reason) {
  const card = document.createElement("div");
  card.className = "empty-message unable";
  const glyph = document.createElement("span");
  glyph.className = "glyph";
  glyph.textContent = box.icon || "\u{1f4c4}";
  const text = document.createElement("p");
  text.textContent = reason;
  card.append(glyph, text);
  const src = opts.src || (box.uuid ? api.rawUrl(box.uuid) : null);
  if (src) {
    const link = document.createElement("a");
    link.href = src;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = box.resource_uri || "Open the file";
    card.append(link);
  }
  return card;
}

/* ------------------------------------------------------------- browser ---- */

/**
 * A `browser` (or plain `web`) box: the page, framed.
 *
 * The frame is sandboxed to scripts, forms, popups and its own origin — enough
 * for a normal page, and it has no `allow-top-navigation`, so a framed site
 * cannot send the boxganizer window somewhere else. Cross-origin means it cannot
 * read this page either.
 *
 * Framing is not always allowed: a site that sends `X-Frame-Options` or a CSP
 * `frame-ancestors` shows nothing, and no script can tell from outside. So the
 * URL is always said out loud under the frame, with a link that opens it for real.
 */
const browserView = {
  render(canvas, box, ctx, mode, opts = {}) {
    if (!box.resource_uri) {
      canvas.append(emptyMessage("This browser box has no address to open."));
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "browser-view";
    let current = box.resource_uri;

    const bar = document.createElement("div");
    bar.className = "browser-bar";
    const address = document.createElement("input");
    address.type = "url";
    address.className = "browser-url";
    address.value = current;
    address.setAttribute("aria-label", "Address");
    const go = document.createElement("button");
    go.type = "button";
    go.textContent = "Go";
    const reload = document.createElement("button");
    reload.type = "button";
    reload.textContent = "\u27f3";
    reload.title = "Reload this frame";
    const openTab = document.createElement("a");
    openTab.textContent = "\u2197 new tab";
    openTab.href = current;
    openTab.target = "_blank";
    openTab.rel = "noreferrer";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save address";
    save.hidden = true;
    bar.append(reload, address, go, save, openTab);

    const frame = document.createElement("iframe");
    frame.className = "browser-frame";
    frame.setAttribute(
      "sandbox",
      "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
    );
    frame.src = current;

    const note = document.createElement("p");
    note.className = "browser-note muted";
    note.textContent = "If the frame stays blank, the site refuses to be shown inside another page.";

    function navigate(to) {
      const url = String(to || "").trim();
      if (!url) return;
      current = /^https?:\/\//i.test(url) ? url : `https://${url}`;
      address.value = current;
      openTab.href = current;
      frame.src = current;
      save.hidden = current === (box.resource_uri || "").trim();
    }

    go.addEventListener("click", () => navigate(address.value));
    address.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        navigate(address.value);
      }
    });
    address.addEventListener("input", () => {
      save.hidden = address.value.trim() === (box.resource_uri || "").trim();
    });
    reload.addEventListener("click", () => {
      frame.src = current;
    });
    save.addEventListener("click", async () => {
      try {
        await api.updateBox(box.uuid, { resource_uri: address.value.trim() });
        box.resource_uri = address.value.trim();
        save.hidden = true;
        ctx.status(`address saved on ${box.name}`);
        refreshSide();
      } catch (error) {
        ctx.status(error.message, true);
      }
    });

    wrap.append(bar, frame, note);
    canvas.append(wrap);
    if (opts.embedded) note.remove(); // a cell is too small for the disclaimer
  },
};

registerView("browser", browserView);
registerView("web", browserView);

/* ------------------------------------------------------------------ mqtt ---- */

/** What the stream reports, in the words a person reading a status wants. */
const MQTT_STATES = {
  connecting: "connecting",
  live: "listening",
  value: "listening",
  retrying: "retrying",
  error: "refused",
};

/** How many of the last messages the opened box keeps on screen. */
const MQTT_HISTORY = 8;

/**
 * A `mqtt` box: the last message published on its topic, and every one after it.
 *
 * The value endpoint answers from what the server already heard, so the box is
 * never blank while the subscription is being made; the stream then pushes each
 * message as it arrives. The address on screen is the redacted one — a box may
 * hold `mqtt://user:secret@host/topic`, and the secret stays in the box.
 *
 * A grid cell shows the last value alone. One subscription per cell would be one
 * per box on the wall, and the opened box is where the listening belongs.
 */
const mqttView = {
  async render(canvas, box, ctx, mode, opts = {}) {

    const token = paneGeneration;

    const wrap = document.createElement("div");
    wrap.className = "mqtt-view";
    wrap.dataset.uuid = box.uuid;

    const chip = document.createElement("span");
    chip.className = "mqtt-state";
    chip.dataset.state = "connecting";
    chip.textContent = "connecting";
    const topic = document.createElement("span");
    topic.className = "mqtt-topic";
    const address = document.createElement("span");
    address.className = "mqtt-address muted";
    const head = document.createElement("div");
    head.className = "mqtt-head";
    head.append(chip, topic, address);

    const value = document.createElement("span");
    value.className = "mqtt-value";
    value.textContent = "\u2014";
    const badge = document.createElement("span");
    badge.className = "mqtt-badge";
    badge.hidden = true;
    const when = document.createElement("span");
    when.className = "mqtt-when muted";
    const readout = document.createElement("div");
    readout.className = "mqtt-readout";
    readout.append(value, badge, when);

    const detail = document.createElement("p");
    detail.className = "mqtt-detail muted";

    const log = document.createElement("ul");
    log.className = "mqtt-log";

    wrap.append(head, readout, detail, log);
    canvas.append(wrap);

    let info;
    try {
      info = await api.mqtt(box.uuid);
    } catch (error) {
      if (!stillOnScreen(token)) return;
      wrap.replaceChildren(emptyMessage(`This sensor cannot be read: ${error.message}`));
      return;
    }
    if (!stillOnScreen(token)) return; // the page moved on mid-fetch

    const target = info.target || {};
    topic.textContent = target.topic || "";
    topic.title = `topic ${target.topic}`;
    address.textContent = info.address || "";
    if (target.credentials) {
      address.title = `${address.textContent} — the user and password stay in the box`;
    }

    function show(reading, { newest = false } = {}) {
      value.textContent = reading.value === "" ? "(empty message)" : reading.value;
      value.dataset.encoding = reading.encoding || "utf-8";
      // The note explains *this* message, so a text message after a binary one
      // must not be left wearing the binary one's tooltip.
      value.title =
        reading.encoding && reading.encoding !== "utf-8"
          ? `the broker sent bytes that are not text, shown as ${reading.encoding}`
          : "";
      badge.hidden = !reading.retained;
      badge.textContent = "retained";
      badge.title = "the broker kept this message for a subscription that started later";
      const at = reading.received_at ? new Date(reading.received_at) : null;
      when.textContent = at ? at.toLocaleTimeString() : "";
      if (at) when.title = at.toLocaleString();
      if (!newest) return;

      const line = document.createElement("li");
      const clock = document.createElement("time");
      clock.textContent = at ? at.toLocaleTimeString() : "";
      if (at) clock.dateTime = reading.received_at;
      const text = document.createElement("span");
      text.textContent = reading.value === "" ? "(empty message)" : reading.value;
      line.append(clock, text);
      log.prepend(line);
      while (log.children.length > MQTT_HISTORY) log.lastElementChild.remove();
    }

    function apply(next, reading, note) {
      chip.dataset.state = next;
      chip.textContent = MQTT_STATES[next] || next;
      detail.textContent = note || "";
      if (reading) show(reading, { newest: true });
    }

    if (info.reading) show(info.reading);
    if (opts.cell) {
      // A cell never subscribes, so it must not claim to be connecting. It shows
      // what the server last heard — and says so — or admits it has nothing yet.
      chip.dataset.state = info.reading ? "cached" : "idle";
      chip.textContent = info.reading ? "last value" : "no value yet";
      detail.textContent = info.reading ? "" : `open the box to listen for ${target.topic}`;
      return;
    }

    apply("connecting", null, `listening for ${target.topic}`);

    const source = new EventSource(api.mqttStreamUrl(box.uuid));
    holdLive(() => source.close());

    const accept = (event) => {
      if (!stillOnScreen(token)) {
        source.close();
        return;
      }
      // A dropped connection is an `error` event too, and it carries no body: the
      // browser is already going back to the broker, so that is a retry, not a refusal.
      if (typeof event.data !== "string") {
        apply("retrying", null, "the stream dropped; going back to the broker");
        return;
      }
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        apply("error", null, "the stream sent something that is not a reading");
        return;
      }
      apply(payload.state || event.type, payload.reading || null, payload.detail || "");
    };

    ["connecting", "live", "value", "retrying", "error"].forEach((name) => {
      source.addEventListener(name, accept);
    });
  },
};

registerView("mqtt", mqttView);

/* -- mindmap -------------------------------------------------------------------
 *
 * One box in the middle, its children as branches growing left and right, and every
 * branch a thought that can be broken into sub-branches. The nodes are boxes and the
 * lines between them are the ordinary links: nothing here is a private idea that only
 * this view knows about. What the map keeps for itself, in `config.mindmap`, is which
 * side a branch grows on and the order of the children in a branch — see
 * `boxganizer.mindmap`.
 *
 * The keys are the point of it. Click once into the picture and then: Insert makes a
 * node under the one in hand, Enter makes one beside it, the arrows walk the map,
 * Ctrl+up and down shuffle a node among its siblings, and Ctrl+left and right send it
 * deeper into the map or back out towards the centre — the direction read off the side
 * the branch grows on, so Ctrl always pushes away from the centre or pulls towards it.
 */

/**
 * The measure of the picture, in CSS pixels.
 *
 * Widths are fixed and a label that overflows is cut with an ellipsis, rather than
 * measured after it is painted: a node that grows when its words arrive moves every
 * node below it, and a picture that jumps about cannot be walked by keyboard.
 */
const MINDMAP = {
  width: 178,
  height: 38,
  centreWidth: 236,
  centreHeight: 56,
  column: 62, // air between one depth and the next
  row: 12, // air between siblings
  branch: 28, // air between the branches off the centre
  pad: 56,
  /** One colour per branch. A thought keeps the colour it started with, all the way down. */
  palette: [
    "#c62828",
    "#1565c0",
    "#2e7d32",
    "#ef6c00",
    "#6a1b9a",
    "#00838f",
    "#8d6e63",
    "#455a64",
  ],
};

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Lay the map out: a tidy tree down each side, the centre between them.
 *
 * Each node is given a row record — its place, its depth, which side it grows on, the
 * colour of the branch it belongs to, and its parent and children rows. Leaves are
 * stacked as they come and a parent sits opposite the middle of its own children,
 * which is what keeps a branch readable: the line of descent is never crossed.
 */
function mindmapLayout(map) {
  const rows = [];
  const byUuid = new Map();

  function walk(node, depth, colour, sign, parent) {
    const row = {
      node,
      depth,
      colour,
      sign,
      parent,
      children: [],
      w: depth === 1 ? MINDMAP.width : MINDMAP.width - 14,
      h: MINDMAP.height,
      cy: 0,
    };
    rows.push(row);
    byUuid.set(node.uuid, row);
    if (parent) parent.children.push(row);

    const below = node.children || [];
    if (!below.length) {
      row.cy = cursor + row.h / 2;
      cursor += row.h + MINDMAP.row;
    } else {
      below.forEach((child) => walk(child, depth + 1, colour, sign, row));
      row.cy = (row.children[0].cy + row.children[row.children.length - 1].cy) / 2;
      // A parent with children is never taller than the stack of them, so the cursor
      // has already moved past everything this row needs.
    }
    // Outward from the centre by one column per depth.
    row.x =
      sign *
      (MINDMAP.centreWidth / 2 + MINDMAP.column + (depth - 1) * (MINDMAP.width + MINDMAP.column));
    return row;
  }

  let cursor = 0;
  const branches = (map.children || []).map((child, index) => ({
    child,
    colour: MINDMAP.palette[index % MINDMAP.palette.length],
    sign: child.side === "left" ? -1 : 1,
  }));

  /**
   * Each half is stacked from the same top line, and the stack is reset between them:
   * one cursor for both would draw the left half, then carry on below it with the
   * right, which is a list, not a mindmap.
   */
  const branchRows = new Map();
  [-1, 1].forEach((sign) => {
    cursor = 0;
    branches
      .filter((branch) => branch.sign === sign)
      .forEach((branch) => {
        const row = walk(branch.child, 1, branch.colour, sign, null);
        branchRows.set(branch.child.uuid, row);
        cursor += MINDMAP.branch - MINDMAP.row;
      });
  });

  const centre = {
    node: map,
    depth: 0,
    colour: "#20201f",
    sign: 1,
    parent: null,
    children: [],
    w: MINDMAP.centreWidth,
    h: MINDMAP.centreHeight,
    x: 0,
    cy: 0,
  };
  rows.unshift(centre);
  byUuid.set(map.uuid, centre);
  // The branches hang off the centre, both for the curve and for the keys: a branch
  // with no parent has nowhere to go back to.
  branches.forEach((branch) => {
    const row = branchRows.get(branch.child.uuid);
    if (row) {
      row.parent = centre;
      centre.children.push(row);
    }
  });
  const ends = rows.filter((row) => row !== centre);
  centre.cy = ends.length
    ? (Math.min(...ends.map((row) => row.cy)) + Math.max(...ends.map((row) => row.cy))) / 2
    : centre.h / 2;

  // Shift the whole picture into positive space, with air around it.
  const left = Math.min(...rows.map((row) => row.x - row.w / 2)) - MINDMAP.pad;
  const top = Math.min(...rows.map((row) => row.cy - row.h / 2)) - MINDMAP.pad;
  const width = Math.max(...rows.map((row) => row.x + row.w / 2)) - left + MINDMAP.pad;
  const height = Math.max(...rows.map((row) => row.cy + row.h / 2)) - top + MINDMAP.pad;
  rows.forEach((row) => {
    row.x -= left;
    row.cy -= top;
  });

  return {rows, byUuid, centre, width, height};
}

/**
 * The map of one box: nodes you can pick, keys you can press, and lines that say which
 * thought came out of which.
 */
async function mindmapView(canvas, box, ctx, opts = {}) {
  const token = paneGeneration;
  let map;
  try {
    map = (await api.mindmap(box.uuid)).map;
  } catch (error) {
    canvas.append(emptyMessage(`Cannot draw the map: ${error.message}`));
    return;
  }
  if (!stillOnScreen(token)) return; // the pane moved on while the map was being drawn

  if (!opts.embedded) canvas.append(mindmapToolbar(box, ctx));

  const view = document.createElement("div");
  view.className = "mindmap-view";
  view.tabIndex = 0; // the picture takes the keyboard: Insert, Enter, the arrows
  view.setAttribute("role", "application");
  view.setAttribute("aria-label", `Mindmap: ${box.name}. Click, then use Insert, Enter and the arrow keys.`);
  const board = document.createElement("div");
  board.className = "mindmap-board";
  view.append(board);
  canvas.append(view);

  let drawn = mindmapLayout(map);
  let selected = box.uuid;
  let renameInPlace = null;

  function label(text, className = "") {
    const span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    return span;
  }

  function select(uuid) {
    selected = uuid;
    board.querySelectorAll(".mindmap-node").forEach((node) => {
      node.classList.toggle("selected", node.dataset.uuid === uuid);
      node.setAttribute("aria-selected", node.dataset.uuid === uuid ? "true" : "false");
    });
  }

  /** One line of the map: a cubic curve, the colour of the branch it belongs to. */
  function linkPath(row) {
    const parent = row.parent;
    if (!parent) return null;
    const path = document.createElementNS(SVG_NS, "path");
    const x1 = parent.x + row.sign * (parent.w / 2);
    const y1 = parent.cy;
    const x2 = row.x - row.sign * (row.w / 2);
    const y2 = row.cy;
    const bend = (x2 - x1) / 2;
    path.setAttribute("d", `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
    path.setAttribute("stroke", row.colour);
    return path;
  }

  function nodeTile(row) {
    const isCentre = row === drawn.centre;
    const tile = document.createElement("div");
    tile.className = `mindmap-node${isCentre ? " mindmap-centre" : ""}`;
    tile.dataset.uuid = row.node.uuid;
    tile.setAttribute("role", "treeitem");
    tile.setAttribute("aria-selected", row.node.uuid === selected ? "true" : "false");
    tile.style.setProperty("--node", row.colour);
    tile.style.left = `${row.x - row.w / 2}px`;
    tile.style.top = `${row.cy - row.h / 2}px`;
    tile.style.width = `${row.w}px`;
    tile.style.height = `${row.h}px`;
    tile.append(iconElement(row.node), label(row.node.name, "mindmap-label"));
    tile.title = isCentre
      ? `${row.node.name} — the middle of the map`
      : `${row.node.name} (${row.node.box_type})`;
    if (row.node.truncated) {
      tile.append(label(" ↻", "mindmap-cycle"));
      tile.title += " — this box is already drawn above; a box inside itself is drawn once";
    }

    tile.addEventListener("click", (event) => {
      event.stopPropagation();
      view.focus();
      select(row.node.uuid);
    });
    tile.addEventListener("dblclick", (event) => {
      event.stopPropagation();
      rename(row);
    });
    tile.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      event.stopPropagation();
      select(row.node.uuid);
      ctx.showMenu(event.clientX, event.clientY, nodeMenu(row));
    });

    // A node can be dragged onto another node: the drop either fills an empty
    // placeholder or files the dragged box inside, which is what the server decides.
    tile.draggable = true;
    tile.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", row.node.uuid);
      event.dataTransfer.effectAllowed = "move";
    });
    tile.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      tile.classList.add("drop-target");
    });
    tile.addEventListener("dragleave", () => tile.classList.remove("drop-target"));
    tile.addEventListener("drop", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      tile.classList.remove("drop-target");
      const dragged = event.dataTransfer.getData("text/plain");
      if (!dragged || dragged === row.node.uuid) return;
      await fillNode(row, dragged);
    });
    return tile;
  }

  function paint(nextMap) {
    map = nextMap;
    drawn = mindmapLayout(map);
    board.style.width = `${drawn.width}px`;
    board.style.height = `${drawn.height}px`;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "mindmap-links");
    svg.setAttribute("width", String(drawn.width));
    svg.setAttribute("height", String(drawn.height));
    svg.setAttribute("viewBox", `0 0 ${drawn.width} ${drawn.height}`);
    drawn.rows.forEach((row) => {
      const path = linkPath(row);
      if (path) svg.append(path);
    });
    const nodes = drawn.rows.map(nodeTile);
    board.replaceChildren(svg, ...nodes);
    if (!drawn.rows.some((row) => row !== drawn.centre)) {
      const hint = document.createElement("p");
      hint.className = "mindmap-hint muted";
      hint.textContent =
        "Click here, then press Insert for a branch — or drag a box from the panel onto the middle.";
      board.append(hint);
    }
    select(selected && drawn.byUuid.has(selected) ? selected : map.uuid);
  }

  /** Take a mutation's answer, and redraw from what the database says the map is now. */
  async function applied(promise, note) {
    try {
      const answer = await promise;
      if (answer && answer.map) paint(answer.map);
      if (note) ctx.status(typeof note === "function" ? note(answer) : note);
      await refreshSide();
    } catch (error) {
      ctx.status(error.message, true);
    }
  }

  async function addNode(parent, after) {
    view.focus();
    const before = new Set(drawn.rows.map((row) => row.node.uuid));
    try {
      const answer = await api.mindmapNode(map.uuid, parent ? parent.node.uuid : null, after);
      paint(answer.map);
      await refreshSide();
      const fresh = drawn.rows.find((row) => !before.has(row.node.uuid));
      if (fresh) {
        select(fresh.node.uuid);
        rename(fresh); // a node is named where it is made
      }
    } catch (error) {
      ctx.status(error.message, true);
    }
  }

  async function fillNode(row, draggedUuid) {
    await applied(
      api.mindmapFill(map.uuid, row.node.uuid, draggedUuid),
      (answer) =>
        answer.replaced
          ? `${answer.removed} was an empty node: the box you dropped took its place`
          : "filed in that node",
    );
  }

  /**
   * Write a node's name where it stands. A node made by Insert is unnamed, and typing
   * the thought the moment the node appears is the whole reason the key exists.
   */
  function rename(row) {
    if (renameInPlace) renameInPlace.cancel();
    const tile = board.querySelector(`.mindmap-node[data-uuid="${row.node.uuid}"]`);
    if (!tile) return;
    const text = tile.querySelector(".mindmap-label");
    const was = row.node.name;
    text.contentEditable = "plaintext-only";
    text.focus();
    const range = document.createRange();
    range.selectNodeContents(text);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);

    const finish = async (keep) => {
      if (done) return;
      done = true;
      text.contentEditable = "false";
      // A field that stops being a field takes the keyboard with it, and the map is
      // nothing without the keyboard: hand it back before anything else.
      view.focus();
      const now = text.textContent.trim();
      if (!keep || !now || now === was) {
        text.textContent = was;
        renameInPlace = null;
        return;
      }
      try {
        await api.updateBox(row.node.uuid, {name: now});
        row.node.name = now;
        text.textContent = now;
        await refreshSide();
        ctx.status(`named ${now}`);
      } catch (error) {
        text.textContent = was;
        ctx.status(error.message, true);
      }
      renameInPlace = null;
    };
    let done = false;
    const onKey = (event) => {
      event.stopPropagation(); // a key typed into a name is not a command to the map
      if (event.key === "Enter") {
        event.preventDefault();
        finish(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      }
    };
    text.addEventListener("keydown", onKey);
    text.addEventListener("blur", () => finish(true), {once: true});
    renameInPlace = {cancel: () => finish(false)};
  }

  /** Which node the arrow keys go to: outward, inward, or along the branch. */
  function navigate(row, key) {
    if (!row) {
      select(map.uuid);
      return;
    }
    const sideways = key === "ArrowLeft" ? -1 : key === "ArrowRight" ? 1 : 0;
    const updown = key === "ArrowUp" ? -1 : key === "ArrowDown" ? 1 : 0;

    if (row === drawn.centre) {
      if (sideways) {
        const first = drawn.rows.filter((other) => other.parent === drawn.centre && other.sign === sideways)[0];
        if (first) select(first.node.uuid);
        return;
      }
      if (updown) {
        const first = drawn.rows.filter((other) => other.parent === drawn.centre)[0];
        if (first) select(first.node.uuid);
      }
      return;
    }

    if (sideways === row.sign) {
      // away from the centre: down into the branch
      if (row.children.length) select(row.children[0].node.uuid);
      return;
    }
    if (sideways) {
      if (row.parent) select(row.parent.node.uuid);
      return;
    }

    // Siblings are the nodes hanging from the same parent *on the same side*: the
    // centre's children live in two columns, and stepping down out of one of them is
    // not a step into the other half of the picture.
    const among = (row.parent ? row.parent.children : drawn.rows).filter(
      (other) => other.parent === row.parent && other.sign === row.sign,
    );
    const at = among.indexOf(row);
    const next = among[at + updown];
    if (next) {
      select(next.node.uuid);
      return;
    }
    // Nothing left in the branch: the nearest node above or below anywhere in the map.
    const candidates = drawn.rows
      .filter((other) => other !== row && (updown < 0 ? other.cy < row.cy : other.cy > row.cy))
      .sort((a, b) => Math.abs(a.cy - row.cy) - Math.abs(b.cy - row.cy));
    if (candidates.length) select(candidates[0].node.uuid);
  }

  function nodeMenu(row) {
    const isCentre = row === drawn.centre;
    const extra = [
      { separator: true },
      { label: "＋ New node here", run: () => addNode(row, null) },
      !isCentre && row.parent
        ? { label: "＋ New node beside it", run: () => addNode(row.parent, row.node.uuid) }
        : null,
      !isCentre ? { label: "Rename…", run: () => rename(row) } : null,
      !isCentre && row.parent === drawn.centre
        ? {
            label: row.sign === 1 ? "Send this branch to the left" : "Send this branch to the right",
            run: () => applied(api.mindmapSide(map.uuid, row.node.uuid, {flip: true})),
          }
        : null,
      !isCentre && row.parent && row.parent !== drawn.centre
        ? {
            label: "Move it up the hierarchy",
            run: () => applied(api.mindmapHierarchy(map.uuid, row.node.uuid, "in"), (answer) => answer.note),
          }
        : null,
      !isCentre
        ? {
            label: "Make it a detail of the node above",
            run: () => applied(api.mindmapHierarchy(map.uuid, row.node.uuid, "out"), (answer) => answer.note),
          }
        : null,
      !isCentre && row.parent
        ? { label: "Take out of this map", run: () => ctx.removeFrom(row.parent.node, row.node) }
        : null,
    ];
    return boxMenuItems(row.node, extra);
  }

  view.addEventListener("keydown", (event) => {
    const target = event.target;
    if (target && target.isContentEditable) return; // typing a name, not commanding the map
    const row = drawn.byUuid.get(selected);
    const key = event.key;

    if (key === "Insert") {
      event.preventDefault();
      addNode(row || drawn.centre, null);
      return;
    }
    if (key === "Enter") {
      event.preventDefault();
      if (!row || row === drawn.centre) addNode(drawn.centre, null);
      else addNode(row.parent, row.node.uuid);
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (key === "ArrowUp" || key === "ArrowDown")) {
      event.preventDefault();
      if (row && row !== drawn.centre) {
        applied(api.mindmapOrder(map.uuid, row.node.uuid, key === "ArrowUp" ? -1 : 1));
      }
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (key === "ArrowLeft" || key === "ArrowRight")) {
      event.preventDefault();
      if (!row || row === drawn.centre) return;
      // Ctrl always means the same thing physically: away from the centre is deeper,
      // towards it is shallower. Which arrow that is depends on the side.
      const away = (key === "ArrowRight" && row.sign === 1) || (key === "ArrowLeft" && row.sign === -1);
      applied(
        api.mindmapHierarchy(map.uuid, row.node.uuid, away ? "out" : "in"),
        (answer) => answer.note,
      );
      return;
    }
    if (key === "F2") {
      event.preventDefault();
      if (row) rename(row);
      return;
    }
    if (key.startsWith("Arrow")) {
      event.preventDefault();
      navigate(row, key);
    }
  });

  view.addEventListener("mousedown", () => view.focus());
  view.addEventListener("contextmenu", (event) => {
    if (event.target.closest(".mindmap-node")) return; // the node answers for itself
    event.preventDefault();
    ctx.showMenu(
      event.clientX,
      event.clientY,
      boxMenuItems(box, [
        { separator: true },
        { label: "＋ New node at the centre", run: () => addNode(drawn.centre, null) },
      ]),
    );
  });

  // A box dropped on the bare picture is filed at the centre of the map: it becomes a
  // branch, which is where a new thought about the subject belongs.
  board.addEventListener("dragover", (event) => {
    event.preventDefault();
    board.classList.add("drop-target");
  });
  board.addEventListener("dragleave", () => board.classList.remove("drop-target"));
  board.addEventListener("drop", async (event) => {
    if (event.target.closest(".mindmap-node")) return;
    event.preventDefault();
    board.classList.remove("drop-target");
    const dragged = event.dataTransfer.getData("text/plain");
    if (!dragged || dragged === map.uuid) return;
    await applied(api.link(map.uuid, dragged), "filed as a branch of the centre");
  });

  paint(map);

  /*
   * In a cell the whole map is drawn smaller, not a corner of it at full size: what
   * someone looks at in a small square is the shape of the thought. The board keeps
   * its own coordinates and one transform shrinks it, so a node clicked in a scaled
   * map still answers for the right box — the pointer never has to be converted,
   * because nothing here is placed by the pointer.
   */
  if (opts.embedded) {
    view.classList.add("mindmap-fitted");
    const fit = () => {
      const availW = view.clientWidth;
      const availH = view.clientHeight;
      if (!availW || !availH) return;
      const scale = Math.min(1, availW / drawn.width, availH / drawn.height);
      board.style.transformOrigin = "top left";
      board.style.transform = scale === 1 ? "" : `scale(${scale})`;
      view.scrollLeft = 0;
      view.scrollTop = 0;
    };
    fit();
    const watcher = new ResizeObserver(fit);
    watcher.observe(view);
    holdLive(() => watcher.disconnect());
  } else {
    // Open with the middle of the picture in the middle of the pane.
    view.scrollLeft = Math.max(0, drawn.centre.x - view.clientWidth / 2);
    view.scrollTop = Math.max(0, drawn.centre.cy - view.clientHeight / 2);
  }
}

/** What the map is, how many nodes it holds, and the keys that drive it. */
function mindmapToolbar(box, ctx) {
  const bar = document.createElement("div");
  bar.className = "mindmap-toolbar";
  const title = document.createElement("strong");
  title.textContent = `${box.icon} ${box.name}`;
  const keys = document.createElement("span");
  keys.className = "muted mindmap-keys";
  keys.textContent =
    "click the map, then: Insert — node · Enter — beside it · arrows — walk · Ctrl+↑↓ — reorder · Ctrl+←→ — deeper or shallower · double-click — rename";
  const add = document.createElement("button");
  add.type = "button";
  add.textContent = "＋ New node";
  add.addEventListener("click", () => {
    const view = document.querySelector(".mindmap-view");
    if (view) view.focus();
    ctx.newBox({parent: box.uuid});
  });
  bar.append(title, keys, add);
  return bar;
}

/* -- ssh ---------------------------------------------------------------------
 *
 * A box that reaches another machine. This is a console, not a terminal emulator: you
 * type a command, the machine answers, and the exchange stays on the screen in order.
 * The distinction is stated on the panel rather than hidden, because the first
 * full-screen program anyone tries (an editor, `top`) will not behave like a program
 * that answers once and exits — and a page that promised a terminal and delivered a
 * scrollback is worse than one that says what it is.
 *
 * There is no password field anywhere, and no box can carry one: the type refuses it.
 * Authentication is what `ssh` already does with keys, an agent, or ~/.ssh/config.
 */

const SSH_MAX_HISTORY = 20;

async function sshView(canvas, box, ctx, opts = {}) {
  const token = paneGeneration;
  let info;
  try {
    info = await api.ssh(box.uuid);
  } catch (error) {
    canvas.append(emptyMessage(`Cannot read the SSH box: ${error.message}`));
    return;
  }
  if (!stillOnScreen(token)) return; // the pane moved on while the address was read

  const where = info.destination;
  const wrap = document.createElement("div");
  wrap.className = "ssh-view";

  const head = document.createElement("div");
  head.className = "ssh-head";
  const address = document.createElement("strong");
  address.textContent = `ssh://${where.user ? `${where.user}@` : ""}${where.host}:${where.port}`;
  head.append(address);
  if (where.jump_host) {
    const jump = document.createElement("span");
    jump.className = "muted";
    jump.textContent = `through ${where.jump_host}`;
    head.append(jump);
  }
  if (where.identity_file) {
    const key = document.createElement("span");
    key.className = "muted";
    key.textContent = `key ${where.identity_file}`;
    head.append(key);
  }
  wrap.append(head);

  if (!info.client_available) {
    const missing = document.createElement("p");
    missing.className = "ssh-warning";
    missing.textContent =
      "This machine has no ssh command, so nothing can be run. Install OpenSSH.";
    wrap.append(missing);
  }

  const note = document.createElement("p");
  note.className = "muted ssh-note";
  note.textContent =
    "One command at a time, run by this machine's own ssh: keys, agents and ~/.ssh/config decide who you are. No password is stored, and none is asked for.";
  wrap.append(note);

  const log = document.createElement("div");
  log.className = "ssh-log";
  log.setAttribute("role", "log");
  log.setAttribute("aria-live", "polite");
  wrap.append(log);

  const form = document.createElement("form");
  form.className = "ssh-form";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "ssh-input";
  input.placeholder = "uptime";
  input.spellcheck = false;
  input.autocomplete = "off";
  input.setAttribute("aria-label", "command to run on the far side");
  const go = document.createElement("button");
  go.type = "submit";
  go.textContent = "Run";
  form.append(input, go);
  wrap.append(form);

  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "ssh-copy muted";
  copy.textContent = "For a session of your own, copy this:";
  const line = document.createElement("code");
  line.className = "ssh-line";
  line.textContent = where.command_line;
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(where.command_line);
      ctx.status("copied the command line");
    } catch {
      // No clipboard permission: the line is on the screen, and selecting it works.
      const range = document.createRange();
      range.selectNodeContents(line);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      ctx.status("selected instead — copy it from here");
    }
  });
  wrap.append(copy, line);

  const history = sshHistory(box);
  let cursor = history.length;

  function entry(record) {
    const block = document.createElement("div");
    block.className = `ssh-entry${record.ok ? "" : " ssh-entry--bad"}`;
    const asked = document.createElement("div");
    asked.className = "ssh-asked";
    asked.textContent = `$ ${record.command}`;
    block.append(asked);
    for (const [text, kind] of [
      [record.stdout, "ssh-out"],
      [record.stderr, "ssh-err"],
    ]) {
      if (!text) continue;
      const part = document.createElement("pre");
      part.className = kind;
      part.textContent = text.replace(/\n$/, "");
      block.append(part);
    }
    const tally = document.createElement("div");
    tally.className = "muted ssh-tally";
    tally.textContent = record.timed_out
      ? `no answer in ${record.seconds.toFixed(1)}s`
      : `exit ${record.exit_code} in ${record.seconds.toFixed(2)}s`;
    block.append(tally);
    log.append(block);
    log.scrollTop = log.scrollHeight;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const command = input.value.trim();
    if (!command) return;
    input.value = "";
    go.disabled = true;
    const pending = document.createElement("div");
    pending.className = "muted ssh-running";
    pending.textContent = `running ${command} …`;
    log.append(pending);
    log.scrollTop = log.scrollHeight;
    try {
      const result = await api.sshRun(box.uuid, command);
      pending.remove();
      entry(result);
      // The list the arrows walk is this page's own, and it is written to the box for
      // tomorrow: an up-arrow that only works after a reload is no help at all.
      if (!history.includes(command)) {
        history.push(command);
      }
      cursor = history.length;
      rememberSsh(box, ctx, command);
    } catch (error) {
      pending.remove();
      const failed = document.createElement("div");
      failed.className = "ssh-err ssh-entry";
      failed.textContent = error.message;
      log.append(failed);
    } finally {
      go.disabled = false;
      input.focus();
    }
  });

  // The arrow keys walk what was run here, because a command repeated from a terminal
  // is muscle memory; the far side has no say in this list.
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      if (!history.length) return;
      event.preventDefault();
      cursor = event.key === "ArrowUp"
        ? Math.max(0, cursor - 1)
        : Math.min(history.length, cursor + 1);
      input.value = history[cursor] || "";
    }
  });

  canvas.append(wrap);
  if (!opts.embedded) input.focus();
}

function sshHistory(box) {
  const section = (box.config || {}).ssh;
  const stored = section && Array.isArray(section.history) ? section.history : [];
  // A copy: the page appends to what it is walking, and must not be editing the box's
  // own config behind the code that saves it.
  return stored.filter((item) => typeof item === "string" && item.trim());
}

function pushHistory(history, command) {
  const kept = history.filter((item) => item !== command);
  return [...kept, command].slice(-SSH_MAX_HISTORY);
}

/** Save the console's history without disturbing anything else in the box's config. */
function rememberSsh(box, ctx, command) {
  const config = {...(box.config || {})};
  const section = {...(config.ssh || {})};
  section.history = pushHistory(sshHistory(box), command);
  config.ssh = section;
  api
    .setConfig(box.uuid, config)
    .then(() => {
      box.config = config;
    })
    .catch(() => {
      /* the command still ran; the history is a convenience, not a result */
    });
}

registerView("ssh", {
  async render(canvas, box, ctx, mode, opts = {}) {
    await sshView(canvas, box, ctx, opts);
  },
});

registerView("mindmap", {
  async render(canvas, box, ctx, mode, opts = {}) {
    await mindmapView(canvas, box, ctx, opts);
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
  checkResourceType,
  openEditBoxModal,
  registerView,
  registerConfigForm,
  addStyle,
  openBox,
  goUp,
  status,
  goToRoot,
  iconElement,
  iconPane,
  detailPanel,
  setting,
  saveSection,
  textOptions,
  folderOptions,
  desktopOptions,
  magnetise,
  viewFor,
  mqttView,
  holdLive,
  releaseLive,
  stillOnScreen,
};
window.boxganizer = boxganizer;

init();
