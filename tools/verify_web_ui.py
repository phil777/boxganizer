#!/usr/bin/env python3
"""Manual browser check for the Boxganizer web UI. Not part of `make test`.

One command, nothing else to set up:

    make uitest                                    # or:
    .venv/bin/python tools/verify_web_ui.py --self [path-to-chromium]

`--self` builds a throwaway library with the fixtures these checks need, serves it on a free
port, runs, then terminates its own server and deletes the directory. A chromium Playwright did
not install can be named as the argument after `--self` or in $BOXGANIZER_CHROME.

To run it against a server you started yourself, name the URL instead -- that is how a scratch
library is driven while a feature is being built:

    .venv/bin/python tools/verify_web_ui.py http://127.0.0.1:8300 [path-to-chromium]

The library it needs used to be a copy-paste recipe of nine commands in a second terminal, and
that is why this file went stale: untested setup steps rot quietly (the recipe here said
`box box add` long after the commands were flattened to `box add`), and nobody ran what was
tiresome to run. The checks also assert absolute state -- whether a text box wraps, which grid
cells are filled, where an icon sits to the pixel, which children are still unplaced -- and an
earlier run moves all of it; on a library this tool had already driven, the desktop chips it
drags were no longer in the strip, which was not a failed check but a 30-second locator timeout
that killed the run. It now un-configures the sections its own run writes, so a second run means
the same thing as the first. Verified: two `--self` runs in a row, RESULT: OK both.

It drives the real interface and prints what the page actually shows: the side
pane, the view each box type chooses for itself, image scaling, folder
navigation, grid resizing, swapping by drag and drop, the context menus, and the
desktop surface
— wallpaper, dropping a box where it was dropped, dragging an icon by the pixels
it was dragged, and the magnetic grid taking it to the nearest line. Exit code is
0 only if every check passed and the browser reported no console errors.

It is kept out of CI on purpose: a browser download in the test pipeline is a
dependency nobody asked for, and `make test` must stay fast and offline.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, condition: bool, detail: Any = "") -> None:
        print(f"  {'ok  ' if condition else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
        if not condition:
            self.failures.append(label)


def api(base: str, path: str, payload: dict | None = None) -> Any:
    request = urllib.request.Request(
        f"{base}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def api_put(base: str, path: str, payload: dict[str, object]) -> Any:
    """PUT with a JSON body, for writing a box's own config back."""
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def api_delete(base: str, path: str) -> None:
    """DELETE, ignoring a box that is already gone. For the fixtures a section makes for itself."""
    request = urllib.request.Request(f"{base}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
    except urllib.error.HTTPError:
        pass


def _run(base: str, executable: str | None) -> int:
    checks = Checks()
    errors: list[str] = []

    with sync_playwright() as drivers:
        chosen = executable or _discover_browser()
        browser = drivers.chromium.launch(
            executable_path=chosen, args=["--no-sandbox"] if chosen else []
        )
        page = browser.new_page(viewport={"width": 1300, "height": 820})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        # What the page asks for, read off the network. The suite used to grep app.js for the path
        # strings instead, which proved that somebody had typed them somewhere; this proves the
        # requests are actually issued, and would notice one quietly stopping.
        asked: list[str] = []
        page.on("request", lambda r: asked.append(r.url.split("/api/")[-1].split("?")[0]))

        page.goto(f"{base}/", wait_until="networkidle")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(400)

        print("shell")
        checks.check("side pane lists boxes", page.locator("#box-list li").count() > 0)
        checks.check(
            "root is pinned first", "root" in page.locator("#box-list li").first.inner_text()
        )
        checks.check(
            "a box is on screen",
            page.locator("#canvas .icon-view, #canvas .children-view").count() > 0,
        )

        print("the shell asks the server for what it draws")
        for endpoint in ("boxes", "types", "index/status", "tags"):
            checks.check(
                f"a load asks for /api/{endpoint}",
                any(a == endpoint or a.startswith(f"{endpoint}?") or a.startswith(f"{endpoint}/")
                    for a in asked),
                sorted(set(asked))[:6],
            )

        print("the root row offers no way to delete it")
        root_row = page.locator("#box-list li").first
        checks.check("the root is the first row", "root" in root_row.inner_text(),
                     root_row.inner_text()[:40])
        root_row.click(button="right")
        page.wait_for_timeout(400)
        root_menu = page.locator("#context-menu").inner_text()
        checks.check(
            "no delete entry is offered for the root",
            "Delete" not in root_menu,
            root_menu.replace("\n", " / ")[:120],
        )
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

        print("settings are folded until asked for, so ask once, here")
        # `#canvas:not(.config-open) .box-config` is height 0 and visibility hidden (app.css), which
        # is the design: a box's settings take no room until the gear is turned. Every later section
        # reaches for a control inside a `.box-config` row -- grid size steppers, the desktop's
        # magnet -- and clicking a hidden button is a 30-second timeout that reports nothing at all.
        page.locator("#toggle-config").click()
        page.wait_for_timeout(600)
        checks.check(
            "the gear opened the settings row",
            page.evaluate(
                "() => document.getElementById('canvas').classList.contains('config-open')"
            ),
        )
        # Whether a row is drawn is the box type's business -- the root has none to draw -- so this
        # asks nothing more of it here. The sections below each click a control that lives inside a
        # settings row, and a hidden control times out rather than failing, which is proof enough.

        print("folder navigation")
        board = page.locator("#box-list li", has_text="Board")
        if board.count():
            board.click()
            page.wait_for_timeout(700)
            # There is no display-mode control to press any more: the view is the box type's own,
            # so opening a grid must draw a grid on its own. Clicking a toggle that was deleted is
            # how this section spent 30 seconds timing out instead of saying anything.
            checks.check(
                "grid renders cells",
                page.locator(".grid-cell").count() >= 4,
                page.locator(".grid-cell").count(),
            )

        print("the box type picks the view, and no preference is stored")
        page.locator("#box-list li", has_text="Sunset").click()
        page.wait_for_timeout(800)
        config = api(base, "/api/boxes/Sunset")["config"]
        checks.check(
            "an image box is drawn by the image view",
            page.locator(".image-view").count() == 1,
            page.locator(".image-view").count(),
        )
        checks.check(
            "and nothing was written into config to say so",
            "display" not in config,
            config,
        )

        print("image keeps its ratio")
        facts = page.evaluate(
            """() => {
              const img = document.querySelector('.image-view img');
              if (!img) return null;
              const r = img.getBoundingClientRect();
              const fit = getComputedStyle(img).objectFit;
              const scale = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
              const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
              return {loaded: img.complete && img.naturalWidth > 0, fit,
                      painted: [Math.round(w), Math.round(h)],
                      ratioPainted: +(w / h).toFixed(3),
                      ratioNatural: +(img.naturalWidth / img.naturalHeight).toFixed(3)};
            }"""
        )
        checks.check("image loaded", bool(facts) and facts["loaded"])
        checks.check("object-fit contain", bool(facts) and facts["fit"] == "contain")
        checks.check(
            "ratio preserved", bool(facts) and facts["ratioPainted"] == facts["ratioNatural"], facts
        )

        print("un-configure what this run configures")
        # Every section below asserts absolute state that a previous run of this same file
        # moved: whether a note wraps, which grid cells are filled and how wide the grid is,
        # and -- worst of all -- which children of the desktop are still unplaced. That last one
        # did not merely fail a check, it crashed the run: the icons the earlier run had dragged
        # onto the surface were no longer in the strip, and the locator timed out 30 seconds on.
        #
        # A snapshot taken at the start and written back at the end was tried first, and it is
        # wrong here: it reproduces the *previous* run's dirt, so the second run still fails.
        # These checks describe a freshly seeded library, so the reset deletes the config
        # sections this tool creates and returns each box to never-configured -- what the seed
        # leaves behind.
        for box_name, section in (("Notes", "text"), ("Board", "grid"), ("Wall", "desktop")):
            try:
                here = api(base, f"/api/boxes/{urllib.parse.quote(box_name)}")
            except urllib.error.HTTPError:
                continue  # a library without that fixture; its section skips itself
            config = {k: v for k, v in (here["config"] or {}).items() if k != section}
            try:
                api_put(base, f"/api/boxes/{urllib.parse.quote(box_name)}", {"config": config})
                checks.check(f"{box_name}.{section} is back to unconfigured", section not in config)
            except urllib.error.HTTPError as exc:
                checks.check(f"{box_name}.{section} reset", False, exc.code)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(700)

        print("text view")
        notes = page.locator("#box-list li", has_text="Notes")
        if notes.count():
            notes.click()
            # the view is the box type's own now; there is no mode toggle to press
            page.wait_for_timeout(800)
            page.wait_for_timeout(600)
            checks.check("text is shown", page.locator(".text-view .text-body").count() == 1)
            page.locator(".text-view").click(button="right")
            page.wait_for_timeout(250)
            menu = page.locator("#context-menu").inner_text()
            checks.check("menu offers wrap", "Wrap lines" in menu)
            page.locator("#context-menu li", has_text="Wrap lines").click()
            page.wait_for_timeout(700)
            text_config = api(base, "/api/boxes/Notes")["config"].get("text", {})
            checks.check("wrap saved to config", text_config.get("wrap") is False, text_config)
            overflows = page.evaluate(
                """() => {
                  const pane = document.querySelector('.text-view');
                  return pane.scrollWidth > pane.clientWidth || getComputedStyle(pane).overflowX;
                }"""
            )
            checks.check("unwrapped text can scroll sideways", bool(overflows), overflows)

        print("grid drag and drop")
        # The recipe at the head of this file files nothing under Board, so on a library that has
        # never had a child put in one this section reported "no unplaced children" as a failure the
        # operator could not act on. It makes the box it needs and takes it back out again.
        board_uuid = api(base, "/api/boxes/Board")["uuid"]
        filler = api(base, "/api/boxes", {"name": "Grid-Filler", "box_type": "note"})
        api(base, f"/api/boxes/{board_uuid}/children", {"child": filler["uuid"]})
        page.locator("#box-list li", has_text="Board").click()
        # the view is the box type's own now; there is no mode toggle to press
        page.wait_for_timeout(900)
        try:
            if page.locator(".grid-unplaced .chip").count():
                before = api(base, "/api/boxes/Board")["config"].get("grid", {}).get("cells", {})
                page.locator(".grid-unplaced .chip").first.drag_to(
                    page.locator('.grid-cell[data-row="0"][data-column="0"]')
                )
                page.wait_for_timeout(900)
                after = api(base, "/api/boxes/Board")["config"].get("grid", {}).get("cells", {})
                checks.check("drop into a cell writes the layout", after != before, after)
            else:
                checks.check("something to drag onto the grid", False, "no unplaced children")
        finally:
            api_delete(base, f"/api/boxes/{filler['uuid']}")

        print("steppers")
        columns_before = api(base, "/api/boxes/Board")["config"].get("grid", {}).get("columns")
        page.locator('[data-action="grid-columns-plus"]').click()
        page.wait_for_timeout(800)
        columns_after = api(base, "/api/boxes/Board")["config"].get("grid", {}).get("columns")
        checks.check(
            "columns +1 saved",
            isinstance(columns_before, int) and columns_after == columns_before + 1,
            f"{columns_before} -> {columns_after}",
        )

        print("context menu creates in a cell")
        page.locator('.grid-cell[data-row="1"][data-column="1"]').click(button="right")
        page.wait_for_timeout(250)
        checks.check(
            "cell menu offers a new box", "New box" in page.locator("#context-menu").inner_text()
        )
        page.keyboard.press("Escape")

        print("desktop")
        wall = page.locator("#box-list li", has_text="Wall")
        if not wall.count():
            print("      (skipped: no `Wall` desktop box in this library)")
        else:
            wall.click()
            # the view is the box type's own now; there is no mode toggle to press
            page.wait_for_timeout(900)
            kids = api(base, "/api/boxes/Wall")["children"]
            checks.check(
                "a child nobody placed is not drawn on the surface",
                page.locator(".desktop-icon").count() == 0,
                f"{page.locator('.desktop-icon').count()} icons for {len(kids)} children",
            )
            checks.check(
                "the resource is painted as the background",
                page.evaluate(
                    """() => {
                      // The wallpaper hangs on the surface, not on the view: the view is the frame
                      // and the surface is the canvas that is scaled, so this is where the class
                      // and the --desk-bg url were moved to. Asserting on the view asked a box that
                      // holds no picture for one and reported the picture as missing.
                      const surf = document.querySelector('.desktop-surface');
                      if (!surf || !surf.classList.contains('has-background')) return false;
                      const style = getComputedStyle(surf);
                      return style.backgroundImage.includes('/raw')
                        && style.backgroundSize.includes('cover');
                    }"""
                ),
            )

            def rect_of(name: str) -> dict:
                uuid = api(base, f"/api/boxes/{name}")["uuid"]
                return (
                    api(base, "/api/boxes/Wall")["config"]
                    .get("desktop", {})
                    .get("positions", {})
                    .get(uuid, {})
                )

            def chip(name: str):
                return page.locator(".desktop-unplaced .chip", has_text=name)

            def drag_tile(name: str, dx: int, dy: int) -> None:
                """Move a placed icon with the pointer, by whole pixels."""
                uuid = api(base, f"/api/boxes/{name}")["uuid"]
                frame = page.locator(f'.desktop-icon[data-uuid="{uuid}"]').bounding_box()
                assert frame is not None, name
                start = (frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 3)
                page.mouse.move(*start)
                page.mouse.down()
                page.mouse.move(start[0] + dx / 2, start[1] + dy / 2, steps=6)
                page.mouse.move(start[0] + dx, start[1] + dy, steps=6)
                page.mouse.up()
                page.wait_for_timeout(800)

            checks.check(
                "a fresh desktop has no positions stored yet",
                api(base, "/api/boxes/Wall")["config"].get("desktop", {}).get("positions", {})
                == {},
            )
            checks.check(
                "children nobody placed wait on the strip below",
                chip("Tile").count() == 1 and chip("Loose").count() == 1,
                page.locator(".desktop-unplaced .chip").count(),
            )

            chip("Tile").drag_to(
                page.locator(".desktop-surface"), target_position={"x": 220, "y": 140}
            )
            page.wait_for_timeout(900)

            def landed_on_target(where: dict, x: int, y: int) -> bool:
                """A whole pixel, because the page measures itself in fractions."""
                return (
                    abs(int(where.get("x", -9999)) - x) <= 1
                    and abs(int(where.get("y", -9999)) - y) <= 1
                )

            landed = rect_of("Tile")
            checks.check(
                "dropping a box puts its icon where it was dropped",
                landed_on_target(landed, 220, 140),
                landed,
            )

            drag_tile("Tile", 70, 50)
            dragged = rect_of("Tile")
            checks.check(
                "dragging an icon moves it by the pixels it was dragged",
                landed_on_target(dragged, 290, 190),
                dragged,
            )

            page.locator('[data-action="desktop-snap"]').click()
            page.wait_for_timeout(700)
            grid_saved = api(base, "/api/boxes/Wall")["config"]["desktop"]
            checks.check(
                "the magnet is a setting on the box",
                grid_saved.get("snap") is True and grid_saved.get("grid") == 32,
                grid_saved,
            )
            checks.check(
                "the grid lines are drawn while it is engaged",
                page.locator(".desktop-view[data-snap='true']").count() == 1,
            )
            checks.check(
                "engaging the magnet leaves what is already placed alone",
                abs(rect_of("Tile").get("x", 0) - 290) <= 1,
                rect_of("Tile"),
            )
            chip("Loose").drag_to(
                page.locator(".desktop-surface"), target_position={"x": 45, "y": 45}
            )
            page.wait_for_timeout(900)
            snapped = rect_of("Loose")
            checks.check(
                "the magnet took the icon to the nearest line",
                snapped.get("x") == 32 and snapped.get("y") == 32,
                snapped,
            )

            checks.check(
                "every placed child has one icon on the surface",
                page.locator(".desktop-icon").count() == len(kids),
                f"{page.locator('.desktop-icon').count()} of {len(kids)}",
            )

            page.locator('[data-action="desktop-grid-plus"]').click()
            page.wait_for_timeout(700)
            checks.check(
                "the grid size is adjustable and saved",
                api(base, "/api/boxes/Wall")["config"]["desktop"]["grid"] == 36,
                api(base, "/api/boxes/Wall")["config"]["desktop"]["grid"],
            )

            painted = page.evaluate(
                """(uuid) => {
                  const tile = document.querySelector(`.desktop-icon[data-uuid="${uuid}"]`);
                  const surface = document.querySelector('.desktop-surface');
                  if (!tile || !surface) return null;
                  return {
                    left: tile.offsetLeft, top: tile.offsetTop,
                    right: tile.offsetLeft + tile.offsetWidth,
                    bottom: tile.offsetTop + tile.offsetHeight,
                    across: surface.offsetWidth, down: surface.offsetHeight,
                    grid: getComputedStyle(surface).backgroundSize,
                  };
                }""",
                api(base, "/api/boxes/Tile")["uuid"],
            )
            tile_rect = rect_of("Tile")
            checks.check(
                "the icon is painted where the box says it is",
                bool(painted)
                and painted["left"] == tile_rect["x"]
                and abs(painted["top"] - tile_rect["y"]) <= 1,
                painted,
            )
            checks.check(
                "the surface is big enough for everything on it",
                bool(painted)
                and painted["right"] <= painted["across"]
                and painted["bottom"] <= painted["down"]
                and painted["grid"].startswith("36px"),
                painted,
            )

            page.locator(".desktop-icon[data-uuid]").first.dblclick()
            page.wait_for_timeout(700)
            checks.check(
                "double-clicking an icon replaces the desktop",
                page.locator(".desktop-icon").count() == 0
                and page.locator("#breadcrumb button").count() >= 2,
                page.locator("#breadcrumb").inner_text(),
            )

            page.locator("#up").click()
            page.wait_for_timeout(800)
            tiles = page.locator(".desktop-icon")
            checks.check("the desktop comes back with its icons placed", tiles.count() == len(kids))
            tiles.first.click(button="right")
            page.wait_for_timeout(250)
            menu = page.locator("#context-menu").inner_text()
            checks.check(
                "the icon menu can modify it and take it off the desktop",
                "Modify" in menu and "Remove from Wall" in menu,
                menu.replace("\n", " / "),
            )
            page.keyboard.press("Escape")

        print("a row is a filing target in both lists")
        # ADR 40's whole argument is that a `DataTransfer` built inside the page can read its own
        # values mid-drag, so a synthetic probe proves the drop handler works and proves nothing
        # about whether a drop was ever allowed to happen. These are pointer drags -- mouse down,
        # move, up -- which is the only evidence that class, and they are what makes the sentence
        # about the harness true rather than aspirational.
        #
        # The four boxes are made here instead of being asked for in the recipe at the top of this
        # file, because the cases need a folder, a box to file, a second folder to file into, and
        # one row whose type refuses hand-filing (a dynamic folder picks its own members, ADR 25).
        made = {
            "shelf": api(base, "/api/boxes", {"name": "Row-Shelf", "box_type": "folder"}),
            "cubby": api(base, "/api/boxes", {"name": "Row-Cubby", "box_type": "folder"}),
            "doodle": api(base, "/api/boxes", {"name": "Row-Doodle", "box_type": "note"}),
            "filter": api(
                base,
                "/api/boxes",
                {
                    "name": "Row-Filter",
                    "box_type": "dynamic-folder",
                    "config": {"filter": {"type": "pdf"}},
                },
            ),
        }
        shelf_u, cubby_u = made["shelf"]["uuid"], made["cubby"]["uuid"]
        doodle_u, filter_u = made["doodle"]["uuid"], made["filter"]["uuid"]

        def pointer_drag(from_sel: str, to_sel: str) -> str:
            """Drag from one row to another with the mouse, and report what the target wore."""
            src, dst = page.locator(from_sel).first, page.locator(to_sel).first
            src.scroll_into_view_if_needed()
            dst.scroll_into_view_if_needed()
            a, b = src.bounding_box(), dst.bounding_box()
            if not (a and b):
                checks.check("both rows are on screen", False, (from_sel, to_sel, a, b))
                return ""
            x1, y1 = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
            x2, y2 = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
            page.mouse.move(x1, y1)
            page.mouse.down()
            page.mouse.move(x1 + 12, y1 + 6, steps=5)
            page.mouse.move((x1 + x2) / 2, (y1 + y2) / 2, steps=8)
            page.mouse.move(x2, y2, steps=8)
            page.wait_for_timeout(220)
            # Read the highlight *while the pointer is held down*: a zone that lights only because a
            # handler ran at the drop would tell us nothing about the promise made mid-drag.
            wore = page.evaluate("(s) => (document.querySelector(s)||{}).className || ''", to_sel)
            page.mouse.up()
            page.wait_for_timeout(800)
            return wore

        def panel_row(uuid: str) -> str:
            return f'#box-list li[data-uuid="{uuid}"]'

        def children_of(ref: str) -> list[str]:
            return [child["uuid"] for child in api(base, f"/api/boxes/{ref}/children")]

        try:
            api(base, f"/api/boxes/{shelf_u}/children", {"child": cubby_u})
            api(base, f"/api/boxes/{shelf_u}/children", {"child": doodle_u})
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(500)

            wore = pointer_drag(panel_row(doodle_u), panel_row(shelf_u))
            checks.check("a panel row lights for a dragged box", "drop-target" in wore, wore)
            checks.check(
                "a drop on a panel row files into that row's own box",
                doodle_u in children_of(shelf_u),
            )

            wore = pointer_drag(panel_row(doodle_u), panel_row(filter_u))
            checks.check(
                "a row that chooses its own members turns the box down in amber",
                "drop-refused" in wore and "drop-target" not in wore,
                wore,
            )
            checks.check("the refusal filed nothing", doodle_u not in children_of(filter_u))

            # The folder's own list: the row must win over the sheet behind it, which is the whole
            # point of the row handler and invisible to a source sweep.
            page.locator(panel_row(shelf_u)).click()
            page.wait_for_timeout(800)
            wore = pointer_drag(
                f'.child[data-uuid="{doodle_u}"]', f'.child[data-uuid="{cubby_u}"]'
            )
            checks.check("a row in the folder's own list lights too", "drop-target" in wore, wore)
            checks.check(
                "and files into the row, not into the folder being listed",
                doodle_u in children_of(cubby_u),
            )
            parents_after = [p["uuid"] for p in api(base, f"/api/boxes/{doodle_u}")["parents"]]
            checks.check(
                "filing into a row does not take the box out of where it already was",
                shelf_u in parents_after,
                parents_after,
            )
        finally:
            for key in ("doodle", "cubby", "shelf", "filter"):
                api_delete(base, f"/api/boxes/{made[key]['uuid']}")

        print("the index bar counts the files, and keeps watching through a failed read")
        # A stubbed status answer rather than a real long pass: the numbers on the bar are what is
        # under test, and a pass that finishes in 300 ms cannot be photographed mid-flight. The
        # server half is covered by tests/test_search.py (the callback) and by the repro tool
        # (`queue=2/839` against a real library copy), so each layer is proved at its own speed.
        busy_body = {
            "wanted": 40, "indexed": 12, "outstanding": 28, "stale": 3, "untouched": 25,
            "folders": 1, "unreadable": 0, "running": True, "scope": "everything", "last": None,
            "observed": {"checked_at": None, "age_seconds": None},
            "missing": {
                "resources": 0, "boxes": 0, "roots": [], "stray": 0,
                "candidates": 28, "work": 28,
            },
            "missing_roots": [],
            "queue": {"waiting": 0, "reading": True, "done": 7, "total": 12, "errors": []},
        }
        mode = {"kind": "busy"}

        def fake_status(route, _request):
            if mode["kind"] == "busy":
                route.fulfill(
                    status=200, content_type="application/json", body=json.dumps(busy_body)
                )
            elif mode["kind"] == "junk":
                # 200 with a body that is not JSON: the read then throws the way a dropped
                # connection throws, without the browser logging a network error that the
                # console check below would (rightly) count as a failure.
                route.fulfill(status=200, content_type="application/json", body="not json")
            else:
                route.fallback()  # let the real server answer

        page.route("**/api/index/status", fake_status)
        said = ""
        try:
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(400)
            checks.check(
                "the bar is there while work is running", page.locator("#index-bar").is_visible()
            )
            bar_text = page.locator("#index-bar-text").inner_text()
            checks.check("the bar carries its own numbers", bar_text == "7 / 12", bar_text)
            sentence = page.locator("#index-status").inner_text()
            checks.check(
                "the sentence says the same pair, in words",
                "reading 7 of 12" in sentence,
                sentence,
            )
            checks.check(
                "the bar's value and max follow the same two numbers",
                page.locator("#index-progress").get_attribute("value") == "7"
                and page.locator("#index-progress").get_attribute("max") == "12",
                f"{page.locator('#index-progress').get_attribute('value')} of "
                f"{page.locator('#index-progress').get_attribute('max')}",
            )

            mode["kind"] = "junk"
            page.wait_for_timeout(1800)
            said = page.locator("#index-status").inner_text()
            checks.check(
                "a failed read admits it instead of freezing the old number",
                said == "index: unknown",
                said,
            )

            mode["kind"] = "real"
            recovered = False
            deadline = time.time() + 15
            while time.time() < deadline:
                page.wait_for_timeout(300)
                said = page.locator("#index-status").inner_text()
                if said and said != "index: unknown":
                    recovered = True
                    break
            checks.check(
                "and the polling survived the failure", recovered, said
            )
            checks.check(
                "the bar is gone once nothing is running", page.locator("#index-bar").is_hidden()
            )
        finally:
            page.unroute("**/api/index/status")

        print("the Index status dialog states its numbers, and what is not work")
        # A real drift rather than a stub: a file is added, read, then edited, so the dialog is
        # opened with genuine counts behind it and the button that opens it is enabled by the
        # same arithmetic the report complained about.
        drift_dir = Path(tempfile.mkdtemp(prefix="verify-index-drift-"))
        created_drift = drift_dir / "drift.md"
        created_drift.write_text("the boiler pressure gauge leaks\n", encoding="utf-8")
        try:
            drift_box = api(
                base,
                "/api/boxes",
                {"name": "Driftfile", "box_type": "markdown", "resource_uri": str(created_drift)},
            )
            api(base, "/api/index/run", {"scope": "everything"})
            for _ in range(60):
                page.wait_for_timeout(200)
                if not api(base, "/api/index/status")["running"]:
                    break
            created_drift.write_text(
                "the boiler pressure gauge leaks and drips\n", encoding="utf-8"
            )
            api(base, "/api/index/check", {})
            status = api(base, "/api/index/status")
            missing = status["missing"]
            expected_up_to_date = max(status["wanted"] - status["stale"] - status["untouched"], 0)

            page.reload(wait_until="networkidle")
            page.wait_for_timeout(500)
            checks.check(
                "work on offer makes the number clickable",
                page.locator("#index-status").is_enabled(),
                page.locator("#index-status").inner_text(),
            )
            page.locator("#index-status").click()
            page.wait_for_timeout(400)
            modal = page.locator("#index-modal")
            checks.check("the dialog opens", modal.is_visible())
            checks.check(
                "it is titled Index status",
                page.locator("#index-modal h2").inner_text() == "Index status",
                page.locator("#index-modal h2").inner_text(),
            )
            checks.check(
                "and carries the same name for a screen reader",
                modal.get_attribute("aria-label") == "Index status",
            )

            tally = page.locator("#index-tally").inner_text()
            checks.check(
                "the four counts are on screen",
                all(
                    label in tally
                    for label in (
                        "files followed for the index",
                        "up to date",
                        "need reindexing",
                        "not indexed yet",
                    )
                ),
                " ".join(tally.split()),
            )
            checks.check(
                "and they are the server's numbers, not the page's arithmetic",
                f"{status['wanted']}" in tally
                and f"{expected_up_to_date}" in tally
                and f"{missing['work_stale']}" in tally,
                f"wanted={status['wanted']} up_to_date={expected_up_to_date} "
                f"work_stale={missing['work_stale']}",
            )
            checks.check(
                "the drifted file is counted as reindex work",
                missing["work_stale"] >= 1,
                missing,
            )
            checks.check(
                "the two actions are named for what they do",
                page.locator("#pending-look").inner_text() == "update index status"
                and page.locator("#pending-reindex").inner_text() == "Reindex",
                f"{page.locator('#pending-look').inner_text()} / "
                f"{page.locator('#pending-reindex').inner_text()}",
            )

            look_box = page.locator("#pending-look").bounding_box()
            close_box = page.locator("#index-close").bounding_box()
            checks.check(
                "Close sits on the same line as the actions",
                bool(look_box and close_box)
                and abs((look_box["y"] + look_box["height"] / 2)
                        - (close_box["y"] + close_box["height"] / 2))
                < 6,
                f"look y={look_box and round(look_box['y'])}"
                f" close y={close_box and round(close_box['y'])}",
            )
            checks.check(
                "and at the right edge, away from the work",
                bool(look_box and close_box) and close_box["x"] > look_box["x"] + look_box["width"],
            )

            asked_before = len(asked)
            page.locator("#pending-look").click()
            page.wait_for_timeout(1200)
            checks.check(
                "the button asks the server to look at the files",
                "index/check" in asked[asked_before:],
                asked[asked_before:][:6],
            )
            listed = page.locator("#index-pending-list li").count()
            checks.check(
                "and the names are still listed under the counts",
                listed >= 1,
                f"{listed} rows",
            )
            page.locator("#index-close").click()
            checks.check("and Close closes it", not modal.is_visible())
        finally:
            api_delete(base, f"/api/boxes/{drift_box['uuid']}")
            shutil.rmtree(drift_dir, ignore_errors=True)

        print("a folder that is not here stops being offered as work")
        # The complaint, end to end: "I have 833 to reindex ... then it says 833 to reindex".
        # A box is filed on a folder, the folder is indexed, the folder is deleted, and the page
        # is asked what it thinks -- first after looking at the files, then after being told to
        # do the work. What must not happen is an offer the button cannot honour.
        gone_dir = Path(tempfile.mkdtemp(prefix="verify-gone-dir-")) / "shelved"
        gone_dir.mkdir(parents=True)
        (gone_dir / "inside.md").write_text("the boiler pressure gauge leaks\n", encoding="utf-8")
        try:
            shelf = api(
                base,
                "/api/boxes",
                {
                    "name": "Shelved",
                    "box_type": "indexed-directory",
                    "resource_uri": str(gone_dir),
                    "index_resource": True,
                },
            )
            api(base, "/api/index/run", {"scope": "everything"})
            for _ in range(60):
                page.wait_for_timeout(200)
                if not api(base, "/api/index/status")["running"]:
                    break
            shutil.rmtree(gone_dir)
            api(base, "/api/index/check", {})
            status = api(base, "/api/index/status")
            checks.check(
                "the deleted folder is reported as gone, not as backlog",
                status["missing"]["resources"] >= 2 and status["missing"]["work"] == 0,
                f"resources={status['missing']['resources']} work={status['missing']['work']} "
                f"stale={status['stale']}",
            )
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(500)
            shown = page.locator("#index-status").inner_text()
            checks.check(
                "the panel offers no work it cannot do",
                "0 to reindex" not in shown and "to reindex" not in shown,
                shown,
            )
            explain = page.locator("#index-status").get_attribute("title") or ""
            checks.check(
                "and stays clickable, because it has something to explain",
                page.locator("#index-status").is_enabled() and "gone from this machine" in explain,
                " ".join(explain.split()),
            )
            page.locator("#index-status").click()
            page.wait_for_timeout(400)
            tallied = page.locator("#index-tally").inner_text()
            checks.check(
                "the dialog names the count of what is gone",
                "gone from this machine" in tallied,
                " ".join(tallied.split()),
            )
            checks.check(
                "and counts the work as zero while still counting what is followed",
                "need reindexing 0" in " ".join(tallied.split())
                and "files followed for the index" in tallied,
                " ".join(tallied.split()),
            )
            page.locator("#index-close").click()
            # Is the control a control at all? The rule is "clickable when there is work, or
            # when there is something to explain" -- compared against the answer that produced
            # the pane rather than against a state this file hopes the library is in, so it
            # cannot quietly become a check that only passes by luck.
            rule = api(base, "/api/index/status")
            should_be_open = rule["missing"]["work"] > 0 or rule["missing"]["resources"] > 0
            enabled = page.locator("#index-status").is_enabled()
            checks.check(
                "the panel is clickable exactly when it has work or an explanation",
                enabled == should_be_open,
                f"enabled={enabled} work={rule['missing']['work']} "
                f"gone={rule['missing']['resources']}",
            )
        finally:
            api_delete(base, f"/api/boxes/{shelf['uuid']}")
            shutil.rmtree(gone_dir.parent, ignore_errors=True)

        checks.check("no console errors", not errors, "; ".join(errors[:3]))
        page.screenshot(path="/tmp/verify-web-ui.png")
        browser.close()

    print()
    print("RESULT:", "OK" if not checks.failures else f"{len(checks.failures)} failure(s)")
    return 1 if checks.failures else 0


def _discover_browser() -> str | None:
    """A chromium Playwright has cached, newest revision first.

    The installed Playwright and the cached browsers drift apart on a machine that updates one and
    not the other -- this one expects revision 1248 and holds 1234 -- and `launch()` then fails with
    advice to download a browser that is already on disk. Finding the cached build keeps
    `make uitest` a single command; an explicit path, named as an argument or in
    $BOXGANIZER_CHROME, still wins.
    """
    roots = sorted(
        Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"),
        key=lambda item: item.as_posix(),
        reverse=True,
    )
    for candidate in roots:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for(base: str, seconds: float = 40.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def _seed(base: str, created: list[Path]) -> None:
    """Build the library this file's header describes, through the API.

    Done here rather than left to the copy-paste recipe because that recipe is exactly why this
    harness went stale: five commands a person runs by hand, in another terminal, before a check
    can run at all. Untested steps rot -- the recipe here named `box box add` for as long as the
    commands were two levels deep, and whole sections stopped matching the page unnoticed.
    """
    from PIL import Image

    images = Path(tempfile.mkdtemp(prefix="verify-web-ui-assets-"))
    created.append(images)
    Image.new("RGB", (320, 200), (30, 60, 120)).save(images / "sunset.png")
    Image.new("RGB", (640, 400), (20, 90, 60)).save(images / "wall.png")
    notes = images / "notes.md"
    notes.write_text("the quick brown fox\n", encoding="utf-8")
    for payload in (
        {"name": "Notes", "box_type": "text", "resource_uri": str(notes)},
        {"name": "Sunset", "box_type": "image", "resource_uri": str(images / "sunset.png")},
        {"name": "Board", "box_type": "grid"},
        {"name": "Wall", "box_type": "desktop", "resource_uri": str(images / "wall.png")},
        {"name": "Tile", "box_type": "text", "resource_uri": str(notes), "parent": "Wall"},
        {"name": "Loose", "box_type": "folder", "parent": "Wall"},
    ):
        api(base, "/api/boxes", payload)


def _serve_pids() -> set[int]:
    """Pids of every running `box serve`, read from the operating system.

    Not from a pid file or a shell's `$!`: the point of this check is that nothing was left
    behind, and a count taken from what we chose to remember is only as honest as that memory.
    The same `/proc` scan `box servers` shows a person is used here, so the two cannot disagree.
    """
    from boxganizer.servers import scan

    return {item.pid for item in scan()}


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--self"]
    self_contained = "--self" in sys.argv[1:]
    # A browser Playwright did not install can be named, for machines where the cached build does
    # not match the installed version. In --self there is no base URL to give, so the one
    # positional a person types IS that browser -- reading it as a URL was my first attempt,
    # which launched the default build, and the run died before it drew anything.
    named_browser = os.environ.get("BOXGANIZER_CHROME")
    if not self_contained:
        base = args[0] if args else "http://127.0.0.1:8300"
        executable = args[1] if len(args) > 1 else named_browser
        return _run(base, executable)
    executable = args[0] if args else named_browser
    # Counted before, counted after, and the difference reported: the reap below is correct as
    # far as it knows, and this machine already collected 32 servers from runs that each believed
    # they had cleaned up. Both counts come from `/proc`, so neither end depends on what this
    # script chose to remember.
    before = _serve_pids()
    code = _self_contained(executable)
    leftover = _serve_pids() - before
    if leftover:
        print(
            f"LEFTOVER SERVERS: {sorted(leftover)} outlived this run "
            f"(there were {sorted(before)} before it)"
        )
        return 3
    print(f"no server left behind: {len(_serve_pids())} running, same as before")
    return code


def _self_contained(executable: str | None) -> int:
    """Start a throwaway library and server, run the checks against them, take the server down."""
    workdir = Path(tempfile.mkdtemp(prefix="verify-web-ui-"))
    created: list[Path] = [workdir]
    port = _free_port()
    server: subprocess.Popen[bytes] | None = None
    try:
        db = workdir / "boxes.db"
        subprocess.run(
            [sys.executable, "-m", "boxganizer", "--db", str(db), "init"],
            check=True,
            capture_output=True,
        )
        # A child we started and reap ourselves: `nohup cmd & echo $!` in a shell records the
        # wrapper, not the process, which is how this machine collected dozens of servers that
        # nobody could kill by the pid they had written down.
        server = subprocess.Popen(
            [sys.executable, "-m", "boxganizer", "--db", str(db), "serve", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        if not _wait_for(base):
            print(f"the throwaway server on {port} never answered; giving up")
            return 2
        _seed(base, created)
        print(f"self-contained run: {base} on {db}")
        return _run(base, executable)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        for directory in created:
            shutil.rmtree(directory, ignore_errors=True)
        # Nothing returns from here: a `return` in a `finally` silences whatever exception is
        # in flight, which would turn a crash into a clean exit. The accounting is the caller's.


if __name__ == "__main__":
    raise SystemExit(main())
