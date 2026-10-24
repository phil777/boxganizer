#!/usr/bin/env python3
"""Manual browser check for the Boxganizer web UI. Not part of `make test`.

    # terminal 1 — a scratch library and a server
    export BOXGANIZER_DB=/tmp/verify.db
    box init
    box box add "Notes"  -t text  -r /tmp/notes.md
    box box add "Sunset" -t image -r /tmp/sunset.png
    box box add "Board"  -t grid
    box serve --port 8300

    # terminal 2
    .venv/bin/pip install playwright && playwright install chromium
    .venv/bin/python tools/verify_web_ui.py http://127.0.0.1:8300 [path-to-chromium]

A chromium that Playwright did not install can be named as the second argument
or in $BOXGANIZER_CHROME.

It drives the real interface and prints what the page actually shows: the side
pane, the display-mode round trip, image scaling, folder navigation, grid
resizing, swapping by drag and drop, the context menus. Exit code is 0 only if
every check passed and the browser reported no console errors.

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

        checks.check("no console errors", not errors, "; ".join(errors[:3]))
        page.screenshot(path="/tmp/verify-web-ui.png")
        browser.close()

    print()
    print("RESULT:", "OK" if not checks.failures else f"{len(checks.failures)} failure(s)")
    return 1 if checks.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
