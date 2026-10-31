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
  editing: null, // uuid of the box the edit modal is open on
  editingBox: null, // that box as the view held it, for filling its metadata fields
  tagTree: [], // /api/tags as last read, shared by the chip field and the manage window
  sensorPalette: null, // /api/sensors/palette, read once: the picker draws what the server accepts
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
    if (response.status === 404 && reason === "Not Found") {
      // No route matched. The page came off this disk a moment ago; the server was started
      // earlier and does not know the endpoint yet. Saying "Not Found" here sends the reader
      // looking for a missing box, when what is missing is a restart.
      reason = `this server has no ${path.split("?")[0]} endpoint — restart \`box serve\``;
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
  link: (parent, child, moveFrom = null) =>
    request(`/api/boxes/${encodeURIComponent(parent)}/children`, {
      method: "POST",
      body: JSON.stringify({ child, move_from: moveFrom }),
    }),
  rawUrl: (ref) => `/api/boxes/${encodeURIComponent(ref)}/raw`,
  thumbnailUrl: (ref) => `/api/boxes/${encodeURIComponent(ref)}/thumbnail`,
  root: () => request("/api/root"),
  filter: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/filter`),
  sensorPalette: () => request("/api/sensors/palette"),
  viewports: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/viewports`),
  viewportAction: (ref, action, payload = {}) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/viewports/${action}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  parseFilter: (expression) =>
    request("/api/filters/parse", {
      method: "POST",
      body: JSON.stringify({ expression }),
    }),
  setFilter: (ref, value) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/filter`, {
      method: "PUT",
      body: JSON.stringify({ filter: value }),
    }),
  resolveFolder: (ref) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/resolve`, { method: "POST" }),
  indexStatus: (check) => request(`/api/index/status${check ? "?check=1" : ""}`),
  suggestType: (resourceUri) =>
    request(`/api/types/suggest?resource_uri=${encodeURIComponent(resourceUri)}`),
  tags: () => request("/api/tags"),
  createTag: (payload) =>
    request("/api/tags", { method: "POST", body: JSON.stringify(payload) }),
  editTag: (name, payload) =>
    request(`/api/tags/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteTag: (name) =>
    request(`/api/tags/${encodeURIComponent(name)}`, { method: "DELETE" }),
  boxTags: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/tags`),
  giveTag: (ref, name) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/tags`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  takeTag: (ref, name) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/tags/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  setBoxTags: (ref, tags) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/tags`, {
      method: "PUT",
      body: JSON.stringify({ tags }),
    }),
  indexPending: () => request("/api/index/pending"),
  // "Look at the files again" without reading any of them: the status endpoint is the one
  // that stats, and `check=1` is what turns "to reindex" from old news into a fresh answer.
  indexStatFiles: () => request("/api/index/status?check=1"),
  indexRun: (scope) =>
    request("/api/index/run", { method: "POST", body: JSON.stringify({ scope }) }),
  health: () => request("/api/health"),
  check: () => request("/api/check"),
  checkFix: (payload) =>
    request("/api/check/fix", { method: "POST", body: JSON.stringify(payload) }),
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
  markdown: (ref, sub) =>
    request(
      `/api/boxes/${encodeURIComponent(ref)}/markdown${sub ? `?sub=${encodeURIComponent(sub)}` : ""}`
    ),
  note: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/note`),
  saveNote: (ref, source, format) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/note`, {
      method: "PUT",
      body: JSON.stringify({ source, format }),
    }),
  vnc: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/vnc`),
  vncOpen: (ref, password) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/vnc/open`, {
      method: "POST",
      body: JSON.stringify(password ? { password } : {}),
    }),
  // The session id is in the query and the password never is: a URL outlives the request in
  // the access log, the browser history and every proxy in between.
  vncStreamUrl: (ref, session) =>
    `/api/boxes/${encodeURIComponent(ref)}/vnc/stream?session=${encodeURIComponent(session)}`,
  vncInput: (ref, payload) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/vnc/input`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  vncClose: (ref, session) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/vnc/close`, {
      method: "POST",
      body: JSON.stringify({ session }),
    }),
  checklist: (ref) => request(`/api/boxes/${encodeURIComponent(ref)}/checklist`),
  saveChecklist: (ref, title, items) =>
    request(`/api/boxes/${encodeURIComponent(ref)}/checklist`, {
      method: "PUT",
      body: JSON.stringify({ title, items }),
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
  // A directory box shows its top directory when it is opened, every time. Remembering where
  // a browse left off sounds kind and is not: the box looks like it has children it does not
  // have, and the second opening is not the first thing the person was looking for. Re-renders
  // of the box already on screen keep their place, because those are not openings.
  dirNav.clear();
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


/**
 * Say what is being dragged, and where it came from.
 *
 * The origin travels with the drag rather than living in a page-wide "current drag" variable: the
 * drop may land in another pane, another viewport, or after a re-render, and a variable set at
 * dragstart would then be describing a drag that already happened.
 */
function dragBox(event, uuid, fromUuid) {
  event.dataTransfer.setData("text/plain", uuid);
  if (fromUuid) event.dataTransfer.setData("application/x-boxganizer-from", fromUuid);
  event.dataTransfer.effectAllowed = "copyMove";
}

/**
 * A box was dropped on something that can hold it.
 *
 * **A plain drop copies the link**: the box is filed where it landed and stays where it came from.
 * That is the gesture people mean when they drag a note into a project — "this belongs here too" —
 * and it is the one that can be undone by taking it back out. **Holding Shift moves it**: the
 * server takes the link off the origin in the same request that files it here, so there is no
 * half-way state where the box is nowhere. The server decides the difference; this only says which
 * was asked for, because two implementations of "move" would drift into different ideas of what
 * happened to a box, and that is a bug a person discovers by losing something.
 *
 * `after` is for views that place as well as file (a grid cell, a desktop, a map node): the link
 * runs first so a move is finished before the placement, which then finds the box already a child.
 */
/* A tag moved. Which folders that matters for is a question about their filters, not about the code
 * that moved the tag, so the tag UI shouts into the page and the folders that resolve their own
 * membership answer by re-reading themselves. */
const RELIST_EVENT = "boxganizer:relist-folders";

function relistFilteredFolders() {
  document.dispatchEvent(new CustomEvent(RELIST_EVENT));
}

async function dropBox(event, ctx, targetUuid, { after = null, label = null } = {}) {
  const dragged = event.dataTransfer ? event.dataTransfer.getData("text/plain") : "";
  if (!dragged || dragged === targetUuid) return false;
  const from = event.dataTransfer ? event.dataTransfer.getData("application/x-boxganizer-from") : "";
  const move = Boolean(event.shiftKey && from && from !== targetUuid);
  try {
    if (move) await api.link(targetUuid, dragged, from);
    if (after) await after(move);
    else if (!move) await api.link(targetUuid, dragged);
    // The two sentences say what changed, because the two gestures look identical and people need
    // to be able to tell, after the fact, whether the box is still where it was.
    ctx.status(move ? "moved here; it is no longer filed where you dragged it from" : label || "filed here");
    return true;
  } catch (error) {
    ctx.status(`cannot drop it here: ${error.message}`, true);
    return false;
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


/* -- view ports: the boxes that keep their space ----------------------------
 *
 * A viewport box is never replaced by what you open in it: it lends its space out, one box per
 * pane, each pane with a path line of its own. Everything below paints the layout the server
 * computed (`/api/boxes/…/viewports`) and sends back *intents* — split, close, move the bar, show
 * this box here. The page never edits a tree and posts it: two pages open on one box would each be
 * deciding what the other's click meant, and the tree model would exist twice, in two languages,
 * with the same name.
 */
/* A pane folds its own settings row, and no one else's.

   On the main screen the gear in the header decides for the one box on screen, and the canvas carries
   that decision. A viewport has several boxes at once: one gear for all of them would mean unfolding
   a grid's columns when you came to change the checklist beside it, and — worse — a second person's
   viewport would move under the first's click. So the state lives on the pane, and the rows carry the
   mark their own pane made: `pane-folded`. The mark is set from the pane after every render rather
   than by a selector, because "the pane this row belongs to" means the *innermost* one, and a
   viewport nested inside a viewport has two answers to give.
*/
function foldPaneRows(pane) {
  const open = pane.classList.contains("config-open");
  pane.querySelectorAll(".box-config").forEach((row) => {
    if (row.closest(".vp-pane") === pane) row.classList.toggle("pane-folded", !open);
  });
}

const viewportView = {
  async render(canvas, box, ctx, mode, opts = {}) {
    const token = paneGeneration;
    const wrap = document.createElement("div");
    wrap.className = "viewport-view";
    wrap.dataset.uuid = box.uuid;
    canvas.append(wrap);

    let layout = null;
    try {
      layout = await api.viewports(box.uuid);
    } catch (error) {
      wrap.append(emptyMessage(`this viewport could not be read: ${error.message}`));
      return;
    }
    if (!stillOnScreen(token)) return;

    const refresh = async () => {
      try {
        layout = await api.viewports(box.uuid);
      } catch (error) {
        ctx.status(`could not re-read the viewports: ${error.message}`, true);
        return;
      }
      if (stillOnScreen(token)) paint();
    };

    // The action routes answer with the layout they produced, so the page adopts that answer
    // instead of asking again: a re-read could come back behind another tab's write and paint a
    // layout that is neither the old one nor the one this click asked for.
    const apply = (next) => {
      if (next && next.kind) layout = next;
    };

    const act = async (action, payload, { repaint = true } = {}) => {
      try {
        apply(await api.viewportAction(box.uuid, action, payload));
        if (repaint) paint();
      } catch (error) {
        // The server's own sentence, because it is the one that knows why: "the last viewport
        // cannot be closed; split it first" is advice, "422" is not.
        ctx.status(error.message, true);
      }
    };

    function paneShell(node, shown) {
      const pane = document.createElement("div");
      pane.className = "vp-pane";
      pane.dataset.node = node;

      const path = document.createElement("div");
      path.className = "vp-path";
      const crumbs = document.createElement("div");
      crumbs.className = "vp-crumbs";
      const buttons = document.createElement("div");
      buttons.className = "vp-buttons";
      path.append(crumbs, buttons);

      const body = document.createElement("div");
      body.className = "vp-body";
      pane.append(path, body);

      /* Each pane walks on its own path. One shared trail would mean that opening a box in the
         left pane changed what "back" means in the right one — and the whole point of the box is
         that two things are open at once. */
      const trail = shown ? [shown] : [];
      const self = {
        node,
        el: pane,
        crumbs,
        body,
        trail,
        async open(ref, options = {}) {
          let target = null;
          try {
            target = await api.box(ref);
          } catch (error) {
            ctx.status(error.message, true);
            return;
          }
          trail.length = 0;
          trail.push(...(options.trail || []), target);
          await self.draw(target);
        },
        async draw(target) {
          body.replaceChildren();
          await renderContentIn(body, target, paneContext(target), { depth: 0, embedded: false });
          drawCrumbs();
          foldPaneRows(pane);
          // A control with nothing to control is decoration: the same rule that hides the header's
          // gear when a box has no settings row applies to a pane's.
          gear.hidden = !body.querySelector(".box-config");
        },
        async reload() {
          if (trail.length) await self.open(trail[trail.length - 1].uuid, { trail: trail.slice(0, -1) });
        },
      };

      function paneContext(target) {
        return {
          ...context,
          open: (ref, options = {}) => self.open(ref, options),
          trail: () => trail,
          reload: () => self.reload(),
          status: ctx.status,
          // A setting written from inside a pane is still a write to that box, so it goes to the
          // shared helper — but the re-render must not take the whole viewport with it.
          saveSetting: (key, value, options = {}) =>
            saveSettingFor(target, key, value, { onSaved: () => self.reload() }, options),
        };
      }

      function drawCrumbs() {
        crumbs.replaceChildren();
        if (!trail.length) {
          const empty = document.createElement("span");
          empty.className = "vp-empty muted";
          empty.textContent = "empty — open a box here, or drop one in";
          crumbs.append(empty);
          return;
        }
        trail.forEach((entry, index) => {
          if (index) crumbs.append(document.createTextNode("›"));
          const crumb = document.createElement("button");
          crumb.type = "button";
          crumb.dataset.action = "vp-crumb";
          crumb.textContent = `${entry.icon} ${entry.name}`;
          crumb.disabled = index === trail.length - 1;
          crumb.addEventListener("click", () =>
            self.open(entry.uuid, { trail: trail.slice(0, index) })
          );
          crumbs.append(crumb);
        });
      }

      const button = (label, title, action, extra = {}) => {
        const node2 = document.createElement("button");
        node2.type = "button";
        node2.className = "vp-btn";
        node2.dataset.action = action;
        node2.textContent = label;
        node2.title = title;
        node2.setAttribute("aria-label", `${title} (viewport ${node})`);
        Object.assign(node2, extra);
        return node2;
      };

      // Drawn in CSS (a rectangle with a line where the new bar will be) rather than a geometric
      // character: ◫ and ◨ do not read as "side by side" and "above and below" in most fonts, and
      // the click handler was telling them apart by their textContent, which is a glyph-deep way
      // to pick a direction. The words say what the panes will do, not which axis is named.
      const split = (direction, title) => {
        const entry = button("", title, "vp-split");
        entry.dataset.direction = direction;
        entry.className = `vp-btn vp-icon vp-icon-${direction}`;
        return entry;
      };
      const gear = document.createElement("button");
      gear.type = "button";
      gear.className = "vp-btn gear";
      gear.dataset.action = "vp-config";
      gear.dataset.node = node;
      gear.innerHTML = "&#9881;";
      gear.title = "Show this viewport's settings";
      gear.setAttribute("aria-expanded", "false");
      gear.setAttribute("aria-label", `settings for viewport ${node}`);
      gear.addEventListener("click", () => {
        const open = pane.classList.toggle("config-open");
        gear.setAttribute("aria-expanded", String(open));
        gear.title = open ? "Hide this viewport's settings" : "Show this viewport's settings";
        foldPaneRows(pane);
      });
      buttons.append(
        gear,
        split("horizontal", "Split this viewport side by side"),
        split("vertical", "Split this viewport above and below"),
        button("×", "Close this viewport", "vp-close"),
      );
      buttons.querySelectorAll("button").forEach((entry) => {
        entry.dataset.node = node;
      });
      const close = buttons.querySelector('[data-action="vp-close"]');
      // Disabled, not hidden and not explained afterwards: the last viewport is the box's last way
      // of showing anything, and a click that silently does nothing teaches people to click twice.
      close.disabled = !layout.closable;
      if (!layout.closable) close.title = "the last viewport cannot be closed; split one first";

      drawCrumbs();
      // Views repaint their controls in place — a grid that changes its column count, a folder that
      // switches layout — and a row drawn after the pane's last render would arrive unfolded.
      new MutationObserver(() => foldPaneRows(pane)).observe(body, { childList: true, subtree: true });
      if (shown) {
        // The pane's box is drawn from the brief the layout already carried, then filled in.
        self.draw(shown);
      } else {
        body.append(emptyMessage("Nothing open here."));
      }

      pane.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = event.shiftKey ? "move" : "copy";
        pane.classList.add("drop-target");
      });
      pane.addEventListener("dragleave", () => pane.classList.remove("drop-target"));
      pane.addEventListener("drop", async (event) => {
        pane.classList.remove("drop-target");
        // A drop the box *inside* this pane already claimed — a folder that filed it, a grid cell
        // that placed it, a checklist line that took it — is not the pane's to answer too. Without
        // this guard the same drop filed the box in the folder it landed on *and* in the viewport
        // box, so one gesture produced two parents and the person watching saw a box appear where
        // they had not put it. Same convention as the root view's holder and the canvas pane.
        if (event.defaultPrevented) return;
        const dragged = event.dataTransfer ? event.dataTransfer.getData("text/plain") : "";
        if (!dragged) return;
        event.preventDefault();
        event.stopPropagation();
        const from = event.dataTransfer.getData("application/x-boxganizer-from");
        const move = Boolean(event.shiftKey && from && from !== dragged);
        try {
          if (move) await api.link(box.uuid, dragged, from);
          apply(await api.viewportAction(box.uuid, "open", { node, child: dragged }));
          paint();
          ctx.status(
            move
              ? "moved into this viewport"
              : "opened here; the box is still filed where you dragged it from"
          );
        } catch (error) {
          ctx.status(`cannot open it here: ${error.message}`, true);
        }
      });

      return pane;
    }

    function renderNode(entry) {
      if (entry.node && entry.child !== undefined && !entry.direction) {
        // The tree says *which node is where*; the briefs that came with the same answer say what
        // each node shows. A leaf on its own has no box — asking it for one is how a pane renders
        // empty while the layout says it is holding something.
        return paneShell(entry.node, briefs.get(entry.node) || null);
      }
      const band = document.createElement("div");
      band.className = `vp-band vp-${entry.direction}`;
      band.dataset.node = entry.node;
      band.dataset.ratio = String(entry.ratio);
      const first = renderNode(entry.first);
      const second = renderNode(entry.second);
      const divider = document.createElement("div");
      divider.className = "vp-divider";
      divider.dataset.node = entry.node;
      divider.dataset.action = "vp-divider";
      divider.setAttribute("role", "separator");
      divider.setAttribute("aria-orientation", entry.direction === "vertical" ? "horizontal" : "vertical");
      divider.setAttribute("aria-label", "drag to change the share of space between these viewports");
      divider.setAttribute("tabindex", "0");
      wireDivider(band, divider, entry);
      band.append(first, divider, second);
      // After the children are in: the ratio is applied to the panes of this band, and applied
      // before them it found nothing to size — so a divider dragged to 0.48 and saved came back
      // at the CSS default on the next load, which is the one thing a saved layout must not do.
      applyRatio(band, entry.ratio);
      return band;
    }

    function applyRatio(band, ratio) {
      const share = Math.min(Math.max(ratio, 0.05), 0.95);
      const kids = band.querySelectorAll(":scope > *");
      kids.forEach((kid) => {
        if (kid.classList.contains("vp-divider")) kid.style.flex = "0 0 7px";
        else kid.style.flexGrow = kid === kids[0] ? String(share) : String(1 - share);
      });
      band.dataset.ratio = String(share);
    }

    /**
     * Drag the bar. The panes follow the pointer at once; the server is told once, at the end.
     *
     * The listeners live on `document` for the duration of the drag rather than on the bar: the
     * bar moves out from under the pointer as the ratio changes, and a capture set on an element
     * that is replaced mid-gesture ends the drag early — which is how a first version saved the
     * ratio the pointer had *started* on, and left a person with a divider that snapped back.
     */
    function wireDivider(band, divider, entry) {
      let share = null;

      const move = (event) => {
        if (share === null) return;
        const rect = band.getBoundingClientRect();
        const raw =
          entry.direction === "vertical"
            ? (event.clientY - rect.top) / rect.height
            : (event.clientX - rect.left) / rect.width;
        share = Math.min(Math.max(raw, 0.05), 0.95);
        applyRatio(band, share);
      };

      const end = async () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", end);
        document.removeEventListener("pointercancel", end);
        divider.classList.remove("dragging");
        if (share === null) return;
        const value = share;
        share = null;
        try {
          apply(
            await api.viewportAction(box.uuid, "ratio", {
              node: entry.node,
              ratio: Number(value.toFixed(4)),
            }),
          );
        } catch (error) {
          ctx.status(`the divider did not stay: ${error.message}`, true);
        }
        // No repaint: the panes are already where the person put them, and rebuilding them would
        // throw away the scroll position of every box open in them.
      };

      // A mouse-drag that crosses something draggable hands the gesture to the browser's own drag,
      // which fires `pointercancel` and ends this one — which is how a first version saved the
      // ratio the pointer had started on instead of the one it reached.
      divider.addEventListener("dragstart", (event) => event.preventDefault());
      divider.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        share = Number(band.dataset.ratio || 0.5);
        divider.classList.add("dragging");
        document.addEventListener("pointermove", move);
        document.addEventListener("pointerup", end);
        document.addEventListener("pointercancel", end);
      });

      divider.addEventListener("keydown", (event) => {
        const step = event.shiftKey ? 0.1 : 0.02;
        const keys = ["ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        const current = Number(band.dataset.ratio || 0.5);
        const next = keys.indexOf(event.key) < 2 ? current - step : current + step;
        share = Math.min(Math.max(next, 0.05), 0.95);
        applyRatio(band, share);
        const value = share;
        share = null;
        api
          .viewportAction(box.uuid, "ratio", { node: entry.node, ratio: Number(value.toFixed(4)) })
          .then(apply)
          .catch((error) => ctx.status(`the divider did not stay: ${error.message}`, true));
      });
    }

    function paintTiling() {
      briefs = new Map((layout.panes || []).map((pane) => [pane.node, pane.box || null]));
      const root = renderNode(layout.tree);
      root.classList.add("vp-root");
      wrap.append(root);
    }

    function paintWindows() {
      const stage = document.createElement("div");
      stage.className = "vp-stage";
      briefs = new Map((layout.panes || []).map((pane) => [pane.node, pane.box || null]));
      const order = [...(layout.order || [])];
      order.forEach((nodeId, index) => {
        const entry = (layout.panes || []).find((pane) => pane.node === nodeId);
        if (!entry) return;
        stage.append(windowFrame(entry, index, order.length));
      });
      wrap.append(stage);
    }

    function windowFrame(entry, index, total) {
      const frame = document.createElement("div");
      frame.className = "vp-window";
      frame.dataset.node = entry.node;
      frame.dataset.action = "vp-focus";
      frame.style.left = `${entry.x}px`;
      frame.style.top = `${entry.y}px`;
      frame.style.width = `${entry.width}px`;
      frame.style.height = `${entry.height}px`;
      frame.style.zIndex = String(10 + index);
      if (index === total - 1) frame.dataset.focused = "true";

      const pane = paneShell(entry.node, entry.box);
      pane.classList.add("vp-in-window");
      frame.append(pane);

      // Clicking anywhere in a window raises it. Raising changes the pile and nothing else: the
      // other windows keep their place, because "bring this one forward" is not "rearrange my desk".
      frame.addEventListener("pointerdown", async () => {
        if (frame.dataset.focused === "true") return;
        frame.parentElement.querySelectorAll(".vp-window").forEach((other) => {
          delete other.dataset.focused;
        });
        frame.dataset.focused = "true";
        try {
          apply(await api.viewportAction(box.uuid, "raise", { node: entry.node }));
        } catch (error) {
          ctx.status(`could not raise that window: ${error.message}`, true);
        }
      });

      dragWindow(frame, pane.querySelector(".vp-path"), entry, "move");
      const handle = document.createElement("div");
      handle.className = "vp-resize";
      handle.dataset.action = "vp-resize";
      handle.setAttribute("aria-label", "resize this window");
      frame.append(handle);
      dragWindow(frame, handle, entry, "resize");
      return frame;
    }

    /** Move or resize a window by dragging. The frame follows the pointer; the box is told once. */
    function dragWindow(frame, grab, entry, kind) {
      if (!grab) return;
      let start = null;
      grab.addEventListener("pointerdown", (event) => {
        if (event.target.closest("button")) return;
        event.preventDefault();
        event.stopPropagation();
        grab.setPointerCapture(event.pointerId);
        start = {
          x: event.clientX,
          y: event.clientY,
          left: frame.offsetLeft,
          top: frame.offsetTop,
          width: frame.offsetWidth,
          height: frame.offsetHeight,
        };
      });
      grab.addEventListener("pointermove", (event) => {
        if (!start || !grab.hasPointerCapture?.(event.pointerId)) return;
        const dx = event.clientX - start.x;
        const dy = event.clientY - start.y;
        if (kind === "move") {
          frame.style.left = `${Math.max(0, start.left + dx)}px`;
          frame.style.top = `${Math.max(0, start.top + dy)}px`;
        } else {
          frame.style.width = `${Math.max(120, start.width + dx)}px`;
          frame.style.height = `${Math.max(60, start.height + dy)}px`;
        }
      });
      const finish = async (event) => {
        if (!start) return;
        grab.releasePointerCapture?.(event.pointerId);
        start = null;
        const payload = { node: entry.node };
        if (kind === "move") {
          payload.x = frame.offsetLeft;
          payload.y = frame.offsetTop;
        } else {
          payload.width = frame.offsetWidth;
          payload.height = frame.offsetHeight;
        }
        try {
          // The frame already sits where it was dragged; the answer is kept, not repainted, so the
          // window does not jump back for a moment while the write lands.
          apply(await api.viewportAction(box.uuid, kind === "move" ? "move" : "resize", payload));
        } catch (error) {
          ctx.status(`that window did not stay put: ${error.message}`, true);
        }
      };
      grab.addEventListener("pointerup", finish);
      grab.addEventListener("pointercancel", finish);
    }

    let briefs = new Map();

    function paint() {
      wrap.replaceChildren();
      if (layout.problems && layout.problems.length) {
        const warn = document.createElement("p");
        warn.className = "vp-problem";
        warn.setAttribute("role", "alert");
        warn.textContent = `the saved layout could not be read: ${layout.problems.join("; ")}. It starts as one viewport.`;
        wrap.append(warn);
      }
      if (layout.kind === "windowed") {
        const bar = document.createElement("div");
        bar.className = "vp-toolbar box-config";
        const add = document.createElement("button");
        add.type = "button";
        add.className = "vp-btn";
        add.dataset.action = "vp-window";
        add.textContent = "+ window";
        add.title = "Open another window";
        bar.append(add);
        wrap.append(bar);
        paintWindows();
      } else {
        paintTiling();
      }
      queueMicrotask(() => syncConfigGear());
    }

    wrap.addEventListener("click", async (event) => {
      const hit = event.target.closest("button[data-action], div[data-action='vp-divider']");
      if (!hit) return;
      const action = hit.dataset.action;
      if (action === "vp-split") {
        await act("split", { node: hit.dataset.node, direction: hit.dataset.direction });
      } else if (action === "vp-close") {
        await act("close", { node: hit.dataset.node });
      } else if (action === "vp-window") {
        await act("window", {});
      }
    });

    paint();
  },
};

