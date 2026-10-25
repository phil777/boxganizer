#!/usr/bin/env python3
"""Manual browser check for the Boxganizer web UI. Not part of `make test`.

    # terminal 1 — a scratch library and a server
    export BOXGANIZER_DB=/tmp/verify.db
    box init
    box box add "Notes"  -t text  -r /tmp/notes.md
    box box add "Sunset" -t image -r /tmp/sunset.png
    box box add "Board"  -t grid
    python - <<'PY'
    from PIL import Image; Image.new("RGB", (320, 200), (30, 60, 120)).save("/tmp/wall.png")
    PY
    box box add "Wall"   -t desktop -r /tmp/wall.png
    box box add "Tile"   -t text -r /tmp/notes.md --parent Wall
    box box add "Loose"  --parent Wall
    box serve --port 8300

    # terminal 2
    .venv/bin/pip install playwright && playwright install chromium
    .venv/bin/python tools/verify_web_ui.py http://127.0.0.1:8300 [path-to-chromium]

A chromium that Playwright did not install can be named as the second argument
or in $BOXGANIZER_CHROME.

It drives the real interface and prints what the page actually shows: the side
pane, the display-mode round trip, image scaling, folder navigation, grid
resizing, swapping by drag and drop, the context menus, and the desktop surface
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

        print("folder navigation")
        board = page.locator("#box-list li", has_text="Board")
        if board.count():
            board.click()
            page.wait_for_timeout(300)
            page.locator('#mode-toggle button[data-mode="content"]').click()
            page.wait_for_timeout(700)
            checks.check(
                "grid renders cells",
                page.locator(".grid-cell").count() >= 4,
                page.locator(".grid-cell").count(),
            )

        print("display mode is stored in the box")
        page.locator("#box-list li", has_text="Sunset").click()
        page.wait_for_timeout(200)
        page.locator('#mode-toggle button[data-mode="content"]').click()
        page.wait_for_timeout(800)
        config = api(base, "/api/boxes/Sunset")["config"]
        checks.check("config.display == content", config.get("display") == "content", config)

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
            page.wait_for_timeout(300)
            page.locator('#mode-toggle button[data-mode="content"]').click()
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
        page.locator("#box-list li", has_text="Board").click()
        page.wait_for_timeout(300)
        page.locator('#mode-toggle button[data-mode="content"]').click()
        page.wait_for_timeout(700)
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
            page.wait_for_timeout(300)
            page.locator('#mode-toggle button[data-mode="content"]').click()
            page.wait_for_timeout(800)
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
                      const view = document.querySelector('.desktop-view');
                      return !!view && view.classList.contains('has-background')
                        && getComputedStyle(view).backgroundImage.includes('/raw');
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

        checks.check("no console errors", not errors, "; ".join(errors[:3]))
        page.screenshot(path="/tmp/verify-web-ui.png")
        browser.close()

    print()
    print("RESULT:", "OK" if not checks.failures else f"{len(checks.failures)} failure(s)")
    return 1 if checks.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
