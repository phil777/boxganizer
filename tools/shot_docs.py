"""Take the screenshots the README is built around.

    make shots
    # or, naming a browser this machine already has:
    .venv/bin/python tools/shot_docs.py /usr/bin/chromium

The library in the pictures is built for the occasion in a temporary directory and thrown
away afterwards: nothing here reads the developer's database, and the boxes are invented
names rather than anyone's notes. The server is started by this script and stopped by it
too, so the pictures show a boxganizer in the state a first-time reader would find — empty
root, four boxes, nothing configured but what the script configured on purpose.

One browser, one viewport, one font: 1440x900. Screenshots that drift because somebody's
window was a different size are worse than none, because they quietly prove the README
wrong. The browser is Playwright's own unless one is named as the argument or in
`BOXGANIZER_CHROME` — the same escape `tools/verify_web_ui.py` offers, for machines where
the cached build does not match the installed Playwright.
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
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only needed to say what `page` is
    from playwright.sync_api import Page

VIEWPORT = {"width": 1440, "height": 900}
#: The four pictures, in the order the README shows them. The names are committed, so a
#: second run overwrites in place instead of leaving a family of near-copies behind.
SHOTS = {
    "web-library-note.png": "the library: the note open in the main pane",
    "web-grid.png": "a grid holding boxes",
    "web-filter.png": "a dynamic folder and the filter that fills it",
    "web-viewport.png": "a viewport split into two panes",
}
FILTER_EXPRESSION = "tag:kitchen or name:pot"

def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])

class Library:
    """A boxganizer server over a library that exists only for this run."""

    def __init__(self, root: Path, port: int) -> None:
        self.root = root
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Library:
        env = dict(os.environ)
        # Own three paths, own environment: whatever the caller had pointed at stays
        # untouched, and the child cannot fall back to a default database.
        env["BOXGANIZER_DB"] = str(self.root / "boxes.db")
        env["BOXGANIZER_CONFIG"] = str(self.root / "config.toml")
        env["BOXGANIZER_CONTENT_ROOT"] = str(self.root / "content")
        (self.root / "content").mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "boxganizer", "serve", "--host", "127.0.0.1", "--port",
                str(self.port)],
            cwd=self.root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.wait()
        return self

    def __exit__(self, *_: object) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # a stuck server is loud, not silent
                self.process.kill()

    def wait(self) -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                self.get("/api/boxes")
                return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.2)
        raise RuntimeError(f"the server on port {self.port} did not answer within 30 seconds")

    def request(self, method: str, path: str, payload: object = None) -> object:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=body,
            headers={"content-type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}

    def get(self, path: str) -> object:
        return self.request("GET", path)

    def post(self, path: str, payload: object = None) -> object:
        return self.request("POST", path, payload)

    def box(self, name: str, box_type: str, **fields: object) -> str:
        made = self.post("/api/boxes", {"name": name, "box_type": box_type, **fields})
        assert isinstance(made, dict)
        return str(made["uuid"])

def seed(library: Library) -> dict[str, str]:
    """Build the small library the four pictures are taken from.

    Invented names, one deliberate shape each: a note with words in it, a grid with boxes
    sitting in named cells, a tag branch a filter can reach, and a viewport with two
    different kinds of box side by side.
    """
    library.post("/api/tags", {"name": "kitchen"})
    uuids = {
        "note": library.box("Standing on the shoulders", "note"),
        "passport": library.box("Passport", "file", resource_uri=str(library.root /
            "Passport.pdf")),
        "receipt": library.box("Electricity receipt", "file", resource_uri=str(library.root /
            "receipt.pdf")),
        "map": library.box("Bus map", "image", resource_uri=str(library.root / "map.png")),
        "pot": library.box("Pressure cooker", "note"),
        "grid": library.box("Reading desk", "grid"),
        "folder": library.box("Kitchen", "dynamic-folder"),
        "viewport": library.box("Compare", "tiling-viewport"),
    }
    (library.root / "Passport.pdf").write_bytes(b"%PDF-1.4\n")
    (library.root / "receipt.pdf").write_bytes(b"%PDF-1.4\n")
    (library.root / "map.png").write_bytes(_tiny_png())

    put_config(library, uuids["grid"], {"grid": {"rows": 3, "columns": 4}})
    for name, cell in (
        ("note", [0, 0]),
        ("passport", [0, 3]),
        ("map", [1, 1]),
        ("receipt", [2, 2]),
    ):
        library.post(f"/api/boxes/{uuids['grid']}/grid/place", {"child": uuids[name], "cell": cell})

    # The folder is a question, so it needs something to be a question about.
    library.post(f"/api/boxes/{uuids['pot']}/tags", {"name": "kitchen"})
    put_config(library, uuids["folder"], {"filter": {"expression": FILTER_EXPRESSION}})

    library.post(f"/api/boxes/{uuids['viewport']}/viewports/open", {"node": "v1", "child":
        uuids["grid"]})
    library.post(f"/api/boxes/{uuids['viewport']}/viewports/split", {"node": "v1", "direction":
        "horizontal"})
    library.post(f"/api/boxes/{uuids['viewport']}/viewports/open", {"node": "v2", "child":
        uuids["folder"]})
    return uuids

def put_config(library: Library, uuid: str, section: dict[str, object]) -> None:
    """Merge one config section through the ordinary endpoint, as the page does."""
    current = library.get(f"/api/boxes/{uuid}")
    assert isinstance(current, dict)
    config = dict(current.get("config") or {})
    config.update(section)
    library.request("PUT", f"/api/boxes/{uuid}/config", {"config": config})

def _tiny_png() -> bytes:
    """A 1x1 blue pixel: enough for an image box to have something to draw."""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )

def expect(page: Page, what: str, ok: bool) -> None:
    """Refuse to publish a picture of the wrong thing.

    The alternative is a human checking four PNGs by eye on every change, which does not happen,
    and a README whose screenshots quietly drift from the software they illustrate.
    """
    if not ok:
        raise RuntimeError(f"{path_of[0]} would show the wrong thing: {what}")

path_of = [""]

def shoot(library: Library, assets: Path, executable: str | None) -> list[Path]:
    from playwright.sync_api import sync_playwright

    written: list[Path] = []
    with sync_playwright() as drivers:
        browser = drivers.chromium.launch(
            executable_path=executable, args=["--no-sandbox", "--force-color-profile=srgb"] if
                executable else []
        )
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        # Light on purpose: `color-scheme: light dark` follows the reader's system, and two
        # readers with opposite settings would otherwise publish different libraries.
        page.emulate_media(color_scheme="light")
        try:
            uuids = seed(library)
            page.goto(f"{library.base}/", wait_until="networkidle")
            page.wait_for_timeout(400)

            def open_box(key: str) -> None:
                page.evaluate(f"openBox({uuids[key]!r})")
                page.wait_for_timeout(700)

            # 1. the library: the note open, written the way a person writes it — in the page
            path_of[0] = "web-library-note.png"
            open_box("note")
            page.click(".note-switch")
            page.wait_for_timeout(300)
            page.click(".note-surface")
            page.keyboard.type(
                "# Standing on the shoulders\n\n"
                "- A box is a name, an icon, and whatever you file inside it.\n"
                "- The kitchen branch holds what the filter below reaches.\n"
            )
            page.wait_for_timeout(900)
            page.click(".note-switch")
            page.wait_for_timeout(900)
            expect(
                page, "the note is rendered", page.locator(".note-view h1").count() == 1
            )
            expect(
                page, "the library list has boxes", page.locator("#box-list li").count() >= 8
            )

            written.append(save(page, assets / "web-library-note.png"))

            # 2. a grid: rows, columns, and four boxes sitting in named cells
            path_of[0] = "web-grid.png"
            open_box("grid")
            expect(page, "twelve cells", page.locator(".grid-cell").count() == 12)
            expect(page, "four occupied cells", page.locator(".grid-cell.occupied").count() == 4)
            written.append(save(page, assets / "web-grid.png"))

            # 3. a dynamic folder and the filter that decides its members
            path_of[0] = "web-filter.png"
            open_box("folder")
            page.click("#toggle-config")
            page.wait_for_timeout(500)
            expect(page, "the filter bar is unfolded", page.locator(".filter-bar").is_visible())
            expect(page, "one row per clause", page.locator(".filter-row").count() >= 2)
            expect(
                page,
                "the member it found",
                "Pressure cooker" in page.locator(".children").inner_text(),
            )
            written.append(save(page, assets / "web-filter.png"))

            # 4. a viewport lending its space to two boxes at once
            path_of[0] = "web-viewport.png"
            open_box("viewport")
            page.wait_for_timeout(900)
            expect(page, "two panes side by side", page.locator(".vp-pane").count() == 2)
            expect(
                page,
                "a grid in one and the folder in the other",
                page.locator(".grid-board").count() == 1
                and page.locator(".vp-pane .children-view").count() == 1,
            )
            written.append(save(page, assets / "web-viewport.png"))
        finally:
            browser.close()
    return written

def save(page: Page, path: Path) -> Path:
    page.screenshot(path=str(path))
    return path

def main() -> int:
    executable = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BOXGANIZER_CHROME") or None
    repo = Path(__file__).resolve().parent.parent
    assets = repo / "doc" / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("no browser: install playwright first — .venv/bin/pip install playwright")
        return 2

    root = Path(tempfile.mkdtemp(prefix="boxganizer-shots-"))
    try:
        port = free_port()
        with Library(root, port) as library:
            print(f"throwaway library: {root / 'boxes.db'}")
            try:
                written = shoot(library, assets, executable)
            except Exception as error:  # one honest sentence, never a traceback
                print(f"no browser: {type(error).__name__}: {str(error).splitlines()[0]}")
                return 2
            for path in written:
                print(f"{path.relative_to(repo)}  {SHOTS.get(path.name, '')}")
            if len(written) != len(SHOTS):
                print(f"only {len(written)} of {len(SHOTS)} pictures were taken")
                return 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