/**
 * Save a setting on a box that is not the one the page is centred on.
 *
 * `saveSetting` writes to `state.current`, which is the viewport box when a pane is being edited.
 * A pane's own setting — its columns, its sort, its surface — belongs to the box inside the pane,
 * so this is the same write aimed at the right box, followed by a re-render of that pane alone.
 */
async function saveSettingFor(box, key, value, { onSaved }, options = {}) {
  const config = { ...(box.config || {}), [key]: value };
  try {
    await api.setConfig(box.uuid, config);
    if (onSaved) await onSaved();
  } catch (error) {
    status(`could not save ${key}: ${error.message}`, true);
  }
}

/* -- tags: chips under the top line ------------------------------------------
 *
 * One place for them, outside the canvas: every view draws its own content, and thirteen
 * views each learning to paint a chip is thirteen chances to paint it differently. The chips
 * come from the server already coloured, and split into what the box was given and what that
 * means — the second kind is dashed and has no cross, because removing it means moving the tag
 * in the hierarchy rather than editing the box.
 *
 * The field offers the tags that exist and takes a new one at face value: typing a word you
 * mean is how tags get invented, and sending someone to a manage window first is a rule about
 * the schema rather than about their filing.
 */
async function renderTagLine(box) {
  const token = paneGeneration; // a reply for the box before this one must not paint this one
  const line = el("tagline");
  line.textContent = "";
  line.hidden = !box;
  if (!box) return;
  let held;
  try {
    held = await api.boxTags(box.uuid);
  } catch (error) {
    line.hidden = true;
    return;
  }
  if (!stillOnScreen(token)) return;

  held.chips.forEach((chip) => {
    const node = document.createElement("span");
    node.className = "tag-chip" + (chip.explicit ? "" : " inherited");
    if (chip.explicit) {
      node.style.background = chip.colour;
      node.style.color = chip.text;
    } else {
      node.style.borderColor = chip.colour;
      node.style.color = chip.colour;
    }
    const path = chip.path.slice(0, -1).join("/");
    if (path && !chip.explicit) {
      const from = document.createElement("span");
      from.className = "tag-path";
      from.textContent = `${path}/`;
      node.append(from);
    }
    const label = document.createElement("span");
    label.textContent = chip.name;
    node.append(label);
    node.title = chip.explicit
      ? `${chip.path.join("/")} — on this box`
      : `${chip.path.join("/")} — inherited, because this box carries something below it`;
    if (chip.explicit) {
      const cross = document.createElement("button");
      cross.type = "button";
      cross.textContent = "×";
      cross.setAttribute("aria-label", `take ${chip.name} off this box`);
      cross.addEventListener("click", () => {
        // one name, asked of the server — not the set this page drew, which may predate a tag
        // added in another window a second ago
        api
          .takeTag(box.uuid, chip.name)
          .then(async () => {
            await renderTagLine(state.current);
            relistFilteredFolders(); // this box may have just left (or entered) a filter
          })
          .catch((error) => status(error.message, true));
      });
      node.append(cross);
    }
    line.append(node);
  });

  const entry = document.createElement("input");
  entry.className = "tag-add";
  entry.type = "text";
  entry.placeholder = held.explicit.length ? "+ tag" : "tag this box";
  entry.setAttribute("list", "tag-options");
  entry.setAttribute("aria-label", "add a tag to this box");
  line.append(entry);

  await ensureTagOptions();

  const commit = async () => {
    const words = entry.value.trim();
    if (!words) return;
    entry.value = "";
    // commas are how a list is typed; treating one as part of a name is how "cooking, baking"
    // arrives as a single chip
    const wanted = words
      .split(",")
      .map((word) => word.trim())
      .filter(Boolean);
    try {
      for (const word of wanted) await api.giveTag(box.uuid, word);
      await renderTagLine(state.current);
      relistFilteredFolders();
      status(wanted.length === 1 ? `tagged ${wanted[0]}` : `tagged ${wanted.length} tags`);
    } catch (error) {
      status(error.message, true);
      await renderTagLine(state.current);
    }
  };
  entry.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    }
  });
  entry.addEventListener("blur", () => {
    if (entry.value.trim()) commit();
  });
}

/* One <datalist> for every tag field in the page, filled from the server once and
   refreshed whenever a tag is created or renamed: the suggestions are the library's
   vocabulary, and a field that offers nothing encourages the same thing to be filed
   under three words. */
async function ensureTagOptions() {
  let list = el("tag-options");
  if (!list) {
    list = document.createElement("datalist");
    list.id = "tag-options";
    document.body.append(list);
  }
  let all;
  try {
    all = await api.tags();
  } catch (error) {
    return;
  }
  list.replaceChildren();
  all.tags.forEach((tag) => {
    const option = document.createElement("option");
    option.value = tag.name;
    option.label = tag.path.join("/");
    list.append(option);
  });
  state.tagTree = all.tags;
}

/* -- the Manage tags window ---------------------------------------------------
 *
 * One row per tag, edited in place: the name, what is above it, and its colour. A window that
 * made every change through a separate dialog would be three clicks per edit and would hide
 * the one thing worth seeing here, which is that a move takes its whole branch with it.
 */
async function openTagsModal() {
  el("tags-modal").showModal();
  await drawTagsTree();
}

async function drawTagsTree() {
  const list = el("tags-tree");
  list.textContent = "";
  await ensureTagOptions();
  const all = state.tagTree || [];
  if (!all.length) {
    const none = document.createElement("li");
    none.className = "muted";
    none.textContent = "No tags yet. Add the first one below — a word, or a path like home/kitchen.";
    list.append(none);
    return;
  }
  all.forEach((tag) => {
    const row = document.createElement("li");
    row.style.paddingLeft = `${0.2 + tag.depth * 1.1}rem`;

    const swatch = document.createElement("span");
    swatch.className = "tag-swatch";
    swatch.style.background = tag.colour;
    row.append(swatch);

    const name = document.createElement("input");
    name.className = "tag-name";
    name.value = tag.name;
    name.setAttribute("aria-label", `the name of the tag ${tag.name}`);
    row.append(name);

    const count = document.createElement("span");
    count.className = "tag-count";
    count.textContent = tag.count
      ? `${tag.count} on it`
      : tag.includingBelow
        ? `${tag.includingBelow} below`
        : "nothing on it";
    count.title = `${tag.count} box(es) were given this tag; ${tag.includingBelow} answer to it counting the tags below`;
    row.append(count);

    const parent = document.createElement("select");
    parent.setAttribute("aria-label", `what is above ${tag.name}`);
    const top = document.createElement("option");
    top.value = "";
    top.textContent = "— top level —";
    parent.append(top);
    all
      .filter((other) => other.name !== tag.name)
      .forEach((other) => {
        const option = document.createElement("option");
        option.value = other.name;
        option.textContent = other.path.join(" / ");
        parent.append(option);
      });
    parent.value = tag.parent || "";
    row.append(parent);

    const colour = document.createElement("input");
    colour.type = "color";
    colour.value = tag.explicit_colour || "#808080";
    colour.title = tag.explicit_colour
      ? "the colour you chose"
      : "derived from the tag above it — pick one to fix it";
    row.append(colour);

    const auto = document.createElement("button");
    auto.type = "button";
    auto.textContent = "auto";
    auto.title = "hand this colour back to the derived palette";
    auto.disabled = !tag.explicit_colour;
    row.append(auto);

    const actions = document.createElement("span");
    actions.className = "tag-actions";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Delete";
    remove.title = "the tags below it move up a level instead of going with it";
    actions.append(save, remove);
    row.append(actions);

    const complain = (error) => {
      // A refused edit must stay on the row it belongs to: this window holds a dozen
      // inputs, and one status line at the bottom of the page says nothing about which.
      status(error.message, true);
      let note = row.querySelector(".tag-error");
      if (!note) {
        note = document.createElement("span");
        note.className = "tag-error";
        row.append(note);
      }
      note.textContent = error.message;
    };

    const commit = async (payload) => {
      try {
        await api.editTag(tag.name, payload);
        await drawTagsTree();
        await renderTagLine(state.current);
        // A rename, a re-parent or a colour: the first two change who a filter reaches, and the
        // store has already re-cut every folder that asks about tags.
        relistFilteredFolders();
        status(`saved ${payload.name || tag.name}`);
      } catch (error) {
        complain(error);
      }
    };
    save.addEventListener("click", () =>
      commit({
        name: name.value.trim() === tag.name ? null : name.value.trim(),
        parent: parent.value,
        colour: colour.value,
      })
    );
    auto.addEventListener("click", () => commit({ clear_colour: true }));
    remove.addEventListener("click", () => {
      if (!remove.dataset.sure) {
        remove.dataset.sure = "1";
        remove.textContent = "Sure?";
        return;
      }
      api
        .deleteTag(tag.name)
        .then(async () => {
          await drawTagsTree();
          await renderTagLine(state.current);
          relistFilteredFolders();
          status(`deleted ${tag.name}`);
        })
        .catch(complain);
    });
    name.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        save.click();
      }
    });
    list.append(row);
  });
}

/* -- a box's settings, shown only when asked for -----------------------------
 *
 * Almost every view carries a row of controls: a folder's search, a desktop's grid size, a
 * note's format bar, a sensor's address. They are wanted occasionally and in the way
 * constantly, and the space they took was the space the box's own content wanted. So they
 * start closed, and one button in the top line opens them — pushing the content down rather
 * than lying over it, because a drawer you cannot read underneath is a drawer on top of a bug.
 *
 * The choice belongs to the page, not to the box: it is about how this person likes to work,
 * and a library should not change shape because somebody opened a folder once. It is remembered
 * in `localStorage`, which survives a reload and does not travel with the database.
 */
const CONFIG_STORAGE_KEY = "boxganizer.configHeader";
let configHeaderOpen = localStorage.getItem(CONFIG_STORAGE_KEY) === "1";
let configSyncTimer = 0;

function applyConfigHeader() {
  el("canvas").classList.toggle("config-open", configHeaderOpen);
  syncConfigGear();
}

function syncConfigGear() {
  const gear = el("toggle-config");
  if (!gear) return;
  const rows = el("canvas").querySelectorAll(".box-config");
  // A gear over a box that has no settings row is a control with nothing to control, and the
  // pane is redrawn asynchronously, so this is asked after each repaint rather than trusted
  // at click time.
  gear.hidden = rows.length === 0;
  gear.setAttribute("aria-expanded", configHeaderOpen ? "true" : "false");
  gear.title = configHeaderOpen ? "Hide the box's settings" : "Show the box's settings";
}

function toggleConfigHeader() {
  configHeaderOpen = !configHeaderOpen;
  localStorage.setItem(CONFIG_STORAGE_KEY, configHeaderOpen ? "1" : "0");
  applyConfigHeader();
  status(configHeaderOpen ? "showing the box's settings" : "hiding the box's settings");
}

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
  renderTagLine(box);
  applyConfigHeader();
  // Views fill themselves in after their own awaits — a folder's children, a sensor's probe —
  // so the gear is re-checked when the canvas settles rather than only when it was cleared.
  queueMicrotask(() => syncConfigGear());
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
  await renderContentIn(container, box, context, { depth, embedded, cell });
}

/** The same, with a caller's context — which is how a viewport pane gets its own navigation. */
async function renderContentIn(container, box, ctx, { depth = 0, embedded = true, cell = false } = {}) {
  const view = viewFor(box) || views.get("*");
  await view.render(container, box, ctx, "content", { embedded, depth, cell });
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
      // A folder that answers for a file has to say which one, or the passage looks like it
      // came out of the folder itself.
      passage.textContent = box.match.in_file
        ? `${box.match.in_file}: ${box.match.passage}`
        : box.match.passage;
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
  el("box-count").textContent = `${state.boxes.length} box${state.boxes.length === 1 ? "" : "es"}`;
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
      option.textContent = `${type.icon} ${type.label}${type.available ? "" : " — not in this install"}`;
      option.title = type.unavailable_reason || "";
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

  /*
   * Nothing is the default parent. Root holds exactly one box, and taking that slot throws the current way in out of
   * it, so preselecting it would mean a New box pressed in the panel — where nobody
   * said where the box belongs — quietly displaces whatever is the way into the
   * library. The empty choice is the honest answer to an unasked question, and it
   * matches `box add`, which files a new box nowhere unless --parent says otherwise.
   *
   * Root stays in the list: filing something at the way in is a thing a person may
   * genuinely mean to do, and then they say so.
   */
  if (preferred) select.value = preferred;
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
  index: "nb-index",
  indexReason: "nb-index-reason",
  suggest: "nb-suggest",
  resourceHint: "nb-resource-hint",
  metadata: "nb-metadata",
  warning: "nb-warning",
  error: "nb-error",
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
  index: "eb-index",
  indexReason: "eb-index-reason",
  suggest: "eb-suggest",
  resourceHint: "eb-resource-hint",
  metadata: "eb-metadata",
  warning: "eb-warning",
  error: "eb-error",
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
  // The type is named every time, not only when a caller asks for one. The list keeps
  // whatever is chosen so that filtering cannot lose it — which means that without
  // this line the type left over from the last search would be the one the next box is
  // made with, and a box nobody asked to be Markdown fails for want of a resource.
  el(CREATE_MODAL.type).value = choices.boxType || "box";
  renderConfigFields(CREATE_MODAL);
  if (choices.name) el(CREATE_MODAL.name).value = choices.name;
  if (choices.resource) el(CREATE_MODAL.resource).value = choices.resource;
  el(CREATE_MODAL.id).showModal();
  el(CREATE_MODAL.name).focus();
  refreshIndexChoice("create", CREATE_MODAL);
}

