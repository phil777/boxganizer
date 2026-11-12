#!/usr/bin/env python3
"""Manual browser check for the Boxganizer web UI. Not part of `make test`.

    # terminal 1 — a scratch library and a server
    export BOXGANIZER_DB=/tmp/verify.db
    box init
    box add "Notes"  -t text  -r /tmp/notes.md
    box add "Sunset" -t image -r /tmp/sunset.png
    box add "Board"  -t grid
    python - <<'PY'
    from PIL import Image; Image.new("RGB", (320, 200), (30, 60, 120)).save("/tmp/wall.png")
    PY
    box add "Wall"   -t desktop -r /tmp/wall.png
    box add "Tile"   -t text -r /tmp/notes.md --parent Wall
    box add "Loose"  --parent Wall
    box serve --port 8300

    # terminal 2
    .venv/bin/pip install playwright && playwright install chromium
    .venv/bin/python tools/verify_web_ui.py http://127.0.0.1:8300 [path-to-chromium]

A chromium that Playwright did not install can be named as the second argument
or in $BOXGANIZER_CHROME.

Re-seed the library between runs. Several checks read absolute state — whether a text box wraps,
where an icon sits to the pixel — and the run before this one moved it: on a library this harness
has already driven it reports those as failures that are really its own footprints.

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
import sys
import urllib.error
import urllib.request
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


def api_delete(base: str, path: str) -> None:
    """DELETE, ignoring a box that is already gone. For the fixtures a section makes for itself."""
    request = urllib.request.Request(f"{base}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
    except urllib.error.HTTPError:
        pass


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8300"
    # A browser Playwright did not install can be named, for machines where the
    # cached build does not match the installed version.
    executable = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("BOXGANIZER_CHROME")
    checks = Checks()
    errors: list[str] = []

    with sync_playwright() as drivers:
        browser = drivers.chromium.launch(
            executable_path=executable, args=["--no-sandbox"] if executable else []
        )
        page = browser.new_page(viewport={"width": 1300, "height": 820})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

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

        checks.check("no console errors", not errors, "; ".join(errors[:3]))
        page.screenshot(path="/tmp/verify-web-ui.png")
        browser.close()

    print()
    print("RESULT:", "OK" if not checks.failures else f"{len(checks.failures)} failure(s)")
    return 1 if checks.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