/**
 * Open the modify dialog for an existing box. Everything the box owns is here,
 * including its type: the type select is prefilled and a resource that fits a
 * more specific type offers to switch to it.
 */
function openEditBoxModal(box) {
  state.editing = box.uuid;
  modalError(EDIT_MODAL);
  state.editingBox = box; // the metadata fields are filled from this box, not the last one
  resetTypeFilter(EDIT_MODAL.type);
  el(EDIT_MODAL.name).value = box.name;
  el(EDIT_MODAL.description).value = box.description || "";
  el(EDIT_MODAL.resource).value = box.resource_uri || "";
  if (EDIT_MODAL.icon) el(EDIT_MODAL.icon).value = box.icon || "";
  el(EDIT_MODAL.type).value = box.box_type;
  renderConfigFields(EDIT_MODAL, box.config || {});
  el(EDIT_MODAL.suggest).hidden = true;
  checkResourceType(EDIT_MODAL);
  // What the box's own resource row says is the starting answer — the suggestion endpoint
  // would only agree with it — and the sentence beside it says where the index stands
  // today, because "it is ticked" and "it has been read" are different facts.
  const known = el(EDIT_MODAL.index);
  if (known) {
    const record = box.resource;
    known.checked = record ? !!record.index_it : false;
    indexChoice.edit.key = `${el(EDIT_MODAL.resource).value.trim()}|${box.box_type}`;
    indexChoice.edit.touched = false;
    const reason = el(EDIT_MODAL.indexReason);
    if (reason) {
      reason.hidden = false;
      reason.textContent = record ? indexStateSentence(record) : "this box points at nothing yet";
    }
  }
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
  if (el(EDIT_MODAL.index)) payload.index_resource = el(EDIT_MODAL.index).checked;
  // Merged over what the box already carries: the dialog shows the keys this type asks
  // for, and it must not quietly empty the ones it does not.
  const was = (state.editingBox && state.editingBox.metadata) || {};
  payload.metadata = { ...was, ...collectMetadata(EDIT_MODAL) };
  if (EDIT_MODAL.icon) payload.icon = el(EDIT_MODAL.icon).value.trim() || null;
  try {
    payload.config = collectConfig(EDIT_MODAL);
  } catch (error) {
    modalError(EDIT_MODAL, error.message);
    return;
  }
  try {
    modalError(EDIT_MODAL);
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
    modalError(EDIT_MODAL, error.message);
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


/**
 * The dialog's indexing box, and what the core says about it.
 *
 * The default is not guessed here: the page asks `/api/index-suggestion`, which answers
 * with the box's own rule (`indexes_content`) or the shape of the address — a local file
 * whose ending says words, yes; a picture, a web page, a topic, no — and with the reason
 * in words. A checkbox whose default is invented in JavaScript is a rule that disagrees
 * with the server by the next release.
 *
 * Someone's own tick is kept while the address and the type stay what they were, and is
 * thrown away when either changes: a decision about `/srv/notes.txt` says nothing about
 * `/srv/backup.zip`, and pretending otherwise would carry a tick across two different
 * answers to two different questions.
 */
const indexChoice = { create: {key: "", touched: false}, edit: {key: "", touched: false} };

async function refreshIndexChoice(which, modal) {
  const box = el(modal.index);
  const reason = el(modal.indexReason);
  if (!box) return;
  const resource = el(modal.resource).value.trim();
  const key = `${resource}|${el(modal.type).value}`;
  const remembered = indexChoice[which];
  if (key === remembered.key && remembered.touched) return;
  try {
    const answer = await request(
      `/api/index-suggestion?resource=${encodeURIComponent(resource)}&box_type=${encodeURIComponent(
        el(modal.type).value
      )}`
    );
    box.checked = !!answer.index_it;
    if (reason) {
      reason.textContent = answer.reason;
      reason.hidden = false;
    }
    remembered.key = key;
    remembered.touched = false;
  } catch {
    if (reason) reason.hidden = true; // the box still works; the explanation is a courtesy
  }
}

/** Where the index stands for one resource, in the fewest honest words. */
function indexStateSentence(record) {
  if (!record.index_it) return "left out of the full-text search";
  if (record.stale) return "in the index, but the file has changed since — it needs reading again";
  if (!record.indexed_at) return "asked for, and not read into the index yet";
  return `in the index since ${record.indexed_at.replace("T", " ").replace(/\+.*$/, "")}`;
}

function wireIndexChoice(which, modal) {
  const box = el(modal.index);
  if (!box) return;
  box.addEventListener("change", () => {
    indexChoice[which].touched = true;
    const reason = el(modal.indexReason);
    if (reason && !reason.hidden) {
      reason.textContent = box.checked
        ? "asked for by hand: this file will be read into the index"
        : "turned off by hand: this file will not be searched by what it says";
    }
  });
  el(modal.resource).addEventListener("input", () => refreshIndexChoice(which, modal));
}

function registerConfigForm(typeKey, form) {
  configForms.set(typeKey, form);
}

/**
 * Say what the server refused, where the person who caused it is looking.
 *
 * A dialog covers the status bar, so a failure written there is a failure nobody sees:
 * the page appeared to do nothing, and only the access log knew it had answered 422.
 */
function modalError(modal = CREATE_MODAL, message = "") {
  const spot = el(modal.error);
  if (!spot) return;
  spot.textContent = message;
  spot.hidden = !message;
}

/* -- what this type asks the resource field for ------------------------------
 *
 * A dialog that shows every control for every type is a dialog asking the same question
 * of a note, a topic and a host. It cannot be the same question. `mqtt://…` and
 * `ssh://…` are addresses whose *path* is the thing being pointed at, so the field needs
 * an example rather than a placeholder word; and Browse and the index tick mean nothing
 * until the resource is a file on this machine — showing them for a sensor invites a
 * person to tick something that will be ignored.
 *
 * A type that genuinely needs a piece of metadata gets a field for it, named. Requiring
 * something in a form the page never renders is not a rule, it is a bug with an error
 * message: this is how creating an MQTT box answered 422 for as long as the page existed.
 */

function applyResourceShape(modal = CREATE_MODAL) {
  const kind = state.types.get(el(modal.type).value) || {};
  const field = el(modal.resource);
  const hint = el(modal.resourceHint);
  modalError(modal);
  modalWarning(modal, kind.available === false ? kind.unavailable_reason || "" : "");
  field.placeholder = kind.resource_hint || "/path or https://…";
  if (hint) {
    hint.hidden = !kind.resource_hint;
    hint.textContent = kind.resource_hint ? `address: ${kind.resource_hint}` : "";
  }

  // Only a file on this machine can be picked, and only its words can be indexed.
  const local = kind.resource_is_local !== false;
  const browse = field.closest(".resource-field")?.querySelector(".browse");
  if (browse) browse.hidden = !local;
  const choice = el(modal.index)?.closest(".index-choice");
  if (choice) choice.hidden = !local;
  if (!local) {
    const reason = el(modal.indexReason);
    if (reason) reason.hidden = true;
  }

  renderMetadataFields(modal, kind);
}

/**
 * Warn, in the dialog, about a type this install cannot run.
 *
 * A box type whose live behaviour needs a package that is not installed is not broken: the
 * box, its place in the graph and its entry in the index are all real, and another machine
 * may read it happily. What would be dishonest is letting someone build one and then watch
 * it fail with something about a dropped stream. The command to fix it comes from the server,
 * which is the only place that knows how it was installed.
 */
function modalWarning(modal = CREATE_MODAL, message = "") {
  const spot = el(modal.warning);
  if (!spot) return;
  spot.textContent = message;
  spot.hidden = !message;
}

function renderMetadataFields(modal = CREATE_MODAL, kind = {}) {
  const holder = el(modal.metadata);
  if (!holder) return;
  const wanted = [
    ...new Set([...(kind.required_metadata || []), ...(kind.optional_metadata || [])]),
  ];
  const source = modal === EDIT_MODAL ? state.editingBox || state.current : null;
  const existing = source ? source.metadata || {} : {};
  const kept = {};
  // Fields already filled survive a type change back to the same type: the person's
  // typing is worth more than the convenience of rebuilding the row.
  holder.querySelectorAll("[name^='metadata:']").forEach((input) => {
    kept[input.name] = input.value;
  });
  holder.replaceChildren();
  wanted.forEach((key) => {
    const label = document.createElement("label");
    const title = document.createElement("span");
    title.textContent = key.replace(/_/g, " ");
    const input = document.createElement("input");
    input.name = `metadata:${key}`;
    input.type = "text";
    input.value =
      kept[`metadata:${key}`] !== undefined
        ? kept[`metadata:${key}`]
        : String(existing[key] ?? "");
    input.placeholder = EXAMPLE_METADATA[key] || "";
    const required = (kind.required_metadata || []).includes(key);
    if (!required) title.textContent += " (optional)";
    input.required = required;
    label.append(title, input);
    holder.append(label);
  });
}

const EXAMPLE_METADATA = {
  password_file: "~/.vnc/workstation.pass",
  entity_id: "sensor.boiler_room",
  topic: "boiler/pressure",
  host: "build.local",
};

function collectMetadata(modal = CREATE_MODAL) {
  const holder = el(modal.metadata);
  const metadata = {};
  if (!holder) return metadata;
  holder.querySelectorAll("[name^='metadata:']").forEach((input) => {
    const key = input.name.slice("metadata:".length);
    const value = input.value.trim();
    if (value) metadata[key] = value;
  });
  return metadata;
}

function renderConfigFields(modal = CREATE_MODAL, values = {}) {
  const holder = el(modal.config);
  holder.replaceChildren();
  const typeKey = el(modal.type).value;
  applyResourceShape(modal);
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
    index_resource: el("nb-index") ? el("nb-index").checked : null,
    metadata: collectMetadata(CREATE_MODAL),
    config: {},
  };
  try {
    payload.config = collectConfig();
  } catch (error) {
    modalError(CREATE_MODAL, error.message);
    return;
  }
  try {
    modalError(CREATE_MODAL); // a previous refusal is stale the moment the box is tried again
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
    // in the dialog, not behind it: the status bar is covered by this very modal
    modalError(CREATE_MODAL, error.message);
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

/* -- checking the library ---------------------------------------------------
 *
 * The same check `box check` runs, with the same set of repairs. A finding is only ever
 * worth showing if there is something to do about it, which is why the verbs come from the
 * core (`boxganizer.check`) and this is only their presentation: a page that invented its
 * own fixes would be a page whose buttons the server sometimes refuses.
 *
 * Two of the repairs delete something, so they take two clicks — the first one changes the
 * button into the question, and no browser dialog is involved. The rest are one click,
 * because they are all reversible by asking again: reading a file, or taking one out of the
 * index and putting it back.
 */

const CHECK_FIXES = {
  relocate: { label: "Point at something else", needsValue: true },
  reindex: { label: "Read it again" },
  unindex: { label: "Leave it out of the index" },
  "delete-box": { label: "Delete the box", dangerous: true },
  "delete-file": { label: "Delete the file", dangerous: true },
};

async function openCheckModal() {
  el("check-modal").showModal();
  await drawCheck();
}

async function drawCheck() {
  const list = el("check-list");
  const summary = el("check-summary");
  summary.textContent = "looking at every box…";
  list.textContent = "";
  let report;
  try {
    report = await api.check();
  } catch (error) {
    summary.textContent = `the check could not run: ${error.message}`;
    return;
  }
  summary.textContent = report.summary;
  el("check-confine").textContent = `files expected inside ${report.confine}`;
  if (!report.findings.length) {
    const fine = document.createElement("li");
    fine.className = "no-fix";
    fine.textContent = "Nothing to report: every box's resource is where it said it would be.";
    list.append(fine);
    return;
  }
  let shown = "";
  report.findings.forEach((finding) => {
    if (finding.kind !== shown) {
      shown = finding.kind;
      const head = document.createElement("h3");
      head.textContent = shown.replace(/_/g, " ");
      list.append(head);
    }
    list.append(checkRow(finding));
  });
}

function checkRow(finding) {
  const row = document.createElement("li");
  row.className = `check-finding${finding.severity === "broken" ? " is-broken" : ""}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = (finding.box && finding.box.name) || finding.path.split("/").pop() || "a file";
  const what = document.createElement("span");
  what.className = "what";
  what.textContent = finding.detail;
  row.append(who, what);
  if (finding.reference && finding.reference !== finding.path) {
    const where = document.createElement("span");
    where.className = "where";
    where.textContent = finding.reference;
    row.append(where);
  }
  if (!finding.fixes.length) {
    const none = document.createElement("span");
    none.className = "no-fix";
    none.textContent = "no automatic fix — the words around it are yours to change";
    row.append(none);
    return row;
  }
  finding.fixes.forEach((action) => {
    const spec = CHECK_FIXES[action] || { label: action };
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = spec.label;
    button.addEventListener("click", () => fixFinding(finding, action, button));
    row.append(button);
  });
  return row;
}

async function fixFinding(finding, action, button) {
  const spec = CHECK_FIXES[action] || {};
  if (spec.dangerous && !button.classList.contains("asking")) {
    const words = button.textContent;
    button.classList.add("asking");
    button.textContent = "Really?";
    setTimeout(() => {
      button.classList.remove("asking");
      button.textContent = words;
    }, 4000);
    return;
  }
  if (spec.needsValue) {
    openFilePicker((chosen) => sendFix(finding, action, chosen), finding.reference || "");
    return;
  }
  await sendFix(finding, action, null);
}

async function sendFix(finding, action, value) {
  try {
    const answer = await api.checkFix({
      kind: finding.kind,
      action,
      box: (finding.box && finding.box.uuid) || "",
      path: finding.path || "",
      value,
    });
    status(answer.did);
    await refreshSide(); // a repair may have deleted a box, or filed new words
    await drawCheck();
  } catch (error) {
    status(error.message, true);
    await drawCheck(); // whatever the page was showing is now known to be out of date
  }
}

/* -- the special-actions menu, and the standing index line -------------------
 *
 * The menu holds whatever is about the library rather than about the box on screen; there
 * is nowhere else in this layout for such a thing, and putting it in every box's context
 * menu would be a lie about what it applies to.
 *
 * The index line at the bottom is the same idea pointing the other way: whether a search
 * will find what is in your files is something you want known *while you are looking*, not
 * inferred after a search comes back empty. So it is drawn on every panel, in front of the
 * count it sits beside, and it says what the database believes — unless it was just asked
 * to look at the files, which is what happens when the panel is opened and when a pass ends.
 */

/** How many resources a pass would take another look at, and so what to call them. */
function indexWaiting(status_) {
  return (status_.stale || 0) + (status_.untouched || 0);
}

function indexWords(status_) {
  if (status_.running) return "indexing…";
  const waiting = indexWaiting(status_);
  // "reindex" only when something was read and has since changed; a file the index has never
  // opened is not being re-read, and a line that says otherwise teaches people to distrust it.
  if (status_.stale) return `${waiting} to reindex`;
  if (waiting) return `${waiting} to index`;
  if (!status_.wanted) return "nothing to index";
  return `${status_.indexed} indexed`;
}

function indexDetail(status_) {
  const parts = [
    `${status_.wanted} file${status_.wanted === 1 ? "" : "s"} wanted in the index`,
    `${status_.indexed} read into it`,
  ];
  if (status_.stale) parts.push(`${status_.stale} changed since they were read`);
  if (status_.outstanding) parts.push(`${status_.outstanding} not read yet`);
  const last = status_.last;
  const quiet = status_.unreadable_examples || [];
  if (status_.unreadable) {
    parts.push(
      `${status_.unreadable} hold nothing to read` +
        (quiet.length ? ` (${quiet.join(", ")})` : "")
    );
  }
  if (last) {
    parts.push(
      last.error
        ? `last pass stopped: ${last.error}`
        : `last pass: ${last.in_index} files in the index, ${last.added} brought in, ` +
          `${last.skipped} could not be read (${last.seconds}s)`
    );
  }
  return parts.join(" · ");
}

let indexTimer = null;

async function refreshIndexStatus({ check = false } = {}) {
  const spot = el("index-status");
  let status_;
  try {
    // Asked, not awaited, by the caller: a pass over the files is a stat per file, and the
    // panel would rather draw now and correct itself a moment later.
    status_ = await api.indexStatus(check);
  } catch (error) {
    spot.textContent = "index: unknown";
    spot.title = error.message;
    return;
  }
  spot.textContent = indexWords(status_);
  spot.title = indexDetail(status_);
  // A number with nothing behind it should not invite a click, and should not pretend to be
  // a control to a keyboard either.
  const waiting = indexWaiting(status_);
  spot.disabled = waiting === 0;
  if (spot.disabled) spot.removeAttribute("aria-haspopup");
  else spot.setAttribute("aria-haspopup", "dialog");
  spot.classList.toggle("is-running", !!status_.running);
  spot.classList.toggle("has-stale", !status_.running && (status_.stale > 0 || status_.outstanding > 0));
  if (status_.running) {
    if (!indexTimer) indexTimer = setInterval(() => refreshIndexStatus(), 1200);
  } else if (indexTimer) {
    clearInterval(indexTimer);
    indexTimer = null;
    // Files were read while the timer ran, so what the search finds has changed underneath
    // the panel. The counts and the list are re-read rather than left as they were.
    refreshSide();
  }
}

/**
 * The names behind the number in the status line.
 *
 * "2 to reindex" is a claim about the library, and a claim a person can click should show its
 * evidence and offer the repair in the same place. The rows are resources rather than boxes
 * because that is what the index is keyed on: several boxes may share one file, and the files
 * a folder brought in belong to no box of their own — they are named by the folder, which is
 * why those chips are dashed.
 *
 * It opens read-only. The button says what is waiting; the pass stays a separate decision,
 * taken with the button in the footer, because reading a library is not a side effect of
 * looking at a list.
 */
async function openIndexModal() {
  el("index-modal").showModal();
  await drawPending({ check: false });
}

async function drawPending({ check = false } = {}) {
  const list = el("index-pending-list");
  const summary = el("index-pending-summary");
  summary.textContent = check
    ? "looking at the files…"
    : "reading what the index already believes…";
  list.textContent = "";
  let report;
  try {
    if (check) await api.indexStatFiles();
    report = await api.indexPending();
  } catch (error) {
    summary.textContent = `the index could not be asked: ${error.message}`;
    return;
  }
  const items = report.items || [];
  const fixable = items.filter((item) => item.fixable);
  const hopeless = items.filter((item) => !item.fixable);
  el("pending-reindex").disabled = fixable.length === 0;
  if (!items.length) {
    const fine = document.createElement("li");
    fine.className = "pending-none";
    fine.textContent = "Nothing is waiting: every file the index is wanted for has been read.";
    list.append(fine);
    summary.textContent = `${report.indexed} in the index, ${report.wanted} wanted`;
    refreshIndexStatus();
    return;
  }
  // Two groups, because they are two different claims. The number in the status line counts
  // only the first group; a list that quietly included the second would say "3 to reindex"
  // about two files that would still be there after any number of passes.
  const waiting = fixable.length;
  summary.textContent =
    (waiting
      ? `${waiting} file${waiting === 1 ? "" : "s"} the index can catch up on`
      : "nothing the index is behind on") +
    (hopeless.length
      ? ` · ${hopeless.length} with no text in it to index`
      : "") +
    ` · ${report.wanted} wanted in the index`;
  const draw = (item, heading) => {
    if (heading) {
      const head = document.createElement("li");
      head.className = "pending-heading";
      head.textContent = heading;
      list.append(head);
    }
    const row = document.createElement("li");
    const what = document.createElement("span");
    what.className = "pending-what";
    what.textContent = item.reference;
    what.title = item.reference;
    row.append(what);
    item.boxes.forEach((holder) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "pending-box" + (holder.inside ? " inside" : "");
      chip.textContent = holder.inside ? `in ${holder.name}` : holder.name;
      chip.title = holder.inside
        ? "a file inside an indexed folder, so it has no box of its own"
        : "open this box";
      chip.addEventListener("click", () => {
        el("index-modal").close();
        openBox(holder.uuid);
      });
      row.append(chip);
    });
    if (!item.boxes.length) {
      const orphan = document.createElement("span");
      orphan.className = "pending-box inside";
      orphan.textContent = "no box";
      orphan.title = "a resource the index is kept for that no box points at";
      row.append(orphan);
    }
    const why = document.createElement("span");
    why.className = "pending-why";
    // The third kind is told apart because reindexing will not help: a binary that gave up no
    // text will give up no text again, and a list that promised otherwise is a list still here
    // tomorrow.
    why.textContent = item.words;
    row.append(why);
    list.append(row);
  };
  // Said out loud, because the list is a query over what the index has rows for, and a file
  // added inside an indexed folder since the last pass has no row yet: nothing here can name
  // it. A pass will find it — which is the other button, and the reason both are on screen.
  if (report.folders) {
    const note = document.createElement("li");
    note.className = "pending-caveat";
    note.textContent =
      "A file added inside an indexed folder since the last pass is not here yet: it has" +
      " no row until something walks the folder. Reindexing does that.";
    list.append(note);
  }
  fixable.forEach((item) => draw(item, ""));
  hopeless.forEach((item, index) =>
    draw(
      item,
      index === 0
        ? "Read, and nothing in them for the index. Reindexing will not change this one."
        : ""
    )
  );
  refreshIndexStatus();
}

async function runIndexation(scope) {
  try {
    await api.indexRun(scope);
    el("index-status").textContent = "indexing…";
    status(scope === "everything" ? "re-reading every file the index is wanted for…" : "reading what changed…");
    refreshIndexStatus();
  } catch (error) {
    status(error.message, true);
  }
}

async function showAbout() {
  const modal = el("about-modal");
  el("about-version").textContent = "reading…";
  modal.showModal();
  try {
    const health = await api.health();
    el("about-version").textContent = `version ${health.version}`;
  } catch (error) {
    // The window opened, so it should say what it does not know rather than nothing.
    el("about-version").textContent = `version unknown: ${error.message}`;
  }
}

function openSideMenu(event) {
  event.preventDefault();
  event.stopPropagation(); // the document hides the menu on any click, including this one
  const button = event.currentTarget;
  button.setAttribute("aria-expanded", "true");
  document.addEventListener("click", () => button.setAttribute("aria-expanded", "false"), {
    once: true,
  });
  const where = button.getBoundingClientRect();
  showMenu(where.left, where.bottom + 4, [
    { label: "About", run: showAbout },
    { label: "Manage tags…", run: openTagsModal },
    { label: "Check the library", run: openCheckModal },
    { separator: true },
    { label: "Index what changed", run: () => runIndexation("outstanding") },
    { label: "Re-read the whole library", run: () => runIndexation("everything") },
  ]);
}

/* -- the pane answers a right-click no view claimed --------------------------
 *
 * Every view that means something by a right-click has its own handler and calls
 * `preventDefault`: a folder row, a grid cell, a desktop tile, a file in the explorer. What
 * had no handler was the *space around* them — right-click the empty part of a folder with no
 * children, or the strip below a short list, and nothing came up, which reads as the box
 * having no menu rather than the click having missed.
 *
 * So the pane answers whenever no view did, and it acts on the box the pane is showing: that
 * is the only subject a click on the box's own empty space can have. It is the same shape as
 * the pane's drop handling — a view claims a drop or a click, and the pane is the fallback,
 * never the first claim.
 */
function wirePaneMenu() {
  const pane = el("canvas");
  pane.addEventListener("contextmenu", (event) => {
    if (event.defaultPrevented) return; // a view means something specific by this one
    const box = state.current;
    if (!box) return;
    // Inside a field the browser's own menu is the useful one: copy, paste, spellcheck. A
    // box menu that replaced it would cost the person the reason they right-clicked.
    if (event.target instanceof Element && event.target.closest("input, textarea, [contenteditable]")) {
      return;
    }
    // Words the person has highlighted are the one thing a right-click is definitely about,
    // and the box menu would replace the copy they came for.
    if (String(window.getSelection() || "").length) return;
    event.preventDefault();
    const kind = typeOf(box) || {};
    const takesChildren = kind.allows_children !== false && kind.accepts_filing !== false;
    const extra = takesChildren
      ? [
          { separator: true },
          { label: `New box in ${box.name}`, run: () => openNewBoxModal({ parent: box.uuid }) },
        ]
      : [];
    showMenu(event.clientX, event.clientY, boxMenuItems(box, extra));
  });
}

/* -- the pane files what is dropped on it -----------------------------------
 *
 * A view that means something particular by a drop claims it first: a grid cell takes
 * the box into that cell, the desktop takes the coordinates of the point, a checklist
 * line hangs an icon on that item, a mindmap node files the box inside the node. Each
 * of them says so by preventing the default, which is the only signal a listener higher
 * up has that the gesture was spoken for.
 *
 * What is left over means the plain thing: put that box into the box I am looking at.
 * Before this, a drop had to be *aimed*. The folder view's own target ends where its
 * list ends, so a pane that is mostly empty background refused a drop a few pixels
 * below it; the same gap sat under the grid, around the desktop canvas, and across
 * almost the whole of a checklist. The pane is the box, so the pane takes it.
 *
 * Only for types that hold boxes. Dropping something on a markdown box is not a filing
 * decision, and pretending otherwise would mean either a silent no or a server refusing
 * what the page just offered.
 */

function paneAcceptsDrops() {
  const box = state.current;
  if (!box) return null;
  const kind = state.types.get(box.box_type);
  if (!kind || !kind.allows_children) return null;
  return kind.accepts_filing === false ? null : box;
}

/* May a box be filed here by hand? Two different noes, and only one of them needs a sentence.
 *
 * A note or an image does not hold boxes at all, so nothing about it invites a drop and the page is
 * right to be quiet. A dynamic folder is the opposite: it is drawn as a folder, lists boxes, and
 * then refuses the drop — the one no a person would not predict. So the refusal says why, in the
 * type's own words, and the pointer says "not here" while it is over the sheet.
 */
function filingTarget(box) {
  const kind = box ? state.types.get(box.box_type) : null;
  if (!kind || kind.allows_children === false) return { ok: false, reason: null };
  if (kind.accepts_filing === false) {
    return { ok: false, reason: kind.filing_refusal || "that folder chooses its own members" };
  }
  return { ok: true, reason: null };
}

function wirePaneDrops() {
  const pane = el("canvas");

  pane.addEventListener("dragover", (event) => {
    if (!paneAcceptsDrops()) return;
    const types = event.dataTransfer ? Array.from(event.dataTransfer.types) : [];
    const draggingFile = types.includes(FILE_DRAG_TYPE);
    if (!draggingFile && !types.includes("text/plain")) return;
    event.preventDefault(); // a candidate, not a claim: a view may still take this drop
    event.dataTransfer.dropEffect = draggingFile ? "copy" : "move";
    pane.classList.add("drop-target");
  });

  pane.addEventListener("dragleave", (event) => {
    if (!pane.contains(event.relatedTarget)) pane.classList.remove("drop-target");
  });

  pane.addEventListener("drop", async (event) => {
    pane.classList.remove("drop-target");
    if (event.defaultPrevented) return; // a view means something specific by this one
    const box = paneAcceptsDrops();
    if (!box) return;
    event.preventDefault();
    const draggedFile = readDraggedFile(event);
    if (draggedFile) {
      try {
        const kind = typeOf(box) || {};
        const takesChildren = kind.allows_children !== false && kind.accepts_filing !== false;
        await createBoxFromDraggedFile(draggedFile, takesChildren ? box.uuid : null);
        openBox(box.uuid); // land back where the drop happened, now showing what arrived
      } catch (error) {
        status(`cannot make a box from that file: ${error.message}`, true);
      }
      return;
    }
    const uuid = event.dataTransfer ? event.dataTransfer.getData("text/plain") : "";
    if (!uuid || uuid === box.uuid) return;
    try {
      await api.link(box.uuid, uuid);
      await refreshSide();
      openBox(box.uuid); // land back where the drop happened, now showing what arrived
    } catch (error) {
      status(`cannot file that box in ${box.name}: ${error.message}`, true);
    }
  });
}

/**
 * The sixteen colours the scale picker offers, read once from the server.
 *
 * They are the server's list because the server is what refuses a colour it does not recognise;
 * a swatch that the store would reject is a control that lies about what it saves.
 */
async function loadSensorPalette() {
  if (state.sensorPalette) return state.sensorPalette;
  try {
    state.sensorPalette = await api.sensorPalette();
  } catch {
    // A missing palette costs a swatch, not the box: the value still shows, and a hex value can
    // still be typed. Nothing here is worth failing the whole page over.
    state.sensorPalette = { palette: [], representations: ["hbar"] };
  }
  return state.sensorPalette;
}

async function init() {
  try {
    const types = await api.types();
    state.types = new Map(types.map((type) => [type.key, type]));
  } catch (error) {
    status(`cannot reach the server: ${error.message}`, true);
    return;
  }

  loadSensorPalette(); // not awaited: a missing palette costs a swatch, not the page

  fillTypeChoices();
  wireTypeFilters();
  wireIndexChoice("create", CREATE_MODAL);
  wireIndexChoice("edit", EDIT_MODAL);
  el("search").addEventListener("input", () => refreshSide());
  el("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    refreshSide();
  });
  el("up").addEventListener("click", goUp);
  el("new-box").addEventListener("click", () => openNewBoxModal());
  el("go-root").addEventListener("click", goToRoot);
  el("index-status").addEventListener("click", openIndexModal);
  el("toggle-config").addEventListener("click", toggleConfigHeader);
  applyConfigHeader();
  // Views fill the pane in after their own awaits — a folder's children, a grid's placement,
  // a sensor's probe — so whether this box has a settings row at all is only known when the
  // canvas settles. One observer for the pane, coalesced: a repaint is a burst of mutations,
  // and the answer is one boolean.
  new MutationObserver(() => {
    window.clearTimeout(configSyncTimer);
    configSyncTimer = window.setTimeout(syncConfigGear, 60);
  }).observe(el("canvas"), { childList: true, subtree: true });
  el("index-close").addEventListener("click", () => el("index-modal").close());
  el("pending-look").addEventListener("click", () => drawPending({ check: true }));
  el("pending-reindex").addEventListener("click", () => {
    el("index-modal").close();
    runIndexation("outstanding");
  });
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

  wirePaneDrops();
  wirePaneMenu();
  // Dropping on the library list means "into the library", not "into this box": the box is made
  // and left unfiled, which is where a box belongs when nobody chose a home for it.
  const sideList = el("side");
  sideList.addEventListener("dragover", (event) => {
    const types = event.dataTransfer ? Array.from(event.dataTransfer.types) : [];
    if (!types.includes(FILE_DRAG_TYPE)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    sideList.classList.add("drop-target");
  });
  sideList.addEventListener("dragleave", () => sideList.classList.remove("drop-target"));
  sideList.addEventListener("drop", async (event) => {
    sideList.classList.remove("drop-target");
    const dragged = readDraggedFile(event);
    if (dragged) {
      event.preventDefault();
      try {
        await createBoxFromDraggedFile(dragged, null);
      } catch (error) {
        status(`cannot make a box from that file: ${error.message}`, true);
      }
      return;
    }
    // A box dropped back on the list is not a filing: every box is already in the list, so there
    // is nothing to copy a link into. Shift is the gesture with a meaning here — "take it out of
    // the box I dragged it from" — and without Shift the drop changes nothing, which is the only
    // answer that cannot lose somebody's filing.
    const uuid = event.dataTransfer ? event.dataTransfer.getData("text/plain") : "";
    const from = event.dataTransfer ? event.dataTransfer.getData("application/x-boxganizer-from") : "";
    if (!uuid || !event.shiftKey || !from) {
      if (uuid) status(uuid && from ? "it is still filed where it was; hold Shift to take it out" : "");
      return;
    }
    event.preventDefault();
    try {
      await api.unlink(from, uuid);
      status("taken out of the box it came from; the box itself is untouched");
      await refreshSide();
    } catch (error) {
      status(`could not unfile it: ${error.message}`, true);
    }
  });

  el("side-menu").addEventListener("click", openSideMenu);
  el("about-close").addEventListener("click", () => el("about-modal").close());
  el("tags-close").addEventListener("click", () => el("tags-modal").close());
  el("tag-new").addEventListener("submit", async (event) => {
    event.preventDefault();
    const field = el("tag-new-name");
    const words = field.value.trim();
    if (!words) return;
    try {
      await api.createTag({ name: words });
      relistFilteredFolders();
      field.value = "";
      await drawTagsTree();
      await renderTagLine(state.current);
      status(`created ${words}`);
    } catch (error) {
      status(error.message, true);
    }
  });
  el("check-close").addEventListener("click", () => el("check-modal").close());
  el("check-again").addEventListener("click", () => drawCheck());
  el("about-modal").addEventListener("click", (event) => {
    if (event.target === el("about-modal")) el("about-modal").close(); // a click past the card
  });

  await refreshSide();
  refreshIndexStatus({ check: true }); // the panel draws first; the files are counted after
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
      orphans.forEach((child) => strip.append(chipFor(child, ctx, box.uuid)));
      canvas.append(strip);
    }
  },
});

function chipFor(child, ctx, fromUuid) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "chip";
  chip.dataset.from = fromUuid || "";
  chip.draggable = true;
  chip.append(iconElement(child), document.createTextNode(` ${child.name}`));
  chip.addEventListener("dragstart", (event) => dragBox(event, child.uuid, chip.dataset.from));
  chip.addEventListener("dblclick", () => ctx.open(child.uuid, { trail: ctx.trail() }));
  return chip;
}

function gridToolbar(box, options, ctx) {
  const bar = document.createElement("div");
  bar.className = "grid-toolbar box-config";

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
    dragBox(event, child.uuid, box.uuid);
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
    const placed = await dropBox(event, ctx, box.uuid, {
      after: () => api.gridPlace(box.uuid, event.dataTransfer.getData("text/plain"), [row, column]),
    });
    if (placed) ctx.reload();
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

    /**
     * Size the canvas to the pane and scale it to fit, and say what was settled on.
     *
     * The canvas is never smaller than the icons need, never larger than what it was
     * arranged at unless the pane has grown into empty space, and is only ever drawn
     * smaller — a desktop with three icons does not blow them up to fill the window,
     * and never past the point where a label is unreadable.
     */
    function fitSurface() {
      const availW = wrap.clientWidth;
      const availH = wrap.clientHeight;
      if (!availW || !availH) return null;
      const base = laidOut || {width: availW, height: availH};
      const target = {
        width: Math.max(base.width, availW, extent.width),
        height: Math.max(base.height, availH, extent.height),
      };
      const ratio = Math.min(availW / target.width, availH / target.height);
      const scale = Math.max(0.2, Math.min(1, ratio));
      surface.style.width = `${target.width}px`;
      surface.style.height = `${target.height}px`;
      surface.style.transform = scale === 1 ? "" : `scale(${scale})`;
      surface.dataset.scale = String(scale);
      return {target, scale};
    }

    let fitted = fitSurface();
    // The pane changes size when a grid gets more rows, when the window is dragged,
    // when the left panel is resized: the canvas follows it, asking nothing of the
    // server. The observer is released with the pane, like any other live thing.
    const watcher = new ResizeObserver(() => {
      fitted = fitSurface();
    });
    watcher.observe(wrap);
    holdLive(() => watcher.disconnect());

    if (!opts.embedded && fitted && fitted.scale === 1) {
      /*
       * What is kept is the size of the CANVAS — the number every icon's coordinates
       * are written in — and never the rectangle on screen, because that is the same
       * number multiplied by the scale. Recording the screen rectangle made a desktop
       * shrink a little each time it was opened: 1200 drawn at 0.6 came back as 720,
       * and in the root's slot, where the pane is smaller than the window, that is
       * what a person sees as the pane flapping between needing a scrollbar and not.
       *
       * Only a full-size layout writes it, so a scaled-down view cannot redefine the
       * canvas it is merely showing.
       */
      const wanted = {width: Math.round(fitted.target.width), height: Math.round(fitted.target.height)};
      if (
        Math.abs(wanted.width - Number(recorded.width || 0)) > 2 ||
        Math.abs(wanted.height - Number(recorded.height || 0)) > 2
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
      loose.forEach((child) => strip.append(chipFor(child, ctx, box.uuid)));
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
      if (event.shiftKey) {
        // A move is filed first, so the tile that appears under the pointer is the box's only copy
        const from = event.dataTransfer.getData("application/x-boxganizer-from");
        if (from && from !== box.uuid) await api.link(box.uuid, dragged, from);
      }
      const frame = surface.getBoundingClientRect();
      // The rect is the scaled box; the position the server keeps is in canvas
      // pixels, so the pointer's distance from the corner is divided back out.
      const scale = surfaceScale(surface);
      const placed = await placeOnDesktop(
        box,
        ctx,
        dragged,
        (event.clientX - frame.left) / scale,
        (event.clientY - frame.top) / scale
      );
      // This is the one desktop gesture that redraws: the box that landed had no tile on
      // this surface a moment ago, so there is nothing here to update in place.
      if (placed) ctx.reload();
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
    return true;
  } catch (error) {
    ctx.status(`cannot place that icon here: ${error.message}`, true);
    return false;
  }
}

function desktopToolbar(box, options, ctx, count) {
  const bar = document.createElement("div");
  bar.className = "desktop-toolbar box-config";

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
  function follow(event, onMove, onCommit, show) {
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

    /*
     * `show` paints the tile where the gesture has just put it, on every pointer move
     * and not only at the end. Without it the icon sits still while the hand drags and
     * then jumps to wherever it ended up — which reads as the desktop lagging a whole
     * gesture behind the mouse. Nothing is written to the database until the release:
     * what moves during the drag is the picture.
     */
    const move = (next) => {
      latest = onMove(origin, next);
      if (show) show(latest);
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
        if (where.x === rect.x && where.y === rect.y) {
          // put back where it was: the tile is already there, the drag only drew it
          return;
        }
        // The tile is already painted where the hand left it, so the move is written into
        // the picture this view holds rather than fetched again. Re-rendering the desktop
        // to show one icon in a place you just dragged it to throws away every tile — and
        // with them every thumbnail, which the browser then re-decodes in front of you:
        // a whole surface flickering to confirm something you can already see.
        placeOnDesktop(box, ctx, child.uuid, where.x, where.y).then((moved) => {
          if (!moved) return;
          rect.x = where.x;
          rect.y = where.y;
        });
      },
      (where) => {
        tile.style.left = `${where.x}px`;
        tile.style.top = `${where.y}px`;
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
        if (size.width === rect.width && size.height === rect.height) return;
        api
          .desktopResize(box.uuid, child.uuid, size.width, size.height)
          .then(() => {
            rect.width = size.width;
            rect.height = size.height; // same reason as the move: the corner is already there
          })
          .catch((error) => ctx.status(`cannot size that icon: ${error.message}`, true));
      },
      (size) => {
        tile.style.width = `${size.width}px`;
        tile.style.height = `${size.height}px`;
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
      const x = Math.max(0, tile.offsetLeft + step[0]);
      const y = Math.max(0, tile.offsetTop + step[1]);
      placeOnDesktop(box, ctx, child.uuid, x, y).then((moved) => {
        if (!moved) return;
        tile.style.left = `${x}px`;
        tile.style.top = `${y}px`;
        rect.x = x;
        rect.y = y;
      });
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
      const landed = await dropBox(event, ctx, box.uuid, { label: "the root now shows that box" });
      if (landed) ctx.reload();
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
    const canFile = filingTarget(box);

    const wrap = document.createElement("div");
    wrap.className = "children-view";
    wrap.dataset.layout = options.layout;
    canvas.append(wrap);

    const toolbar = document.createElement("div");
    toolbar.className = "children-toolbar box-config";
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
    // A box made here would have to be filed here, and a filtered folder refuses that — the same
    // refusal the drop gets. Offering the button and then refusing the save would be a lie with an
    // extra step, so the button stays home. The cabinet is not affected: it takes hand-filing.
    if (!canFile.ok) {
      add.title = canFile.reason || "this folder chooses its own members";
      add.disabled = true;
    }
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
      if (!canFile.ok) {
        // Say it once per pass over the sheet, not once per pixel: the pointer sends a hundred
        // dragover events a second and the sentence is the same the whole time it is hovering.
        if (!wrap.classList.contains("drop-refused") && event.dataTransfer) {
          event.dataTransfer.dropEffect = "none";
        }
        wrap.classList.remove("drop-target");
        wrap.classList.add("drop-refused");
        if (canFile.reason) ctx.status(canFile.reason, true);
        return; // not claimed: the pane behind it refuses too, so the drop goes nowhere
      }
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      wrap.classList.add("drop-target");
    });
    wrap.addEventListener("dragleave", () => wrap.classList.remove("drop-target", "drop-refused"));
    wrap.addEventListener("drop", async (event) => {
      wrap.classList.remove("drop-target", "drop-refused");
      if (!canFile.ok) return; // the server would say the same thing; say it before the round trip
      event.preventDefault();
      const landed = await dropBox(event, ctx, box.uuid, { label: `filed in ${box.name}` });
      if (landed) ctx.reload();
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

    // The members of a filtered folder are decided by tags, and a tag can move while this folder is
    // on screen: renamed in the tag dialog, or given to a box three panes away. The store re-cuts
    // the membership the moment the tag moves, so the only stale thing left is this list — and a
    // folder that goes on showing a box it stopped holding is the same confusion the drop refusal
    // above exists to prevent, arriving from the other direction. Ask again, quietly, in place.
    const asksForItself = (state.types.get(box.box_type) || {}).resolves_membership === true;
    const relist = () => {
      if (!asksForItself || !document.contains(wrap)) return; // replaced by a newer view
      api
        .children(box.uuid)
        .then((next) => {
          if (!document.contains(wrap)) return;
          children = next;
          draw();
        })
        .catch(() => {
          /* the next click will ask again; a folder that cannot be read says so when it is opened */
        });
    };
    document.addEventListener(RELIST_EVENT, relist);

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
      bar.className = "filter-bar box-config";
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

  // One text field holds the filter, because the filter is one expression (ADR 20) and the
  // page is an editor for it, not a second representation of it. The controls underneath write
  // into that field; they never hold a private copy of the filter, so there is no way for the
  // two halves to disagree about what the folder selects.
  const expression = document.createElement("input");
  expression.type = "text";
  expression.className = "filter-expression";
  expression.spellcheck = false;
  expression.setAttribute("aria-label", "Filter expression");
  expression.placeholder = 'type:note and tag:urgent and not name:"draft"';
  expression.value = storedExpression(info.filter);
  bar.append(expression);

  const complaint = document.createElement("span");
  complaint.className = "filter-broken";
  complaint.hidden = true;
  bar.append(complaint);

  const apply = document.createElement("button");
  apply.type = "button";
  apply.dataset.action = "apply-filter";
  apply.textContent = "Apply filter";
  apply.addEventListener("click", () => commit(expression.value.trim()));

  bar.append(apply);

  expression.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit(expression.value.trim());
    }
  });

  // A folder written before the grammar may filter on metadata, which an expression does not
  // carry. Saying so beats dropping a clause the person cannot see being dropped.
  if (info.filter.metadata && Object.keys(info.filter.metadata).length) {
    const note = document.createElement("span");
    note.className = "filter-lost muted";
    note.textContent = "this folder also filters on metadata; applying an expression replaces that";
    bar.append(note);
  }

  const rows = document.createElement("div");
  rows.className = "filter-rows";
  bar.append(rows);

  // The says line is the server's sentence about the current text, so it is redrawn from every
  // parse: a person reads what their folder would hold before they apply it.
  const paint = (data) => {
    says.textContent = data.usable ? `holds every box where ${data.describes}` : data.describes;
    says.dataset.usable = String(data.usable);
  };
  const drawRows = () => renderFilterRows(rows, expression, says, complaint, paint);
  expression.addEventListener("change", () => {
    // A change that reports the text the rows were already drawn from is not new information.
    // This matters because a click on a clause's own button blurs the field first, and that
    // blur used to start a second render of the text as it was *before* the click — which then
    // finished after it and painted the clause back.
    if (expression.value.trim() === (rows.dataset.drawn || "").trim()) return;
    drawRows();
  });
  await drawRows();

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
  bar.append(again);

  async function commit(text) {
    complaint.hidden = true;
    try {
      const answer = await api.setFilter(box.uuid, text);
      ctx.status(`filter set: ${answer.describes}`);
      ctx.reload();
    } catch (error) {
      // The reason belongs here, in the bar, next to the words that caused it — and the folder
      // must not be reloaded into a state the server refused.
      complaint.textContent = error.message;
      complaint.hidden = false;
      says.dataset.usable = "false";
    }
  }
}

/**
 * The stored filter as one expression, for the field.
 *
 * A filter written the old way (a mapping of clauses) is rendered into the new form rather than
 * hidden behind a "this folder is legacy" state, because the person has to be able to see and
 * edit what their folder actually does. `metadata` has no expression form, and the caller says
 * so when it is present.
 */
function storedExpression(filter) {
  if (typeof filter === "string") return filter;
  if (filter && typeof filter.expression === "string") return filter.expression;
  const parts = [];
  if (filter && filter.type) parts.push(`type:${quoteTerm(filter.type)}`);
  if (filter && filter.name) parts.push(`name:${quoteTerm(filter.name)}`);
  if (filter && filter.resource) parts.push(`resource:${quoteTerm(filter.resource)}`);
  return parts.join(" and ");
}

function quoteTerm(value) {
  const text = String(value);
  return /[\s()|&"']/.test(text) || text === "" ? `"${text}"` : text;
}

/**
 * The controls under a folder's expression: one row per clause, and the list writes the text.
 *
 * The rows are drawn from what the **server** parsed (ADR 20), not from a second parser here.
 * The page holding its own copy of the grammar would be two grammars — and the first symptom is
 * pretty controls that disagree with the stored filter about what the folder selects. So: the
 * field is the filter, the server reads it, this draws the reading, and every edit here produces
 * text for the field, which is read again the same way a typed filter is.
 */
let filterRowsSeq = 0;

async function renderFilterRows(host, expression, says, complaint, onParsed) {
  // Every edit of a clause re-reads the whole expression from the server, and two of those reads
  // can be in flight at once — typing fast, or a hand edit landing while a click is still being
  // drawn. Whoever started first must then be the one to throw away its answer, or an old parse
  // paints rows for a filter the field no longer holds. Same rule as pane generation, same
  // reason: an asynchronous page has to say which render it is still speaking for.
  const seq = ++filterRowsSeq;
  const stale = () => seq !== filterRowsSeq;
  const text = expression.value.trim();
  let data = null;
  try {
    data = await api.parseFilter(text);
  } catch (error) {
    if (!stale()) host.replaceChildren(noteRow(`filter could not be read: ${error.message}`));
    return;
  }
  if (stale()) return;
  onParsed(data);
  host.replaceChildren();

  if (!data.usable) {
    // Nothing is drawn under a filter that could not be read: the reason belongs in the row
    // above, next to the words that caused it, and a half-drawn list of rows would imply the
    // folder was understood.
    complaint.textContent = data.broken;
    complaint.hidden = false;
    return;
  }
  complaint.hidden = true;

  if (data.clauses === null || data.clauses === undefined) {
    // Nested groups have no row order that survives a reorder, so the field keeps them.
    host.append(noteRow("groups clauses with brackets, so it is edited in the field"));
    return;
  }

  const rows = data.clauses.map((clause) => ({ ...clause }));
  let join = data.join || "and";

  const rebuild = () => {
    expression.value = expressionFromRows(rows, join);
    render();
  };

  const render = async () => {
    if (stale()) return;
    host.dataset.drawn = expression.value.trim();
    host.replaceChildren();
    rows.forEach((row, index) => host.append(rowFor(row, index)));
    host.append(controlRow());
    await sync();
  };

  // Ask the server what the current text means, then redraw from that answer. Every edit —
  // here or in the field — ends up going through this, which is what keeps the two in step.
  const sync = async () => {
    let data2 = null;
    try {
      data2 = await api.parseFilter(expression.value.trim());
    } catch (error) {
      if (stale()) return;
      complaint.textContent = error.message;
      complaint.hidden = false;
      return;
    }
    if (stale()) return;
    if (data2.usable) {
      complaint.hidden = true;
    } else {
      complaint.textContent = data2.broken;
      complaint.hidden = false;
    }
    onParsed(data2);
  };

  const rowFor = (row, index) => {
    const line = document.createElement("div");
    line.className = "filter-row";

    const negate = select(
      "Negate",
      [
        { value: "", label: "with" },
        { value: "not", label: "without" },
      ],
      row.negate ? "not" : "",
    );
    const field = select(
      "Field",
      [
        { value: "type", label: "type" },
        { value: "name", label: "name" },
        { value: "tag", label: "tag" },
        { value: "content", label: "words" },
        { value: "resource", label: "resource" },
      ],
      row.field,
    );
    const mode = select(
      "Match",
      [
        { value: "text", label: "contains" },
        { value: "regex", label: "matches /regex/" },
      ],
      row.mode,
    );
    const wordsOnly = row.field === "tag" || row.field === "content";
    if (wordsOnly) {
      mode.value = "text";
      mode.disabled = true;
      mode.title = "tags and words are asked in words; a regular expression belongs on the field";
    }

    const value = document.createElement("input");
    value.type = "text";
    value.className = "filter-value";
    value.setAttribute("aria-label", "Value to match");
    value.value = row.value;
    if (row.field === "tag" && typeof ensureTagOptions === "function") {
      value.setAttribute("list", "tag-options");
      ensureTagOptions();
    }

    const move = (label, delta, at) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.action = "move-clause";
      button.textContent = label;
      button.setAttribute("aria-label", `${label} clause ${at + 1}`);
      // one rule per arrow, not one for both: with two clauses, a single "either end disables
      // both" test leaves nothing clickable and the contract to reorder clauses is a lie
      button.disabled = delta < 0 ? at === 0 : at === rows.length - 1;
      button.addEventListener("click", () => {
        const other = at + delta;
        const held = rows[at];
        rows[at] = rows[other];
        rows[other] = held;
        rebuild();
      });
      return button;
    };

    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.action = "remove-clause";
    remove.textContent = "×";
    remove.setAttribute("aria-label", "Remove this clause");
    remove.addEventListener("click", () => {
      rows.splice(index, 1);
      rebuild();
    });

    const changed = () => {
      row.negate = negate.value === "not";
      row.field = field.value;
      row.mode = mode.disabled ? "text" : mode.value;
      row.value = value.value.trim();
      rebuild();
    };
    [negate, field, mode].forEach((control) => control.addEventListener("change", changed));
    value.addEventListener("input", () => {
      row.value = value.value.trim();
    });
    value.addEventListener("change", changed);

    line.append(negate, field, mode, value, move("↑", -1, index), move("↓", 1, index), remove);
    return line;
  };

  const controlRow = () => {
    const line = document.createElement("div");
    line.className = "filter-row filter-row-controls";

    const joins = select(
      "Combine clauses with",
      [
        { value: "and", label: "match every clause (and)" },
        { value: "or", label: "match any clause (or)" },
      ],
      join,
    );
    joins.addEventListener("change", () => {
      join = joins.value;
      rebuild();
    });

    const add = document.createElement("button");
    add.type = "button";
    add.dataset.action = "add-clause";
    add.textContent = "Add clause";
    add.addEventListener("click", () => {
      // A new row starts empty and stays out of the expression until it has a value, so an
      // unfinished clause cannot quietly become "name contains nothing" and empty the folder.
      rows.push({ field: "name", value: "", mode: "text", negate: false });
      render();
    });

    line.append(joins, add);
    return line;
  };

  await render();
}

function noteRow(text) {
  const note = document.createElement("div");
  note.className = "filter-nested muted";
  note.textContent = text;
  return note;
}

function select(label, options, chosen) {
  const node = document.createElement("select");
  node.setAttribute("aria-label", label);
  options.forEach((option) => {
    const item = document.createElement("option");
    item.value = option.value;
    item.textContent = option.label;
    node.append(item);
  });
  if (chosen !== undefined && chosen !== null) node.value = chosen;
  return node;
}

/** The expression a list of rows stands for, in the one syntax the server reads. */
function expressionFromRows(rows, join) {
  return rows
    .filter((row) => row.value !== "")
    .map((row) => {
      const body = row.mode === "regex" ? `/${row.value}/` : quoteTerm(row.value);
      return `${row.negate ? "not " : ""}${row.field}:${body}`;
    })
    .join(` ${join} `);
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
  item.addEventListener("dragstart", (event) => dragBox(event, child.uuid, parent.uuid));
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

/* What a dragged file is carrying, and where it came from.
 *
 * A box and a file are different things to drop, and the difference has to travel with the
 * drag: `text/plain` holds a box uuid, and a view that files that uuid would happily file the
 * directory box itself, which is a box that was never being moved. So a file gets its own type,
 * its own path, and the name the box would sensibly take.
 */
const FILE_DRAG_TYPE = "application/x-boxganizer-file";

function fileDragPayload(box, entry) {
  return JSON.stringify({
    from: box.uuid,
    sub: entry.sub,
    path: entry.path || "",
    name: entry.name.replace(/\.[^.]+$/, ""),
  });
}

function readDraggedFile(event) {
  const transfer = event.dataTransfer;
  if (!transfer || !Array.from(transfer.types).includes(FILE_DRAG_TYPE)) return null;
  const raw = transfer.getData(FILE_DRAG_TYPE);
  if (!raw) return null;
  try {
    const payload = JSON.parse(raw);
    return payload && payload.path ? payload : null;
  } catch {
    return null;
  }
}

/**
 * Make a box out of a file that was dragged out of a directory box.
 *
 * The type is asked of the server, because that is where the inference lives: a second copy in
 * the page would disagree with `box add` about what a .md file is within a release. `parent` is
 * omitted when the drop lands on the library list rather than on a box, which leaves the new
 * box in the cabinet and unfiled — the same thing typing a name in the side pane would do.
 */
async function createBoxFromDraggedFile(payload, parent) {
  let type = "file";
  try {
    const suggested = await api.suggestType(payload.path);
    if (suggested && suggested.suggestion) type = suggested.suggestion.key || type;
  } catch (error) {
    // A suggestion is a convenience. The box still gets made, as a plain file.
  }
  const made = await api.create({
    name: payload.name || "untitled",
    box_type: type,
    resource_uri: payload.path,
    ...(parent ? { parent } : {}),
  });
  await refreshSide();
  status(`made a ${type} box from ${payload.name}`);
  return made;
}

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
    bar.className = "fs-bar box-config";
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
        sub: entry.sub, // the file to read, which is not a box and has no uuid of its own
        owner: box.uuid, // ... but this box is who is asked, because it is the confine
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
        if (entry.kind !== "dir" && entry.path) {
          // out of the folder and into a box, by dragging: the row already knows what type the
          // file would infer to, and the server is the one that will say so
          row.draggable = true;
          row.title = "Drag onto a box to make a box out of this file";
          row.addEventListener("dragstart", (event) => {
            event.dataTransfer.setData(FILE_DRAG_TYPE, fileDragPayload(box, entry));
            event.dataTransfer.effectAllowed = "copy";
          });
        }
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
    bar.className = "browser-bar box-config";
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
  refused: "cannot listen",
  connecting: "connecting",
  live: "listening",
  value: "listening",
  retrying: "retrying",
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

/**
 * The four pictures a numeric sensor may be drawn as, at the size the box is shown at.
 *
 * The server decides *where the value sits* (`numeric.position`, `numeric.colour`, and whether it
 * was clamped); this only paints that. Splitting it that way means the CLI, a grid cell and an
 * opened pane cannot each invent their own idea of what 8.4 on a 0-10 scale looks like — and the
 * clamping rule in particular has to be one rule: a bar drawn at 140% is a bar that left the pane.
 */
function sensorPicture(info, { cell = false } = {}) {
  const wrap = document.createElement("div");
  wrap.className = `sensor-picture sensor-${info.representation}`;
  wrap.dataset.position = String(info.position);
  wrap.dataset.colour = info.colour || "";
  wrap.dataset.clamped = info.clamped || "";
  if (cell) wrap.classList.add("sensor-cell");

  const fill = (style, cls) => {
    const bar = document.createElement("div");
    bar.className = `sensor-fill ${cls || ""}`.trim();
    Object.assign(bar.style, style);
    return bar;
  };

  const track = document.createElement("div");
  track.className = "sensor-track";

  if (info.representation === "gauge") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 56");
    svg.setAttribute("class", "sensor-gauge-svg");
    svg.innerHTML = gaugeMarkup(info);
    wrap.append(svg);
  } else if (info.representation === "vbar") {
    track.append(fill({ height: `${info.position * 100}%`, background: info.colour || "currentColor" }));
    wrap.append(track);
  } else {
    track.append(fill({ width: `${info.position * 100}%`, background: info.colour || "currentColor" }));
    wrap.append(track);
  }

  if (info.clamp_text) {
    const clamp = document.createElement("span");
    clamp.className = "sensor-clamp";
    clamp.dataset.clamped = info.clamped;
    clamp.textContent = info.clamp_text;
    clamp.title = `${info.value} is outside ${info.min} to ${info.max}; the bar stops at the end of the scale`;
    wrap.append(clamp);
  }
  return wrap;
}

/** A half-ring, the arc drawn in proportion, and a needle that points at the value. */
function gaugeMarkup(info) {
  const radius = 40;
  const circumference = Math.PI * radius;
  const drawn = circumference * info.position;
  const angle = 180 * info.position;
  return (
    `<path d="M 10 50 A ${radius} ${radius} 0 0 1 90 50" class="sensor-gauge-back"></path>` +
    `<path d="M 10 50 A ${radius} ${radius} 0 0 1 90 50" class="sensor-gauge-arc" ` +
    `stroke="${info.colour || "currentColor"}" stroke-dasharray="${drawn} ${circumference}"></path>` +
    `<line x1="50" y1="50" x2="50" y2="14" class="sensor-gauge-needle" ` +
    `transform="rotate(${-90 + angle} 50 50)"></line>` +
    `<circle cx="50" cy="50" r="3.5" class="sensor-gauge-pivot"></circle>`
  );
}

/**
 * The three templates a sensor box runs over a reading, where the owner writes them.
 *
 * It lives in the settings drawer beside every other box setting, and it saves the whole config
 * it can see rather than the one field being typed in: the three templates are one pipeline, and
 * a form that could save a display template against a half-typed extraction template would let a
 * person leave a box in a state it cannot render.
 */
function sensorTemplateEditor(box, ctx) {
  const editor = document.createElement("div");
  editor.className = "sensor-templates box-config";

  const fields = [
    ["extract_template", "The value in what arrived", "{{ data.pressure }}"],
    ["display_template", "How the value is worded", "{{ value }} bar"],
    ["style_template", "How the words are dressed", '<b style="color: {{ \'red\' if number > 1.3 else \'inherit\' }}">{{ display }}</b>'],
  ];

  const keep = document.createElement("p");
  keep.className = "sensor-templates-note muted";
  keep.textContent =
    "Jinja. `value`, `raw`, `data` (the payload, when it is JSON), `number`, plus each earlier "
    + "step: `extracted` and `display`. Styling may use formatting tags only.";
  editor.append(keep);

  const inputs = [];
  fields.forEach(([key, label, hint]) => {
    const line = document.createElement("label");
    line.className = "sensor-template";
    const title = document.createElement("span");
    title.textContent = label;
    const area = document.createElement("textarea");
    area.rows = 2;
    area.spellcheck = false;
    area.placeholder = hint;
    area.name = `sensor:${key}`;
    area.value = (box.config && box.config[key]) || "";
    line.append(title, area);
    editor.append(line);
    inputs.push([key, area]);
  });

  const save = document.createElement("button");
  save.type = "button";
  save.dataset.action = "save-sensor-templates";
  save.textContent = "Save templates";
  save.addEventListener("click", async () => {
    const config = { ...(box.config || {}) };
    inputs.forEach(([key, area]) => {
      const text = area.value.trim();
      if (text) config[key] = text;
      else delete config[key];
    });
    try {
      await api.updateBox(box.uuid, { config });
      ctx.status("templates saved");
      ctx.reload();
    } catch (error) {
      // The server checks the templates before it stores them, so a Jinja typo is answered here,
      // in the field that holds it, rather than by a box that apologises later.
      ctx.status(`templates refused: ${error.message}`, true);
    }
  });
  editor.append(save);
  return editor;
}


/**
 * The scale: min, max, one of four pictures, and the colours the range is split into.
 *
 * The colour stops are saved with the range they cover, and the server refuses the pair when they
 * do not meet: intervals that stop short of `max` leave a reading with no colour at all, and the
 * half of a gauge that never changes colour is not something a person watching it can see.
 * The refusal comes back as the sentence the check produced — naming the value that would fall
 * through — which is why there is no duplicate validation here.
 */
function sensorScaleEditor(box, ctx) {
  const editor = document.createElement("div");
  editor.className = "sensor-scale box-config";
  const numeric = { ...((box.config && box.config.numeric) || {}) };
  const rows = Array.isArray(numeric.intervals) ? numeric.intervals.map((row) => ({ ...row })) : [];

  const title = document.createElement("p");
  title.className = "sensor-section";
  title.textContent = "Numeric scale";
  editor.append(title);

  const min = numberField("Minimum", numeric.min, "0");
  const max = numberField("Maximum", numeric.max, "10");
  const shape = document.createElement("select");
  shape.setAttribute("aria-label", "Visual representation");
  [
    ["hbar", "Horizontal bar"],
    ["vbar", "Vertical bar"],
    ["gauge", "Gauge"],
    ["progress", "Progression bar"],
  ].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    shape.append(option);
  });
  shape.value = numeric.representation || "hbar";

  const scaleRow = document.createElement("div");
  scaleRow.className = "sensor-scale-row";
  scaleRow.append(min.wrap, max.wrap, shape);
  editor.append(scaleRow);

  const intervals = document.createElement("div");
  intervals.className = "sensor-intervals";

  const drawIntervals = () => {
    intervals.replaceChildren();
    rows.forEach((row, index) => {
      const line = document.createElement("div");
      line.className = "sensor-interval";
      const upto = numberField(`Colour ${index + 1} ends at`, row.upto, "5");
      upto.input.addEventListener("change", () => {
        row.upto = upto.input.value === "" ? "" : Number(upto.input.value);
      });
      const colour = colourChooser(row.colour || "#7f8c8d", (picked) => {
        row.colour = picked;
      });
      const label = document.createElement("input");
      label.type = "text";
      label.className = "sensor-interval-label";
      label.placeholder = "label (optional)";
      label.setAttribute("aria-label", `Label for colour ${index + 1}`);
      label.value = row.label || "";
      label.addEventListener("change", () => {
        row.label = label.value.trim();
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.action = "remove-interval";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove colour ${index + 1}`);
      remove.addEventListener("click", () => {
        rows.splice(index, 1);
        drawIntervals();
      });
      line.append(upto.wrap, colour, label, remove);
      intervals.append(line);
    });
    const add = document.createElement("button");
    add.type = "button";
    add.dataset.action = "add-interval";
    add.textContent = "Add colour";
    add.addEventListener("click", () => {
      rows.push({ upto: "", colour: "#7f8c8d" });
      drawIntervals();
    });
    intervals.append(add);
  };
  drawIntervals();
  editor.append(intervals);

  const colours = textColourEditor(box, editor);

  const save = document.createElement("button");
  save.type = "button";
  save.dataset.action = "save-sensor-scale";
  save.textContent = "Save scale and colours";
  save.addEventListener("click", async () => {
    const config = { ...(box.config || {}) };
    const scale = {
      min: min.input.value === "" ? 0 : Number(min.input.value),
      max: max.input.value === "" ? 0 : Number(max.input.value),
      representation: shape.value,
      intervals: rows
        .filter((row) => row.upto !== "" && row.upto !== null && row.upto !== undefined)
        .map((row) => ({ upto: Number(row.upto), colour: row.colour, ...(row.label ? { label: row.label } : {}) })),
    };
    if (min.input.value === "" && max.input.value === "" && rows.length === 0) delete config.numeric;
    else config.numeric = scale;
    const text = colours.read();
    if (text) config.text_colours = text;
    else delete config.text_colours;
    try {
      await api.updateBox(box.uuid, { config });
      ctx.status("scale saved");
      ctx.reload();
    } catch (error) {
      // The server owns the coverage rule, so its sentence — "the colours stop at 6 but the range
      // runs to 10 — a reading of 8 would have no colour" — is what appears, unchanged.
      ctx.status(`scale refused: ${error.message}`, true);
    }
  });
  editor.append(save);
  return editor;
}

/** The textual alternative: patterns, each with a text colour and a background. */
function textColourEditor(box, host) {
  const stored = { ...((box.config && box.config.text_colours) || {}) };
  const rules = Array.isArray(stored.rules) ? stored.rules.map((rule) => ({ ...rule })) : [];
  const fallback = { colour: (stored.default && stored.default.colour) || "", background: (stored.default && stored.default.background) || "" };

  const title = document.createElement("p");
  title.className = "sensor-section";
  title.textContent = "Text colours (first match wins)";
  host.append(title);

  const list = document.createElement("div");
  list.className = "sensor-text-rules";

  const draw = () => {
    list.replaceChildren();
    rules.forEach((rule, index) => {
      const line = document.createElement("div");
      line.className = "sensor-text-rule";
      const pattern = document.createElement("input");
      pattern.type = "text";
      pattern.placeholder = "regex, e.g. ^open";
      pattern.setAttribute("aria-label", `Pattern ${index + 1}`);
      pattern.value = rule.pattern || "";
      pattern.addEventListener("change", () => {
        rule.pattern = pattern.value.trim();
      });
      line.append(
        pattern,
        colourChooser(rule.colour || "#111111", (picked) => {
          rule.colour = picked;
        }, "text"),
        colourChooser(rule.background || "#ffffff", (picked) => {
          rule.background = picked;
        }, "background"),
      );
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.action = "remove-text-rule";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove pattern ${index + 1}`);
      remove.addEventListener("click", () => {
        rules.splice(index, 1);
        draw();
      });
      line.append(remove);
      list.append(line);
    });
    const add = document.createElement("button");
    add.type = "button";
    add.dataset.action = "add-text-rule";
    add.textContent = "Add pattern";
    add.addEventListener("click", () => {
      rules.push({ pattern: "", colour: "#111111" });
      draw();
    });
    list.append(add);
  };
  draw();
  host.append(list);

  const defaultLine = document.createElement("div");
  defaultLine.className = "sensor-text-rule sensor-text-default";
  const word = document.createElement("span");
  word.textContent = "otherwise";
  defaultLine.append(
    word,
    colourChooser(fallback.colour || "#111111", (picked) => {
      fallback.colour = picked;
    }, "text"),
    colourChooser(fallback.background || "#ffffff", (picked) => {
      fallback.background = picked;
    }, "background"),
  );
  host.append(defaultLine);

  return {
    read: () => {
      const kept = rules.filter((rule) => rule.pattern);
      const hasDefault = fallback.colour || fallback.background;
      if (!kept.length && !hasDefault) return null;
      return {
        ...(kept.length ? { rules: kept } : {}),
        ...(hasDefault ? { default: { ...fallback } } : {}),
      };
    },
  };
}

/**
 * A colour: the sixteen presets, and the picker for the one that is not among them.
 *
 * The presets come from the server (`/api/sensors/palette`), because the list is also what
 * `check_numeric` will accept — a swatch the store would refuse is a control that lies.
 */
function colourChooser(value, onPick, role = "colour") {
  const holder = document.createElement("span");
  holder.className = "sensor-colour";
  holder.setAttribute("role", "group");
  holder.setAttribute("aria-label", `${role} colour`);

  const presets = (state.sensorPalette && state.sensorPalette.palette) || [];
  let current = value || "";

  const swatches = document.createElement("span");
  swatches.className = "sensor-swatches";

  const picked = document.createElement("input");
  picked.type = "color";
  picked.className = "sensor-picker";
  picked.setAttribute("aria-label", `${role} picker`);

  const named = document.createElement("input");
  named.type = "text";
  named.className = "sensor-colour-name";
  named.setAttribute("aria-label", `${role} as a name or hex value`);
  named.placeholder = "#rrggbb or name";

  // One function is the whole state of this control: the swatch row, the picker, the text field
  // and the saved value are the same colour or they are not one control. A draft of this kept a
  // `sync()` that wrote the *original* value back, so every pick was undone the moment it was
  // made — the scale saved grey for all three stops and looked, until it was measured, fine.
  const choose = (next) => {
    current = (next || "").trim();
    named.value = current;
    if (/^#[0-9a-fA-F]{6}$/.test(current)) picked.value = current;
    onPick(current);
  };

  presets.forEach((entry) => {
    const swatch = document.createElement("button");
    swatch.type = "button";
    swatch.className = "sensor-swatch";
    swatch.dataset.action = "pick-colour";
    swatch.style.background = entry.colour;
    swatch.title = `${entry.name} ${entry.colour}`;
    swatch.setAttribute("aria-label", `${entry.name} ${entry.colour}`);
    swatch.addEventListener("click", () => choose(entry.colour));
    swatches.append(swatch);
  });

  picked.addEventListener("input", () => choose(picked.value));
  named.addEventListener("change", () => choose(named.value));

  if (/^#[0-9a-fA-F]{6}$/.test(current)) picked.value = current;
  named.value = current;

  holder.append(swatches, picked, named);
  return holder;
}
function numberField(label, value, placeholder) {
  const wrap = document.createElement("label");
  wrap.className = "sensor-number";
  const text = document.createElement("span");
  text.textContent = label;
  const input = document.createElement("input");
  input.type = "number";
  input.step = "any";
  input.placeholder = placeholder;
  input.setAttribute("aria-label", label);
  if (value !== undefined && value !== null) input.value = String(value);
  wrap.append(text, input);
  return { wrap, input };
}

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
    head.className = "mqtt-head box-config";
    head.append(chip, topic, address);

    const value = document.createElement("span");
    value.className = "mqtt-value";
    value.textContent = "\u2014";
    // A template that fails is answered beside the reading, not instead of it: a sensor whose
    // number vanished looks like a sensor that heard nothing, and the two are very different
    // problems to be sitting in front of.
    const picture = document.createElement("div");
    picture.className = "sensor-slot";
    picture.hidden = true;
    const problem = document.createElement("div");
    problem.className = "sensor-problem";
    problem.hidden = true;
    problem.setAttribute("role", "status");
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

    wrap.append(
      head,
      readout,
      picture,
      problem,
      detail,
      sensorTemplateEditor(box, ctx),
      sensorScaleEditor(box, ctx),
      log,
    );
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

    function show(reading, { newest = false, shown = null } = {}) {
      const binary = reading.encoding && reading.encoding !== "utf-8";
      if (shown && shown.styled) {
        // The one place this page puts a box's own text in as HTML. `sensors.sanitise` rebuilt it
        // from an allow-list of formatting tags and style properties before it left the server, so
        // what arrives here cannot script, navigate, load, or re-layout anything.
        value.innerHTML = shown.styled;
      } else {
        const words = shown ? shown.display : reading.value;
        value.textContent = words === "" ? "(empty message)" : words;
      }
      value.dataset.encoding = reading.encoding || "utf-8";
      // The note explains *this* message, so a text message after a binary one
      // must not be left wearing the binary one's tooltip.
      const notes = [];
      if (binary) notes.push(`the broker sent bytes that are not text, shown as ${reading.encoding}`);
      if (shown && shown.display !== reading.value) notes.push(`the broker sent: ${reading.value}`);
      value.title = notes.join(" — ");
      problem.hidden = !(shown && shown.problem);
      if (shown && shown.problem) problem.textContent = shown.problem;
      // The picture sits beside the words, not instead of them: `8.4 bar` has to stay readable,
      // and the bar is what makes it obvious at a glance that 8.4 on a 0-10 scale is nearly full.
      picture.replaceChildren(
        shown && shown.numeric ? sensorPicture(shown.numeric, { cell: !!opts.cell }) : null,
      );
      picture.hidden = !(shown && shown.numeric);
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
      if (shown && shown.display !== reading.value) text.title = shown.display;
      line.append(clock, text);
      log.prepend(line);
      while (log.children.length > MQTT_HISTORY) log.lastElementChild.remove();
    }

    function apply(next, reading, note, shown = null) {
      chip.dataset.state = next;
      chip.textContent = MQTT_STATES[next] || next;
      detail.textContent = note || "";
      if (reading) show(reading, { newest: true, shown });
    }

    if (info.reading) show(info.reading, { shown: info.shown });
    if (opts.cell) {
      // A cell never subscribes, so it must not claim to be connecting. It shows
      // what the server last heard — and says so — or admits it has nothing yet.
      chip.dataset.state = info.reading ? "cached" : "idle";
      chip.textContent = info.reading ? "last value" : "no value yet";
      if (info.reading) show(info.reading, { shown: info.shown });
      detail.textContent = info.reading ? "" : `open the box to listen for ${target.topic}`;
      return;
    }

    apply("connecting", null, `listening for ${target.topic}`);

    const source = new EventSource(api.mqttStreamUrl(box.uuid));
    holdLive(() => source.close());

    // The last thing the server actually said. EventSource reports a broken connection with
    // an `error` event that carries nothing — no status, no reason, by design — so if the
    // page answers that with its own generic line, it overwrites the specific sentence that
    // arrived a moment ago. This was the whole of a bug report: a box that could not start at
    // all spent its time saying "the stream dropped" about a missing package.
    let lastWords = "";
    let refused = false;

    const accept = (event) => {
      if (!stillOnScreen(token)) {
        source.close();
        return;
      }
      // A dropped connection is an `error` event too, and it carries no body: the
      // browser is already going back to the broker, so that is a retry, not a refusal.
      if (typeof event.data !== "string") {
        if (refused) return; // nothing to add to a refusal, and nothing to retry
        apply("retrying", null, lastWords || "the stream dropped; going back to the broker");
        return;
      }
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        apply("refused", null, "the stream sent something that is not a reading");
        return;
      }
      const state = payload.state || event.type;
      if (state === "refused") {
        // A box that cannot be read will not be helped by waiting, and EventSource retries
        // forever: hang up, and leave the server's own sentence on the screen.
        refused = true;
        source.close();
        apply("refused", null, payload.detail || "the box was refused");
        return;
      }
      lastWords = payload.detail || "";
      apply(state, payload.reading || null, lastWords, payload.shown || null);
    };

    ["connecting", "live", "value", "retrying", "refused"].forEach((name) => {
      source.addEventListener(name, accept);
    });
  },
};

registerView("tiling-viewport", viewportView);
registerView("windowed-viewport", viewportView);
registerView("mqtt", mqttView);

/* -- markdown ----------------------------------------------------------------
 *
 * A .md file is words with a shape, and the shape is the reason anyone writes it: a
 * heading is a heading, a table lines up, `code` looks like code. So the file is
 * rendered rather than dumped — which is what its type has always claimed to do.
 *
 * The HTML comes from the server (`boxganizer.markdown`), because that is where the
 * parser that decides what is safe to show lives; a second implementation in the page
 * would be a second set of rules, quietly disagreeing with the first within a release.
 * Raw HTML in the file never becomes HTML on the screen, and a link whose address would
 * run something arrives with the address gone.
 *
 * The plain text is one click away and came in the same response, because "rendered"
 * that quietly differs from the file is worse than no rendering, and checking what a
 * note actually says should not cost a round trip.
 */

const MARKDOWN_SECTIONS = {rendered: "Rendered", raw: "Plain text"};

async function markdownView(canvas, box, ctx, opts = {}) {
  const token = paneGeneration;
  const options = textOptions(box);
  const section = (box.config || {}).markdown || {};
  let mode = section.mode === "raw" ? "raw" : "rendered";

  const stage = document.createElement("div");
  stage.className = "markdown-view";
  stage.dataset.mode = mode;
  stage.style.setProperty("--text-size", `${options.font_size}px`);

  const bar = document.createElement("div");
  bar.className = "markdown-bar box-config";
  Object.entries(MARKDOWN_SECTIONS).forEach(([key, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.dataset.mode = key;
    button.setAttribute("aria-pressed", String(key === mode));
    button.addEventListener("click", async () => {
      if (key === mode) return;
      mode = key;
      stage.dataset.mode = key;
      bar.querySelectorAll("button").forEach((b) => {
        b.setAttribute("aria-pressed", String(b.dataset.mode === key));
      });
      paint();
      // Remembered in the box, the way every other view choice is: a note read as plain
      // text at midnight should still be plain text in the morning. A preview has no box to
      // remember it in, and asking for one would answer `box_not_found`, so it switches for
      // this look only.
      if (!box.uuid) return;
      try {
        await api.setConfig(box.uuid, {
          ...(box.config || {}),
          markdown: {...(section || {}), mode: key},
        });
        box.config = {...(box.config || {}), markdown: {...(section || {}), mode: key}};
      } catch {
        /* the view switched; the memory is a courtesy, not the answer */
      }
    });
    bar.append(button);
  });

  const body = document.createElement("article");
  body.className = "markdown-body";
  const source = document.createElement("pre");
  source.className = "markdown-source text-body";
  stage.append(bar, body, source);
  canvas.append(stage);

  let answer = null;

  function paint() {
    if (!answer) return;
    if (mode === "raw") {
      source.textContent = answer.source;
      body.hidden = true;
      source.hidden = false;
    } else {
      // Only ever the server's HTML: see `boxganizer.markdown`. Nothing here is built
      // from the file's own text.
      body.innerHTML = answer.html;
      body.hidden = false;
      source.hidden = true;
    }
  }

  try {
    // A preview of a file inside a directory box: the file is not a box and has no uuid, so
    // the directory that holds it is asked (`opts.owner`) and the file named to it
    // (`opts.sub`). Asking for the preview itself as a box could only answer
    // `box_not_found`, which reads like a lost box and is really a file that never was one.
    answer = await api.markdown(opts.owner || box.uuid, opts.sub || "");
  } catch (error) {
    if (!stillOnScreen(token)) return;
    stage.replaceChildren(emptyMessage(`Cannot render ${box.resource_uri}: ${error.message}`));
    return;
  }
  if (!stillOnScreen(token)) return; // the pane moved on while the file was read

  if (answer.note) {
    stage.replaceChildren(emptyMessage(answer.note));
    return;
  }

  paint();

  stage.addEventListener("contextmenu", (event) => {
    if (event.target.closest("a")) return; // a link is a link; the menu is for the view
    event.preventDefault();
    ctx.showMenu(event.clientX, event.clientY, [
      {
        label: mode === "rendered" ? "Show plain text instead" : "Render the markdown",
        run: () => bar.querySelector(`button[data-mode="${mode === "rendered" ? "raw" : "rendered"}"]`).click(),
      },
      { separator: true },
      {
        label: `${options.wrap ? "✕" : "✓"} Wrap lines`,
        run: () => ctx.saveSection("text", { wrap: !options.wrap }),
      },
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
}

registerView("markdown", {
  async render(canvas, box, ctx, mode, opts = {}) {
    await markdownView(canvas, box, ctx, opts);
  },
});

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
    tile.addEventListener("dragstart", (event) => dragBox(event, row.node.uuid, map.uuid));
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
      if (event.shiftKey) {
        const from = event.dataTransfer.getData("application/x-boxganizer-from");
        if (from && from !== map.uuid) await api.link(map.uuid, dragged, from);
      }
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
    const from = event.dataTransfer.getData("application/x-boxganizer-from");
    const move = event.shiftKey && from && from !== map.uuid;
    await applied(
      api.link(map.uuid, dragged, move ? from : null),
      move ? "moved here, off the box it came from" : "filed as a branch of the centre"
    );
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
  bar.className = "mindmap-toolbar box-config";
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

// -- VNC screens ------------------------------------------------------------
//
// The page never speaks VNC. It cannot: RFB is a TCP conversation, and a browser has no
// TCP. So this machine opens the connection (`boxganizer.vnc`), decodes the framebuffer,
// and sends the page a picture when the far screen changes — while pointer moves and
// keystrokes come back as small POSTs that this module is responsible for making.
//
// Three things in here are worth the comments they carry: the coordinate mapping, because
// the canvas is scaled to fit the pane and a click has to land where it looked like it
// landed; the button mask, because RFB carries buttons with every position rather than as
// presses; and the focus rule on the keyboard, because a page that swallows Ctrl+R because
// a VNC pane is open is a page people close.

const VNC_BUTTONS = { 0: 1, 1: 2, 2: 4 }; // left, middle, right in RFB's order

async function vncView(canvas, box, ctx, opts = {}) {
  const token = paneGeneration;
  let status;
  try {
    status = await api.vnc(box.uuid);
  } catch (error) {
    canvas.append(emptyMessage(`Cannot read the VNC box: ${error.message}`));
    return;
  }
  if (!stillOnScreen(token)) return; // the pane moved on while the address was probed

  const wrap = document.createElement("div");
  wrap.className = "vnc-view";

  const head = document.createElement("div");
  head.className = "vnc-head";
  const address = document.createElement("strong");
  address.textContent = status.destination ? status.destination.url : "no address";
  head.append(address);
  const line = document.createElement("span");
  line.className = "muted vnc-line";
  head.append(line);
  wrap.append(head);

  if (status.screen) {
    const found = status.screen;
    // A probe that stopped to ask for a password never reached ServerInit, so there is no
    // size to print yet. "0×0" would be a number nobody believes, printed as a fact.
    const size = found.width && found.height ? ` · ${found.width}×${found.height}` : "";
    line.textContent = `${found.server_name || "a VNC server"} · RFB ${found.version}${size}`;
  } else {
    line.textContent = status.error || "nothing answered";
    wrap.classList.add("is-out");
  }

  if (status.destination) {
    const controls = document.createElement("div");
    controls.className = "vnc-controls box-config";
    const play = document.createElement("button");
    play.type = "button";
    play.className = "vnc-play";
    play.textContent = "Show the screen";
    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "vnc-stop";
    stop.textContent = "Stop";
    stop.hidden = true;
    controls.append(play, stop);

    // Shown when the screen asks for one, and only then: a password field that is always
    // there is a field people type anything into, and this one has exactly one use.
    const secret = document.createElement("input");
    secret.type = "password";
    secret.className = "vnc-password";
    secret.placeholder = "server password";
    secret.autocomplete = "off";
    secret.setAttribute("aria-label", "the password this screen asks for");
    secret.hidden = true;
    controls.append(secret);
    const copy = document.createElement("code");
    copy.className = "vnc-address";
    copy.textContent = status.destination.url;
    controls.append(copy);
    wrap.append(controls);

    const stage = document.createElement("div");
    stage.className = "vnc-stage";
    stage.tabIndex = 0;
    const hint = document.createElement("p");
    hint.className = "muted vnc-hint";
    hint.textContent = status.reachable
      ? "Nothing is drawn until you ask to see it. The machine sends a frame only when its screen changes."
      : "The machine did not answer, so there is nothing to draw.";
    stage.append(hint);
    wrap.append(stage);

    const screen = document.createElement("canvas");
    screen.className = "vnc-screen";
    screen.hidden = true;
    stage.append(screen); // it starts hidden, but it has to be in the page to be in it

    let stream = null;
    let session = null;
    let size = { width: 0, height: 0 };
    let buttons = 0;
    const queue = [];
    let sending = false;

    // Input goes back one message at a time, and while one is in flight the newest pointer
    // POSITION wins — replaying a hand the person stopped moving would drag a window twice.
    // A button change is never merged away: the press and the release are the click, and
    // dropping one of them leaves a window being dragged by a button nobody is holding.
    // So moves collapse into each other, and everything else queues in order.
    const VNC_QUEUE_LIMIT = 8;

    function send(payload) {
      if (!session) return;
      if (sending) {
        const last = queue[queue.length - 1];
        const positionOnly =
          payload.kind === "pointer" && last && last.kind === "pointer" && last.button_mask === payload.button_mask;
        if (positionOnly) queue[queue.length - 1] = payload;
        else if (queue.length < VNC_QUEUE_LIMIT) queue.push(payload);
        return; // the in-flight message's `finally` will take the next one
      }
      sending = true;
      api
        .vncInput(box.uuid, { ...payload, session })
        .catch((error) => {
          if (!/410|409/.test(error.message)) return; // the session ended; said so already
          stopEverything("the session ended");
        })
        .finally(() => {
          sending = false;
          const next = queue.shift();
          if (next) send(next);
        });
    }

    function pointAt(event) {
      // The canvas is drawn to fit the pane, so the pointer's position on the screen and
      // its position in the remote framebuffer are different numbers. Both are needed.
      const rect = screen.getBoundingClientRect();
      return {
        x: Math.round(((event.clientX - rect.left) / rect.width) * size.width),
        y: Math.round(((event.clientY - rect.top) / rect.height) * size.height),
      };
    }

    screen.addEventListener("pointermove", (event) => {
      const at = pointAt(event);
      send({ kind: "pointer", x: at.x, y: at.y, button_mask: buttons });
    });
    screen.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      buttons |= VNC_BUTTONS[event.button] ?? 1;
      const at = pointAt(event);
      send({ kind: "pointer", x: at.x, y: at.y, button_mask: buttons });
      stage.focus();
    });
    const release = (event) => {
      buttons &= ~(VNC_BUTTONS[event.button] ?? 1);
      const at = pointAt(event);
      send({ kind: "pointer", x: at.x, y: at.y, button_mask: buttons });
    };
    screen.addEventListener("pointerup", release);
    screen.addEventListener("pointercancel", release);
    screen.addEventListener("contextmenu", (event) => event.preventDefault());

    // Keys belong to the pane that has focus, and only to it. Combos with Ctrl or Meta are
    // left alone: the browser's own shortcuts outrank a remote screen, and a page that ate
    // reload would be a page nobody leaves open.
    stage.addEventListener("keydown", (event) => {
      if (event.ctrlKey || event.metaKey || !session) return;
      if (event.key.length > 1 && !event.key.startsWith("F") && event.key !== "Tab") return;
      event.preventDefault();
      send({ kind: "key", key: event.key, down: true });
    });
    stage.addEventListener("keyup", (event) => {
      if (event.ctrlKey || event.metaKey || !session) return;
      event.preventDefault();
      send({ kind: "key", key: event.key, down: false });
    });

    function stopEverything(words) {
      if (stream) {
        stream.close();
        stream = null;
      }
      if (session) api.vncClose(box.uuid, session).catch(() => {});
      session = null;
      play.hidden = false;
      stop.hidden = true;
      screen.hidden = true;
      hint.hidden = false;
      hint.textContent = words;
    }

    async function connect(password) {
      play.disabled = true;
      let opened;
      try {
        opened = await api.vncOpen(box.uuid, password);
      } catch (error) {
        line.textContent = error.message;
        play.disabled = false;
        return;
      }
      if (opened.password_needed) {
        // Not a failure to read and stop on: the answer is a field to type into.
        secret.hidden = false;
        secret.focus();
        line.textContent = opened.reason;
        play.disabled = false;
        play.textContent = "Try again";
        return;
      }
      session = opened.session;
      size = { width: opened.width, height: opened.height };
      screen.width = opened.width;
      screen.height = opened.height;
      secret.value = ""; // the bytes are in the server's session now, not in this input
      play.hidden = true;
      stop.hidden = false;
      hint.hidden = true;
      screen.hidden = false;
      stream = new EventSource(api.vncStreamUrl(box.uuid, opened.session));
      stream.addEventListener("info", (event) => {
        const info = JSON.parse(event.data);
        session = info.session;
        size = { width: info.width, height: info.height };
        screen.width = info.width;
        screen.height = info.height;
        line.textContent =
          `${info.name || "VNC"} · RFB ${info.version} · ${info.width}×${info.height} · live` +
          (info.used_password ? " · authenticated" : "");
        stage.focus();
      });
      stream.addEventListener("frame", (event) => {
        if (!stillOnScreen(token)) return;
        const image = new Image();
        image.onload = () => {
          const context = screen.getContext("2d");
          context.drawImage(image, 0, 0);
        };
        image.src = `data:image/png;base64,${event.data}`;
      });
      stream.addEventListener("error", (event) => {
        // EventSource's own `error` carries no message; a server-sent one does.
        const detail = event && event.data ? JSON.parse(event.data).message : "";
        if (detail) line.textContent = detail;
      });
      stream.addEventListener("end", () => stopEverything("the screen closed."));
    }

    stop.addEventListener("click", () => stopEverything("stopped."));
    play.addEventListener("click", () => connect(secret.value || undefined));
    secret.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      connect(secret.value || undefined);
    });

    if (!status.reachable) {
      play.disabled = true;
      play.title = status.error || "the machine did not answer";
    } else if (status.screen && status.screen.needs_password) {
      // The probe already learned this, so ask before the person has clicked anything.
      secret.hidden = false;
      line.textContent = `${line.textContent} · wants a password`;
    }
  }

  const note = document.createElement("p");
  note.className = "muted vnc-note";
  note.textContent =
    "The screen is drawn here, on this machine's connection: the page is shown frames and " +
    "sends back where the pointer is and what was typed. A server that wants a password is " +
    "asked for one when you connect, and the answer is used for that connection alone — it " +
    "is never written to the library, because the library is a file that gets copied. To " +
    "open a screen without being asked, point the box at a file that holds it: set the " +
    "metadata field " +
    `"${"password_file"}" to a path outside the library, readable only by you.`;
  wrap.append(note);

  canvas.append(wrap);
}

registerView("vnc", {
  async render(canvas, box, ctx, mode, opts = {}) {
    await vncView(canvas, box, ctx, opts);
  },
});

// -- notes ------------------------------------------------------------------
//
// A note is free text kept in a file of its own beside the database (ADR 14), read as
// plain words or as markdown. Everything here is a decision about which of those two the
// person is looking at, and about what the toolbox is allowed to do to the text.
//
// The toolbox writes markdown markers and nothing else. It puts `**` around a selection
// and moves on; it does not hold a document model, a delta format, or a second copy of
// the text in some rich structure of its own. That is what keeps the file the file: an
// editor that stores its state in the document is a document that only that editor can
// read, and the whole point of ADR 14 is that these files outlive the page.
//
// Rendering is the server's, through the same function a `.md` box is rendered by, so a
// note and a markdown file cannot disagree about what a heading is. The one thing the
// page adds afterwards is the meaning of a `boxganizer:` link: the server renders it as
// an ordinary anchor — inert, reaching nothing — and only here does it gain the power to
// open a box.

const NOTE_SAVE_DELAY = 700; // ms of quiet before the file is written

/** One button in the toolbox: either it wraps the selection or it prefixes the line. */
const NOTE_TOOLBOX = [
  { key: "bold", label: "B", title: "bold — wraps the words in **", wrap: ["**", "**"] },
  { key: "italic", label: "I", title: "italic — wraps the words in *", wrap: ["*", "*"] },
  { key: "strike", label: "S", title: "struck through — wraps the words in ~~", wrap: ["~~", "~~"] },
  { key: "h1", label: "H1", title: "a heading — starts the line with #", line: "# " },
  { key: "h2", label: "H2", title: "a smaller heading — starts the line with ##", line: "## " },
  { key: "list", label: "•", title: "a bullet — starts the line with -", line: "- " },
  { key: "numbered", label: "1.", title: "a numbered item — starts the line with 1.", line: "1. " },
  { key: "quote", label: "❝", title: "a quote — starts the line with >", line: "> " },
  { key: "code", label: "</>", title: "code — wraps the words in `", wrap: ["`", "`"] },
  { key: "link", label: "\u{1f517}", title: "a link — [words](address), then type the address", link: true },
];

async function noteView(canvas, box, ctx, opts = {}) {
  const token = paneGeneration;
  let note;
  try {
    note = await api.note(box.uuid);
  } catch (error) {
    canvas.append(emptyMessage(`Cannot read the note: ${error.message}`));
    return;
  }
  if (!stillOnScreen(token)) return; // the pane moved on while the file was read

  const section = (box.config || {}).note || {};
  let format = note.format || "markdown";
  // A markdown note opens as rendered words; a plain one has nothing to render, so it
  // opens in the editor and says so rather than showing an empty pane.
  let writing = format !== "markdown" || section.mode === "write";
  let source = note.source;
  let mentioned = note.mentioned || [];

  const wrap = document.createElement("div");
  wrap.className = "note-view";
  wrap.dataset.format = format;

  const bar = document.createElement("div");
  // Not `box-config`: the gear folds a box's *settings*, and this row is the note's toolbar —
  // format, the edit switch, the markdown toolbox. Folding it meant the way to start writing a
  // note lived behind an icon one click away from being unnoticed, which is a stranger claim about
  // a note than "choose what this box is" ever was. The cue on the sheet still says the state.
  bar.className = "note-bar";
  wrap.append(bar);

  const surface = document.createElement("div");
  surface.className = "note-surface";
  wrap.append(surface);

  const footer = document.createElement("div");
  footer.className = "note-foot muted";
  wrap.append(footer);

  // -- saving ----------------------------------------------------------------

  let pending = null;
  function say(words) {
    footer.textContent = words;
  }

  function scheduleSave() {
    say("writing soon…");
    if (pending) clearTimeout(pending);
    pending = setTimeout(save, NOTE_SAVE_DELAY);
  }

  async function save() {
    pending = null;
    if (!stillOnScreen(token)) return;
    try {
      const answer = await api.saveNote(box.uuid, source, format);
      if (!stillOnScreen(token)) return;
      const open = answer.unknown && answer.unknown.length;
      say(
        `saved · ${answer.characters} characters` +
          (open ? ` · ${answer.unknown.length} named box not in the library` : "")
      );
      if (answer.attached && answer.attached.length) refreshSide();
    } catch (error) {
      say(`not saved: ${error.message}`);
    }
  }

  // -- the two shapes of the pane --------------------------------------------

  function paint() {
    surface.textContent = "";
    bar.textContent = "";
    paintBar();
    if (writing) paintEditor();
    else paintRendered();
    paintCue();
  }

  /**
   * Whether this note can be typed into, said where it can be seen.
   *
   * The control that decides it lives in the settings drawer, and the drawer is closed by
   * default — so a person returning to a note could not tell whether clicking it would change
   * it, and found out by changing it. The cue therefore sits on the note itself, not beside the
   * control, and it names the way out in the same breath.
   */
  function paintCue() {
    // replaced, not appended: the cue lives outside the surface that `paint` clears, and a
    // second one per repaint is a stale label sitting on top of the true one
    wrap.querySelectorAll(".note-cue").forEach((old) => old.remove());
    if (format !== "markdown") return; // plain text is always the editor: nothing to declare
    const cue = document.createElement("span");
    cue.className = writing ? "note-cue editing" : "note-cue readonly";
    cue.textContent = writing ? "editing" : "read-only";
    cue.title = writing
      ? "typing changes this note — press Read when you are done"
      : "this note is being read, not edited — press the gear for Write";
    cue.setAttribute("aria-live", "polite");
    wrap.append(cue);
    wrap.dataset.mode = writing ? "write" : "read";
  }

  function toggle(button, key, label, on, onClick) {
    const item = document.createElement("button");
    item.type = "button";
    item.textContent = label;
    item.dataset.toggle = key;
    item.setAttribute("aria-pressed", String(on));
    item.addEventListener("click", onClick);
    button.append(item);
    return item;
  }

  function paintBar() {
    const group = document.createElement("div");
    group.className = "note-group";
    toggle(group, "format", "Markdown", format === "markdown", () => setFormat("markdown"));
    toggle(group, "format", "Plain text", format !== "markdown", () => setFormat("text"));
    bar.append(group);

    if (format === "markdown") {
      // One button, two states, because "Read" and "Write" side by side are one control that
      // has been drawn as two: exactly one of them can ever be the next thing you press, and a
      // pair of buttons where one is always already-pressed invites a click that does nothing.
      // The label is what the click will get you, which is the only reading that makes sense of
      // a button, and `aria-pressed` carries the state for anyone not reading the label.
      const shape = document.createElement("div");
      shape.className = "note-group";
      // A switch, not a button that changes name. The two-state button worked, but it read as an
      // action ("Write") rather than a state, so the note's condition had to be remembered from
      // the last click instead of seen. A switch shows the condition where the action used to be,
      // and the word beside it is the thing being switched — it does not move.
      // (Plain-text notes have no rendering to look at, so they are always editable and the
      // switch is not offered for them; that is `format !== "markdown"` above, unchanged here.)
      const mode = document.createElement("button");
      mode.type = "button";
      mode.className = "note-switch";
      mode.dataset.toggle = "mode";
      mode.setAttribute("role", "switch");
      mode.setAttribute("aria-checked", String(writing));
      mode.title = writing
        ? "editing this note — click to read it as it will be read"
        : "this note is read-only — click to edit its text";
      const word = document.createElement("span");
      word.className = "note-switch-label";
      word.textContent = "edit";
      const track = document.createElement("span");
      track.className = "note-switch-track";
      const knob = document.createElement("span");
      knob.className = "note-switch-knob";
      track.append(knob);
      mode.append(word, track);
      mode.addEventListener("click", () => setWriting(!writing));
      shape.append(mode);
      bar.append(shape);
    }

    if (writing && format === "markdown") {
      const tools = document.createElement("div");
      tools.className = "note-group note-toolbox";
      NOTE_TOOLBOX.forEach((tool) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `note-tool note-tool-${tool.key}`;
        button.textContent = tool.label;
        button.title = tool.title;
        button.addEventListener("mousedown", (event) => event.preventDefault()); // keep the selection
        button.addEventListener("click", () => applyTool(tool));
        tools.append(button);
      });

      // Naming a box inside the note is done by picking it, not by typing a uuid: the
      // uuid is the reference and no person should have to read one to write a sentence.
      const picker = document.createElement("select");
      picker.className = "note-box-picker";
      picker.title = "name a box inside the note — it is filed here as well";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "\u{1f4e6} name a box…";
      picker.append(placeholder);
      (state.boxes || []).forEach((other) => {
        if (other.uuid === box.uuid) return;
        const option = document.createElement("option");
        option.value = other.uuid;
        option.textContent = `${other.icon || "\u{1f4e6}"} ${other.name}`;
        picker.append(option);
      });
      picker.addEventListener("change", () => {
        const uuid = picker.value;
        if (!uuid) return;
        const other = (state.boxes || []).find((candidate) => candidate.uuid === uuid);
        insertBoxLink(other || { uuid, name: uuid, icon: "" });
        picker.value = "";
      });
      tools.append(picker);
      bar.append(tools);
    }

    const file = document.createElement("span");
    file.className = "muted note-file";
    file.textContent = format === "markdown" ? "markdown" : "plain text";
    bar.append(file);
  }

  async function setFormat(next) {
    if (next === format) return;
    format = next;
    wrap.dataset.format = format;
    if (format !== "markdown") writing = true; // plain text has no rendering to look at
    paint();
    await save();
  }

  async function setWriting(next) {
    if (next === writing) return;
    if (!next && pending) await save(); // do not lose keystrokes behind a re-render
    writing = next;
    paint();
    // the choice is the box's, kept where every other view choice is kept
    try {
      await api.setConfig(box.uuid, {
        ...(box.config || {}),
        note: { ...(section || {}), mode: next ? "write" : "read" },
      });
    } catch (error) {
      say(`could not remember the view: ${error.message}`);
    }
  }

  // -- reading ---------------------------------------------------------------

  async function paintRendered() {
    let answer;
    try {
      answer = await api.note(box.uuid);
    } catch (error) {
      surface.textContent = "";
      say(`cannot be read: ${error.message}`);
      return;
    }
    if (!stillOnScreen(token)) return;
    source = answer.source;
    surface.textContent = "";
    if (!source.trim()) {
      // A blank sheet next to a blank note reads like a failure to load. Say what it is.
      const empty = document.createElement("p");
      empty.className = "note-empty muted";
      empty.textContent = "Nothing written yet. Press Write and start.";
      surface.append(empty);
      say("empty");
      return;
    }
    const body = document.createElement("div");
    body.className = "markdown-body note-body";
    // the only innerHTML in this view, and the only source is the server's rendering of
    // the file — the same rules that keep a .md box honest apply to a note
    body.innerHTML = answer.html;
    surface.append(body);
    wireBoxLinks(body);
    say(`${answer.source.length} characters · rendered`);
  }

  function wireBoxLinks(root) {
    // An anchor on this project's scheme is inert until here: this is where it is given
    // the power to open a box, and a file that only mentions a uuid cannot navigate.
    root.querySelectorAll(`a[href^="${"boxganizer"}:`).forEach((anchorEl) => {
      const uuid = anchorEl.getAttribute("href").slice("boxganizer:".length);
      const known = mentioned.find((child) => child.uuid === uuid);
      anchorEl.classList.add("note-chip");
      if (known && known.gone) anchorEl.classList.add("is-gone");
      anchorEl.removeAttribute("href");
      anchorEl.title =
        known && known.gone
          ? `${uuid} is not in the library any more`
          : `open ${known ? known.name : uuid}`;
      anchorEl.addEventListener("click", (event) => {
        event.preventDefault();
        if (!(known && known.gone)) openBox(uuid);
      });
    });
  }

  // -- writing ---------------------------------------------------------------

  function paintEditor() {
    const editor = document.createElement("textarea");
    editor.className = "note-editor";
    editor.spellcheck = true;
    editor.placeholder =
      format === "markdown" ? "write it — **bold**, *italic*, a list, a box…" : "write it…";
    editor.value = source;
    surface.append(editor);
    editor.addEventListener("input", () => {
      source = editor.value;
      scheduleSave();
    });
    editor.addEventListener("blur", () => {
      if (pending) save();
    });
    // A box dragged out of the panel lands where the cursor is, or at the end when the
    // cursor is not in the note. Placing it at the drop point inside a textarea is not
    // something the browser will tell us; guessing at it would put words in a sentence
    // the person did not write them into.
    editor.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    editor.addEventListener("drop", (event) => {
      const uuid = event.dataTransfer.getData("text/plain");
      if (!uuid || uuid === box.uuid) return;
      event.preventDefault();
      const other = (state.boxes || []).find((candidate) => candidate.uuid === uuid);
      const caret = editor.selectionStart || source.length;
      source = `${source.slice(0, caret)} ${boxLink(other || { uuid, name: uuid, icon: "" })} ${source.slice(caret)}`;
      editor.value = source;
      paint();
      save();
    });
    editor.focus();
    editor.setSelectionRange(source.length, source.length);
    say(`${source.length} characters`);
  }

  function boxLink(other) {
    // The same shape `boxganizer.boxrefs` writes server-side: an ordinary markdown link
    // on a scheme that belongs to this program.
    const label = `${other.icon || "\u{1f4e6}"} ${other.name || other.uuid}`.trim();
    return `[${label}](boxganizer:${other.uuid})`;
  }

  function insertBoxLink(other) {
    const editor = surface.querySelector(".note-editor");
    if (!editor) return;
    const caret = editor.selectionStart || source.length;
    const before = source.slice(0, caret);
    const after = source.slice(caret);
    const join = before && !/\s$/.test(before) ? " " : "";
    source = `${before}${join}${boxLink(other)} ${after.replace(/^\s+/, "")}`;
    editor.value = source;
    editor.focus();
    scheduleSave();
  }

  function applyTool(tool) {
    const editor = surface.querySelector(".note-editor");
    if (!editor) return;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const selected = source.slice(start, end);
    if (tool.link) {
      // The address is left empty on purpose and the caret dropped inside it: a dialog
      // that asks for a URL first, before the words are even chosen, is the wrong order.
      const text = selected || "words";
      source = `${source.slice(0, start)}[${text}]()${source.slice(end)}`;
      editor.value = source;
      const at = start + text.length + 3;
      editor.focus();
      editor.setSelectionRange(at, at);
      scheduleSave();
      return;
    }
    if (tool.wrap) {
      const [open, close] = tool.wrap;
      source = `${source.slice(0, start)}${open}${selected || "words"}${close}${source.slice(end)}`;
      editor.value = source;
      const from = start + open.length;
      editor.focus();
      editor.setSelectionRange(from, from + (selected || "words").length);
      scheduleSave();
      return;
    }
    // A line prefix is applied to every line the selection touches, because a bullet that
    // lands on the first line of a two-line selection is a broken list.
    const from = source.lastIndexOf("\n", start - 1) + 1;
    let to = source.indexOf("\n", end);
    if (to === -1) to = source.length;
    const block = source
      .slice(from, to)
      .split("\n")
      .map((line) =>
        line.startsWith(tool.line) ? line.slice(tool.line.length) : `${tool.line}${line}`
      )
      .join("\n");
    source = `${source.slice(0, from)}${block}${source.slice(to)}`;
    editor.value = source;
    editor.focus();
    editor.setSelectionRange(from, from + block.length);
    scheduleSave();
  }

  paint();
  canvas.append(wrap);
}

registerView("note", {
  async render(canvas, box, ctx, mode, opts = {}) {
    await noteView(canvas, box, ctx, opts);
  },
});

// -- checklists ---------------------------------------------------------------
//
// The items live in a file beside the database — see `boxganizer.checklist` and
// `boxganizer.authored` — and the page holds a copy of that file as `items`.
//
// Every change sends the WHOLE list back, not a patch. That is not laziness: the file is
// the list, a text editor can be open on it at the same moment, and a set of granular
// edits (insert at 3, tick 1, move 2) would be a second order of events to reconcile with
// whoever changed the file meanwhile. Replacing it is the same operation an editor does
// when it saves, and it is idempotent.
//
// A child box is hung on an item by dropping it on that line. The drop writes a link into
// the line (`[📦 Name](boxganizer:uuid)`) and files the box here; taking the icon off
// removes only the link. Boxes filed here that are on no line are listed under the list
// rather than hidden — nothing a person filed in a box should become invisible because a
// file does not mention it.

const CHECKLIST_SAVE_DELAY = 700; // ms of quiet before the file is written

async function checklistView(canvas, box, ctx, opts = {}) {
  const token = paneGeneration;
  let list;
  try {
    list = await api.checklist(box.uuid);
  } catch (error) {
    canvas.append(emptyMessage(`Cannot read the checklist: ${error.message}`));
    return;
  }
  if (!stillOnScreen(token)) return; // the pane moved on while the file was read

  let items = list.items.map((item) => ({ ...item, refs: [...item.refs] }));
  let title = list.title || "";
  let savedAt = null;

  const wrap = document.createElement("div");
  wrap.className = "checklist-view";

  const head = document.createElement("div");
  head.className = "checklist-head";
  const tally = document.createElement("span");
  tally.className = "checklist-tally";
  const status = document.createElement("span");
  status.className = "checklist-status muted";
  head.append(tally, status);
  wrap.append(head);

  const titleInput = document.createElement("input");
  titleInput.type = "text";
  titleInput.className = "checklist-title";
  titleInput.placeholder = "what this list is called";
  titleInput.value = title;
  wrap.append(titleInput);

  const body = document.createElement("ul");
  body.className = "checklist-items";
  body.setAttribute("role", "list");
  wrap.append(body);

  const addForm = document.createElement("form");
  addForm.className = "checklist-add";
  const addInput = document.createElement("input");
  addInput.type = "text";
  addInput.placeholder = "add something to do, then Enter";
  addInput.className = "checklist-add-input";
  const addMore = document.createElement("button");
  addMore.type = "submit";
  addMore.className = "checklist-add-button";
  addMore.textContent = "add";
  addForm.append(addInput, addMore);
  wrap.append(addForm);

  const unattachedRow = document.createElement("div");
  unattachedRow.className = "checklist-filed";
  wrap.append(unattachedRow);

  // -- saving ----------------------------------------------------------------

  function shape(item) {
    return {
      text: item.text,
      checked: !!item.checked,
      depth: item.depth || 0,
      kind: item.kind || "box",
      refs: [...item.refs],
    };
  }

  function paintStatus(words) {
    status.textContent = words;
  }

  let pending = null;
  function scheduleSave() {
    paintStatus("changed…");
    if (pending) clearTimeout(pending);
    pending = setTimeout(save, CHECKLIST_SAVE_DELAY);
  }

  async function save() {
    pending = null;
    if (!stillOnScreen(token)) return;
    paintStatus("writing…");
    try {
      const answer = await api.saveChecklist(box.uuid, title, items.map(shape));
      if (!stillOnScreen(token)) return;
      savedAt = new Date();
      paintStatus("saved");
      tally.textContent = answer.summary || "";
      // A box dropped in from the panel is filed here by the save: the sidebar may well
      // have a different idea of what is inside, so it is re-read.
      if (answer.attached && answer.attached.length) refreshSide();
      // The line the file wrote is the truth now: an icon naming a box that is not filed
      // here would render as gone, and the unattached row has one fewer box in it.
      await reload();
    } catch (error) {
      paintStatus(`not saved: ${error.message}`);
    }
  }

  async function reload() {
    try {
      const fresh = await api.checklist(box.uuid);
      if (!stillOnScreen(token)) return;
      items = fresh.items.map((item) => ({ ...item, refs: [...item.refs] }));
      title = fresh.title || "";
      if (document.activeElement !== titleInput) titleInput.value = title;
      unattached = fresh.unattached;
      render();
    } catch (error) {
      paintStatus(`the file cannot be read: ${error.message}`);
    }
  }

  // -- rendering -------------------------------------------------------------

  let unattached = list.unattached || [];

  function paintTally() {
    let open = 0;
    let done = 0;
    for (const item of items) {
      if ((item.kind || "box") !== "box") continue;
      if (item.checked) done += 1;
      else open += 1;
    }
    if (open) tally.textContent = `${open} of ${open + done} still open`;
    else if (done) tally.textContent = `all ${done} done`;
    else tally.textContent = "nothing ticked on it yet";
  }

  function render() {
    body.textContent = "";
    items.forEach((item, index) => body.append(row(item, index)));
    if (!items.length) {
      const nothing = document.createElement("li");
      nothing.className = "checklist-empty muted";
      nothing.textContent = "Nothing on it. Type below, or drop a box onto a line.";
      body.append(nothing);
    }
    paintTally();
    renderUnattached();
  }

  function row(item, index) {
    const line = document.createElement("li");
    line.className = `checklist-item${item.checked ? " is-done" : ""}${(item.kind || "box") === "text" ? " is-prose" : ""}`;
    line.style.setProperty("--depth", String(item.depth || 0));
    line.draggable = true;

    if ((item.kind || "box") === "box") {
      const tick = document.createElement("input");
      tick.type = "checkbox";
      tick.className = "checklist-tick";
      tick.checked = !!item.checked;
      tick.setAttribute("aria-label", `done: ${item.text}`);
      tick.addEventListener("change", () => {
        item.checked = tick.checked;
        line.classList.toggle("is-done", item.checked);
        // the count at the top moves with the tick: a list that says "3 of 3 still open"
        // under a row you have just crossed off is a list nobody trusts
        paintTally();
        scheduleSave();
      });
      line.append(tick);
    }

    const words = document.createElement("div");
    words.className = "checklist-words";
    words.contentEditable = "plaintext-only";
    words.textContent = item.text;
    words.addEventListener("input", () => {
      item.text = words.textContent.replace(/\s+/g, " ").trim();
      scheduleSave();
    });
    words.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        words.blur();
        addInput.focus();
      } else if (event.key === "Escape") {
        words.textContent = item.text;
        words.blur();
      }
    });
    words.addEventListener("blur", () => {
      const text = words.textContent.replace(/\s+/g, " ").trim();
      if (text === item.text) return;
      item.text = text;
      save();
    });
    line.append(words);

    const icons = document.createElement("div");
    icons.className = "checklist-icons"; // the right end of the line, vertically centred
    (item.attached || []).forEach((child) => icons.append(chip(child, child.gone)));
    line.append(icons);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "checklist-remove";
    remove.textContent = "\u00d7";
    remove.title = "take this line off the list";
    remove.setAttribute("aria-label", `remove: ${item.text || "this line"}`);
    remove.addEventListener("click", () => {
      items.splice(index, 1);
      render();
      save();
    });
    line.append(remove);

    // a box from the panel, or from the row underneath, lands on this line
    line.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      line.classList.add("is-target");
    });
    line.addEventListener("dragleave", () => line.classList.remove("is-target"));
    line.addEventListener("drop", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      line.classList.remove("is-target");
      const uuid = event.dataTransfer.getData("text/plain");
      if (!uuid || uuid === box.uuid || item.refs.includes(uuid)) return;
      const from = event.dataTransfer.getData("application/x-boxganizer-from");
      // Shift while dropping onto a line means "move it here", and the save below only files it;
      // the unlink has to happen for the origin, which this box is not.
      if (event.shiftKey && from && from !== box.uuid) await api.link(box.uuid, uuid, from);
      const known = unattached.find((child) => child.uuid === uuid);
      item.refs.push(uuid);
      if (known) {
        (item.attached ||= []).push({ ...known, gone: false });
        unattached = unattached.filter((child) => child.uuid !== uuid);
      }
      render();
      save(); // the save is what files the box here as well
    });
    return line;
  }

  function chip(child, gone) {
    const tag = document.createElement("button");
    tag.type = "button";
    tag.className = `checklist-chip${gone ? " is-gone" : ""}`;
    tag.title = gone
      ? `${child.uuid} is not in the library any more`
      : `${child.name} (${child.box_type}) — open it`;
    tag.textContent = gone ? "\u2753 missing" : `${child.icon || "\u{1f4e6}"} ${child.name}`;
    tag.addEventListener("click", (event) => {
      event.preventDefault();
      if (!gone) openBox(child.uuid);
    });
    if (!gone) {
      tag.draggable = true;
      tag.addEventListener("dragstart", (event) => dragBox(event, child.uuid, box.uuid));
      // lifting an icon off a line takes the link away and leaves the box filed here
      const detach = document.createElement("span");
      detach.className = "checklist-detach";
      detach.textContent = "\u00d7";
      detach.title = "take this box off the line (it stays in the box)";
      detach.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        items.forEach((item) => {
          if (item.refs.includes(child.uuid)) {
            item.refs = item.refs.filter((uuid) => uuid !== child.uuid);
            item.attached = (item.attached || []).filter((one) => one.uuid !== child.uuid);
          }
        });
        unattached.push(child);
        render();
        save();
      });
      tag.append(detach);
    }
    return tag;
  }

  function renderUnattached() {
    unattachedRow.textContent = "";
    const rest = unattached.filter((child) => !items.some((item) => item.refs.includes(child.uuid)));
    if (!rest.length) return;
    const label = document.createElement("span");
    label.className = "muted";
    label.textContent = "filed here, on no line:";
    unattachedRow.append(label);
    rest.forEach((child) => unattachedRow.append(chip(child, false)));
    const hint = document.createElement("span");
    hint.className = "muted checklist-hint";
    hint.textContent = "drag one onto a line to hang it there";
    unattachedRow.append(hint);
  }

  addForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = addInput.value.trim();
    if (!text) return;
    items.push({ text, checked: false, depth: 0, kind: "box", refs: [], attached: [] });
    addInput.value = "";
    render();
    save();
  });

  titleInput.addEventListener("input", () => {
    title = titleInput.value;
    scheduleSave();
  });

  render();
  if (savedAt) paintStatus("saved");
  canvas.append(wrap);
  addInput.focus();
}

registerView("checklist", {
  async render(canvas, box, ctx, mode, opts = {}) {
    await checklistView(canvas, box, ctx, opts);
  },
});

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
  head.className = "ssh-head box-config";
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
