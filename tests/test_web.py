"""JSON API and the HTML page, exercised with FastAPI's TestClient."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from boxganizer import mqtt
from boxganizer.errors import MqttError
from boxganizer.models import Box
from boxganizer.store import BoxStore

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from boxganizer.web.app import create_app


@pytest.fixture
def client(populated: BoxStore) -> Iterator[TestClient]:
    with TestClient(create_app(populated)) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    body: dict[str, Any] = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["boxes"] == 5


def test_list_boxes(client: TestClient) -> None:
    names = [box["name"] for box in client.get("/api/boxes").json()]
    assert names == ["Books", "Garage", "House", "Kitchen", "Passport"]


def test_create_box(client: TestClient) -> None:
    response = client.post(
        "/api/boxes",
        json={"name": "Studio", "description": "Workspace", "metadata": {"floor": "2"}},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["id"] is not None
    assert client.get("/api/boxes/studio").json()["metadata"]["floor"] == "2"


def test_create_duplicate_box_conflicts(client: TestClient) -> None:
    response = client.post("/api/boxes", json={"name": "kitchen"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "duplicate_name"


def test_create_box_without_name_is_unprocessable(client: TestClient) -> None:
    assert client.post("/api/boxes", json={"description": "nameless"}).status_code == 422


def test_read_box_includes_relations(client: TestClient) -> None:
    body = client.get("/api/boxes/Kitchen").json()
    assert [parent["name"] for parent in body["parents"]] == ["House"]
    assert [child["name"] for child in body["children"]] == ["Passport"]
    assert body["parents"][0]["uuid"]


def test_read_unknown_box_is_not_found(client: TestClient) -> None:
    response = client.get("/api/boxes/Ghost")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "box_not_found"


def test_delete_box(client: TestClient) -> None:
    assert client.delete("/api/boxes/Books").status_code == 204
    assert client.get("/api/boxes/Books").status_code == 404


def test_link_boxes(client: TestClient) -> None:
    response = client.post("/api/boxes/House/children", json={"child": "Books"})
    assert response.status_code == 201
    assert "Books" in [child["name"] for child in response.json()["children"]]


def test_a_cycle_through_the_api_is_accepted(client: TestClient) -> None:
    """The graph may contain cycles: House holds Kitchen holds Passport holds House."""
    response = client.post("/api/boxes/Passport/children", json={"child": "House"})
    assert response.status_code == 201
    assert "House" in [child["name"] for child in response.json()["children"]]


def test_search_endpoint(client: TestClient) -> None:
    names = [box["name"] for box in client.get("/api/search", params={"q": "cooking"}).json()]
    assert names == ["Kitchen"]


def test_the_page_is_an_application_shell(client: TestClient) -> None:
    page = client.get("/")
    assert page.headers["content-type"].startswith("text/html")
    assert "Boxganizer" in page.text
    for landmark in (
        'id="side"',
        'id="new-box"',
        'id="search"',
        'id="box-list"',
        'id="canvas"',
        'id="breadcrumb"',
        'id="mode-toggle"',
        'id="new-box-modal"',
        'id="context-menu"',
    ):
        assert landmark in page.text, landmark


def test_serve_function_runs_uvicorn_with_a_real_app(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    from boxganizer.web import server as web_server

    seen: dict[str, object] = {}

    def fake_run(asgi: object, **kwargs: object) -> None:
        seen["asgi"] = asgi
        seen.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    announced: list[tuple[str, int]] = []

    def announce(bound_host: str, bound_port: int) -> None:
        announced.append((bound_host, bound_port))

    web_server.run(host="0.0.0.0", port=1234, db=db_path, on_start=announce)
    assert announced == [("0.0.0.0", 1234)]
    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 1234
    assert getattr(seen["asgi"], "title", None) == "Boxganizer"
    assert db_path.exists()


def test_the_shell_assets_are_served(client: TestClient) -> None:
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    stylesheet = client.get("/static/app.css")
    assert stylesheet.status_code == 200
    assert "css" in stylesheet.headers["content-type"]


def test_the_shell_fetches_its_data_from_the_api(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for endpoint in ("/api/boxes", "/api/types", "/api/search"):
        assert endpoint in script, endpoint


# -- box types through the API ---------------------------------------------


def test_types_endpoint(client: TestClient) -> None:
    described = client.get("/api/types").json()
    keys = {entry["key"] for entry in described}
    assert {"markdown", "3d-model", "home-assistant"} <= keys


def test_types_endpoint_can_be_reduced_to_choices(client: TestClient) -> None:
    keys = {
        entry["key"] for entry in client.get("/api/types", params={"selectable_only": True}).json()
    }
    assert "resource" not in keys
    assert "file" in keys


def test_create_a_box_of_a_type(client: TestClient) -> None:
    response = client.post(
        "/api/boxes",
        json={"name": "Cellar temp", "box_type": "mqtt", "metadata": {"topic": "cellar/temp"}},
    )
    assert response.status_code == 201
    assert response.json()["box_type"] == "mqtt"
    assert client.get("/api/boxes/Cellar temp").json()["box_type"] == "mqtt"


def test_create_with_an_unknown_type_is_unprocessable(client: TestClient) -> None:
    response = client.post("/api/boxes", json={"name": "Odd", "box_type": "spreadsheet"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unknown_box_type"


def test_create_that_breaks_the_type_rules_is_unprocessable(client: TestClient) -> None:
    response = client.post("/api/boxes", json={"name": "Fake", "box_type": "image"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_for_type"
    assert "resource" in detail["reason"]


def test_linking_into_a_leaf_type_conflicts(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "Pic", "box_type": "image", "resource_uri": "/a.png"})
    response = client.post("/api/boxes/Pic/children", json={"child": "Books"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "children_not_allowed"


def test_the_types_endpoint_carries_what_the_modal_needs(client: TestClient) -> None:
    types = {
        entry["key"]: entry
        for entry in client.get("/api/types", params={"selectable_only": True}).json()
    }
    assert types["grid"]["summary"]
    assert "folder" in types["cabinet"]["ancestors"]


# -- config ----------------------------------------------------------------


def test_create_a_box_with_config(client: TestClient) -> None:
    response = client.post(
        "/api/boxes",
        json={
            "name": "Cellar",
            "box_type": "cabinet",
            "config": {"drawers": 4, "layout": {"rows": 2, "labels": ["tools", "wine"]}},
        },
    )
    assert response.status_code == 201
    assert response.json()["config"]["drawers"] == 4
    read = client.get("/api/boxes/Cellar").json()
    assert read["config"]["layout"]["labels"] == ["tools", "wine"]


def test_config_must_be_an_object(client: TestClient) -> None:
    response = client.post("/api/boxes", json={"name": "Bad", "config": [1, 2]})
    assert response.status_code == 422


def test_a_box_without_config_reports_an_empty_object(client: TestClient) -> None:
    assert client.get("/api/boxes/Kitchen").json()["config"] == {}


# -- references, children and config ---------------------------------------


def test_a_box_can_be_read_by_uuid(client: TestClient, populated: BoxStore) -> None:
    uuid = populated.get("Kitchen").uuid
    body = client.get(f"/api/boxes/{uuid}").json()
    assert body["name"] == "Kitchen"


def test_children_are_listed_for_a_container(client: TestClient) -> None:
    names = [box["name"] for box in client.get("/api/boxes/House/children").json()]
    assert names == ["Garage", "Kitchen"]


def test_children_of_an_unknown_box_are_not_found(client: TestClient) -> None:
    assert client.get("/api/boxes/Ghost/children").status_code == 404


def test_config_is_replaced_and_persisted(client: TestClient, populated: BoxStore) -> None:
    response = client.put(
        "/api/boxes/Kitchen/config",
        json={"config": {"view": "grid", "rows": 3, "cells": {"0,0": "abc"}}},
    )
    assert response.status_code == 200
    assert response.json()["config"]["rows"] == 3
    assert populated.get("Kitchen").config["view"] == "grid"


def test_put_config_must_be_an_object(client: TestClient) -> None:
    assert client.put("/api/boxes/Kitchen/config", json={"config": [1]}).status_code == 422


def test_config_of_an_unknown_box_is_not_found(client: TestClient) -> None:
    assert client.put("/api/boxes/Ghost/config", json={"config": {}}).status_code == 404


def test_a_box_can_be_created_inside_another(client: TestClient) -> None:
    response = client.post(
        "/api/boxes", json={"name": "Drawer", "box_type": "cabinet", "parent": "House"}
    )
    assert response.status_code == 201
    assert "Drawer" in [box["name"] for box in client.get("/api/boxes/House/children").json()]


def test_creating_inside_a_parent_by_uuid(client: TestClient, populated: BoxStore) -> None:
    parent = populated.get("Kitchen").uuid
    response = client.post("/api/boxes", json={"name": "Spoon", "parent": parent})
    assert response.status_code == 201
    children = client.get(f"/api/boxes/{parent}/children").json()
    assert [box["name"] for box in children] == ["Passport", "Spoon"]


def test_creating_inside_an_unknown_parent_is_reported(client: TestClient) -> None:
    response = client.post("/api/boxes", json={"name": "Orphan", "parent": "Ghost"})
    assert response.status_code == 404


def test_a_box_can_be_deleted_by_uuid(client: TestClient, populated: BoxStore) -> None:
    uuid = populated.get("Books").uuid
    assert client.delete(f"/api/boxes/{uuid}").status_code == 204
    assert client.get(f"/api/boxes/{uuid}").status_code == 404


# -- serving the file behind a box -----------------------------------------


def _write(tmp_path: Path, name: str = "pic.png", body: bytes = b"\x89PNG fake") -> Path:
    path = tmp_path / name
    path.write_bytes(body)
    return path


def test_a_file_box_serves_its_bytes(
    client: TestClient, populated: BoxStore, tmp_path: Path
) -> None:
    path = _write(tmp_path)
    box = populated.add(Box(name="Picture", box_type="image", resource_uri=str(path)))
    response = client.get(f"/api/boxes/{box.uuid}/raw")
    assert response.status_code == 200
    assert response.content == b"\x89PNG fake"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_file_url_is_served_too(client: TestClient, populated: BoxStore, tmp_path: Path) -> None:
    path = _write(tmp_path, "notes.txt", b"hello")
    box = populated.add(Box(name="Text", box_type="text", resource_uri=path.as_uri()))
    response = client.get(f"/api/boxes/{box.uuid}/raw")
    assert response.status_code == 200
    assert response.text == "hello"


def test_types_without_a_file_are_not_served(client: TestClient) -> None:
    response = client.get("/api/boxes/House/raw")
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "type_not_servable"


def test_a_file_box_without_a_resource_is_not_found(
    client: TestClient, populated: BoxStore
) -> None:
    box = populated.add(Box(name="Empty image", box_type="image", resource_uri="/tmp/gone.png"))
    response = client.get(f"/api/boxes/{box.uuid}/raw")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "resource_missing"


def test_a_directory_is_not_read_as_content(client: TestClient, populated: BoxStore) -> None:
    box = populated.add(Box(name="Some dir", box_type="directory", resource_uri="/tmp"))
    response = client.get(f"/api/boxes/{box.uuid}/raw")
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "type_not_servable"


def test_an_http_box_is_not_read_from_disk(client: TestClient, populated: BoxStore) -> None:
    box = populated.add(Box(name="Site", box_type="web", resource_uri="https://example.com"))
    response = client.get(f"/api/boxes/{box.uuid}/raw")
    assert response.status_code == 415


def test_svg_is_sandboxed(client: TestClient, populated: BoxStore, tmp_path: Path) -> None:
    path = _write(tmp_path, "mark.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    box = populated.add(Box(name="Mark", box_type="image", resource_uri=str(path)))
    response = client.get(f"/api/boxes/{box.uuid}/raw")
    assert response.status_code == 200
    assert "sandbox" in response.headers["content-security-policy"]


def test_a_confined_server_refuses_files_outside_its_root(
    populated: BoxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    good = _write(root, "ok.png")
    outside = _write(tmp_path, "secret.png")

    monkeypatch.setenv("BOXGANIZER_CONTENT_ROOT", str(root))
    from boxganizer.web.app import create_app

    with TestClient(create_app(populated)) as confined:
        box_in = populated.add(Box(name="Inside", box_type="image", resource_uri=str(good)))
        box_out = populated.add(Box(name="Outside", box_type="image", resource_uri=str(outside)))
        assert confined.get(f"/api/boxes/{box_in.uuid}/raw").status_code == 200
        refused = confined.get(f"/api/boxes/{box_out.uuid}/raw")
        assert refused.status_code == 403
        assert refused.json()["detail"]["error"] == "resource_out_of_range"


# -- the text view's rules travel with the assets ---------------------------


def test_the_text_view_is_registered(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'registerView("text"' in script
    assert "TEXT_DEFAULTS" in script
    assert "wrap" in script and "crop" in script and "font_size" in script


def test_horizontal_scrolling_is_tied_to_wrapping(client: TestClient) -> None:
    css = client.get("/static/app.css").text
    assert '.text-view[data-wrap="off"] { overflow-x: auto; }' in css
    assert "overflow-y: auto" in css


# -- the folder view --------------------------------------------------------


def test_the_folder_view_is_registered_for_containers(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'registerView("folder", folderView)' in script
    # A directory on disk is browsed, not listed like a folder.
    assert 'registerView("directory", directoryView)' in script
    assert "FOLDER_DEFAULTS" in script


def test_a_child_opens_over_its_parent(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'item.addEventListener("dblclick"' in script
    assert "ctx.open(child.uuid, { trail: ctx.trail(), mode: ctx.mode() })" in script


def test_content_is_the_default_and_a_saved_setting_still_wins(client: TestClient) -> None:
    """A box shows its content first; a saved preference beats the browsing mode."""
    script = client.get("/static/app.js").text
    assert 'setting(box, "display", options.mode || "content")' in script
    assert '|| "icon"' not in script


def test_the_root_box_is_always_its_content(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "const isRootType = (box) =>" in script
    assert "state.mode = isRootType(box)" in script
    assert 'button.disabled = forced && button.dataset.mode === "icon"' in script


def test_the_root_view_shows_its_one_child_content(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'registerView("root", {' in script
    assert "let child = children[0] || null;" in script
    # the brief of a child is not enough to be the subject of the page: it carries
    # nothing the child itself contains, so the one box on screen is fetched whole
    assert "child = await api.box(child.uuid);" in script
    assert "renderContent(block, child, { depth: 1, embedded: false })" in script
    assert "Drag a box here from the panel on the left" in script
    assert ".root-empty" in client.get("/static/app.css").text


def test_the_root_view_accepts_a_drop(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'holder.addEventListener("drop"' in script
    assert "await api.link(box.uuid, dragged)" in script


def test_a_link_between_two_boxes_can_be_deleted(client: TestClient, populated: BoxStore) -> None:
    response = client.delete("/api/boxes/House/children/Passport")
    assert response.status_code == 204 or "Passport" not in [
        b.name for b in populated.children("House")
    ]
    # Kitchen holds Passport; removing House -> Kitchen leaves both boxes alive
    assert client.delete("/api/boxes/House/children/Kitchen").status_code == 204
    assert populated.exists("Kitchen")
    assert populated.exists("House")
    assert [b.name for b in populated.children("House")] == ["Garage"]


def test_deleting_an_unknown_link_is_not_found(client: TestClient) -> None:
    assert client.delete("/api/boxes/Ghost/children/Books").status_code == 404


def test_grid_cells_render_child_content_to_a_bounded_depth(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "MAX_EMBED_DEPTH" in script
    assert "class" in script and 'cell.classList.add("occupied")' in script
    assert "renderContent(content, child, { depth: childDepth, cell: true })" in script
    assert ".cell-content" in client.get("/static/app.css").text


def test_embedded_views_drop_their_toolbars(client: TestClient) -> None:
    """A folder in a cell has no room to change its own layout controls."""
    script = client.get("/static/app.js").text
    assert "opts.embedded ? [] : [toggle, add]" in script
    assert "if (!opts.embedded) canvas.append(gridToolbar(box, options, ctx));" in script


def test_the_modal_can_be_opened_with_a_parent(client: TestClient) -> None:
    assert "function openNewBoxModal(options = {})" in client.get("/static/app.js").text


# -- root -------------------------------------------------------------------


def test_root_cannot_be_filed(client: TestClient, populated: BoxStore) -> None:
    populated.add(Box(name="root", box_type="root"))
    response = client.post("/api/boxes/House/children", json={"child": "root"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "root_cannot_be_filed"


def test_the_types_endpoint_marks_the_root(client: TestClient) -> None:
    types = {entry["key"]: entry for entry in client.get("/api/types").json()}
    assert types["root"]["is_root"] is True
    assert types["folder"]["is_root"] is False


def test_the_side_panel_pins_the_root(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "function orderedForSide(boxes)" in script
    assert "orderedForSide(state.boxes)" in script


# -- the grid view ----------------------------------------------------------


def test_the_grid_view_is_registered(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'registerView("grid"' in script
    assert "GRID_DEFAULTS" in script
    for endpoint in ("grid/place", "grid/unplace", "grid/resize"):
        assert endpoint in script


def test_grid_cells_are_drawn_from_the_config(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "gridTemplateRows" in script
    assert "gridTemplateColumns" in script
    assert "cell.dataset.row" in script and "cell.dataset.column" in script


def test_the_grid_has_absolute_and_stepped_controls(client: TestClient) -> None:
    """Both ways of setting rows and columns: type a number, or step it."""
    script = client.get("/static/app.js").text
    assert 'stepper("rows"' in script
    assert 'stepper("columns"' in script
    assert '"grid-rows"' in script and '"grid-columns"' in script
    for suffix in ("-minus", "-plus", "-value"):
        assert suffix in script, suffix
    assert 'action.endsWith("-minus")' in script
    assert 'action === "grid-rows-value"' in script
    assert 'action === "grid-columns-value"' in script
    assert 'input.type = "number"' in script
    assert 'input.min = "1"' in script and "input.max = String(max)" in script


def test_grid_cells_accept_drops_and_open_on_double_click(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert '"dragover"' in script
    assert '"drop"' in script
    assert "drop-target" in script
    assert 'cell.addEventListener("dblclick"' in script
    assert '"contextmenu"' in script


def test_typed_grid_sizes_apply_on_enter_too(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'bar.addEventListener("keydown"' in script
    assert 'event.key === "Enter"' in script


# -- grid endpoints ---------------------------------------------------------


@pytest.fixture
def board(populated: BoxStore) -> Box:
    """A 2x2 grid to place things in."""
    populated.add(Box(name="Board", box_type="grid"))
    box = populated.get("Board")
    box.config = {"grid": {"rows": 2, "columns": 2, "cells": {}}}
    return populated.update(box)


def grid_of(client: TestClient, ref: str = "Board") -> dict:
    return client.get(f"/api/boxes/{ref}").json()["config"]["grid"]


def test_placing_a_child_in_a_cell(client: TestClient, populated: BoxStore, board: Box) -> None:
    child = populated.get("Passport").uuid
    response = client.post(
        "/api/boxes/Board/grid/place", json={"child": "Passport", "cell": [0, 1]}
    )
    assert response.status_code == 200
    assert grid_of(client)["cells"] == {"0,1": child}


def test_placing_files_the_box_if_it_was_not_a_child(
    client: TestClient, populated: BoxStore, board: Box
) -> None:
    before = [box.name for box in populated.children("Board")]
    assert "Books" not in before
    client.post("/api/boxes/Board/grid/place", json={"child": "Books", "cell": [1, 0]})
    assert "Books" in [box.name for box in populated.children("Board")]


def test_two_children_swap_cells(client: TestClient, populated: BoxStore, board: Box) -> None:
    client.post("/api/boxes/Board/grid/place", json={"child": "Passport", "cell": [0, 0]})
    client.post("/api/boxes/Board/grid/place", json={"child": "Books", "cell": [1, 1]})
    client.post("/api/boxes/Board/grid/place", json={"child": "Passport", "cell": [1, 1]})
    cells = grid_of(client)["cells"]
    uuids = {box.name: box.uuid for box in populated.list_boxes()}
    assert cells == {"1,1": uuids["Passport"], "0,0": uuids["Books"]}


def test_a_child_dragged_in_displaces_the_occupant(
    client: TestClient, populated: BoxStore, board: Box
) -> None:
    client.post("/api/boxes/Board/grid/place", json={"child": "Passport", "cell": [0, 0]})
    client.post("/api/boxes/Board/grid/place", json={"child": "Books", "cell": [0, 0]})
    cells = grid_of(client)["cells"]
    uuids = {box.name: box.uuid for box in populated.list_boxes()}
    assert cells["0,0"] == uuids["Books"]
    assert cells["0,1"] == uuids["Passport"]


def test_a_cell_outside_the_grid_is_refused(client: TestClient, board: Box) -> None:
    response = client.post(
        "/api/boxes/Board/grid/place", json={"child": "Passport", "cell": [7, 7]}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "bad_grid_move"


def test_resizing_unplaces_what_no_longer_fits_but_keeps_the_box(
    client: TestClient, populated: BoxStore, board: Box
) -> None:
    client.post("/api/boxes/Board/grid/place", json={"child": "Passport", "cell": [1, 1]})
    response = client.post("/api/boxes/Board/grid/resize", json={"rows": 1, "columns": 1})
    assert response.status_code == 200
    assert response.json()["config"]["grid"]["cells"] == {}
    assert response.json()["unplaced"] == [populated.get("Passport").uuid]
    assert "Passport" in [box.name for box in populated.children("Board")]


def test_an_absurd_grid_size_is_rejected(client: TestClient, board: Box) -> None:
    assert (
        client.post("/api/boxes/Board/grid/resize", json={"rows": 99, "columns": 2}).status_code
        == 422
    )


def test_unplacing_leaves_the_box_in_the_graph(
    client: TestClient, populated: BoxStore, board: Box
) -> None:
    client.post("/api/boxes/Board/grid/place", json={"child": "Passport", "cell": [0, 0]})
    response = client.post("/api/boxes/Board/grid/unplace", json={"child": "Passport"})
    assert response.status_code == 200
    assert response.json()["config"]["grid"]["cells"] == {}
    assert "Passport" in [box.name for box in populated.children("Board")]


def test_a_new_box_can_be_created_straight_into_a_cell(
    client: TestClient, populated: BoxStore, board: Box
) -> None:
    created = client.post(
        "/api/boxes", json={"name": "Fresh", "parent": "Board", "cell": [1, 0]}
    ).json()
    assert grid_of(client)["cells"] == {"1,0": created["uuid"]}
    assert created["config"] == {}


def test_grid_endpoints_on_an_unknown_box_are_not_found(client: TestClient) -> None:
    assert (
        client.post("/api/boxes/Ghost/grid/place", json={"child": "x", "cell": [0, 0]}).status_code
        == 404
    )
    assert (
        client.post("/api/boxes/Ghost/grid/resize", json={"rows": 2, "columns": 2}).status_code
        == 404
    )


# -- the root button --------------------------------------------------------


def test_the_side_panel_has_a_root_button(client: TestClient) -> None:
    page = client.get("/")
    assert 'id="go-root"' in page.text
    assert "🌲 Root" in page.text


def test_the_root_button_asks_the_server_for_the_root_box(client: TestClient) -> None:
    """A filtered or short panel must not decide where the Root button goes."""
    script = client.get("/static/app.js").text
    assert "async function goToRoot()" in script
    assert "async function findRoot()" in script
    assert "return await api.root();" in script
    # a library with no root is said, not guessed at
    assert '"no root box in this library — run `box init`"' in script
    assert 'el("go-root").addEventListener("click", goToRoot);' in script


# -- type proposals for a resource ------------------------------------------


def test_the_api_proposes_a_more_specific_type(client: TestClient) -> None:
    body = client.get(
        "/api/types/suggest", params={"resource_uri": "/tmp/report.pdf", "current": "file"}
    ).json()
    assert body["suggestion"]["key"] == "pdf"
    assert ".pdf" in body["suggestion"]["reason"]


def test_the_api_proposes_nothing_when_the_choice_is_right(client: TestClient) -> None:
    body = client.get(
        "/api/types/suggest", params={"resource_uri": "/tmp/a.png", "current": "image"}
    ).json()
    assert body["suggestion"] is None


def test_the_api_proposes_a_usable_type_when_the_choice_would_fail(client: TestClient) -> None:
    body = client.get(
        "/api/types/suggest", params={"resource_uri": "https://x.test/y", "current": "mqtt"}
    ).json()
    assert body["suggestion"]["key"] == "web"


def test_the_modal_offers_the_proposal(client: TestClient) -> None:
    assert 'id="nb-suggest"' in client.get("/").text
    script = client.get("/static/app.js").text
    assert "accept-suggestion" in script
    assert "api.suggestType" in script
    assert "el(modal.type).value = suggestion.key" in script  # both dialogs share it


def test_a_hidden_element_stays_hidden_under_display_flex(client: TestClient) -> None:
    """`.suggest { display: flex }` would otherwise beat the `hidden` attribute."""
    css = client.get("/static/app.css").text
    assert ".suggest[hidden] { display: none; }" in css


# -- the miniature a box uses as its icon -----------------------------------


@pytest.fixture
def picture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from PIL import Image

    from boxganizer import config

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    source = tmp_path / "shot.png"
    Image.new("RGB", (600, 150), (30, 90, 200)).save(source)
    return source


def test_creating_an_image_box_gives_it_a_picture_icon(client: TestClient, picture: Path) -> None:
    created = client.post(
        "/api/boxes", json={"name": "Shot", "box_type": "image", "resource_uri": str(picture)}
    ).json()
    assert created["config"]["icon_source"] == "thumbnail"


def test_the_thumbnail_endpoint_serves_a_png(client: TestClient, picture: Path) -> None:
    created = client.post(
        "/api/boxes", json={"name": "Shot", "box_type": "image", "resource_uri": str(picture)}
    ).json()
    response = client.get(f"/api/boxes/{created['uuid']}/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:4] == b"\x89PNG"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_thumbnail_can_be_fetched_by_name(client: TestClient, picture: Path) -> None:
    client.post(
        "/api/boxes", json={"name": "Shot", "box_type": "image", "resource_uri": str(picture)}
    )
    assert client.get("/api/boxes/Shot/thumbnail").status_code == 200


def test_a_box_without_a_miniature_answers_not_found(client: TestClient) -> None:
    response = client.get("/api/boxes/Kitchen/thumbnail")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "no_thumbnail"


def test_an_explicit_icon_is_not_replaced_by_a_miniature(client: TestClient, picture: Path) -> None:
    created = client.post(
        "/api/boxes",
        json={"name": "Chosen", "box_type": "image", "resource_uri": str(picture), "icon": "🐧"},
    ).json()
    assert created["icon"] == "🐧"
    assert "icon_source" not in created["config"]


# -- PDF boxes ---------------------------------------------------------------


@pytest.fixture
def document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from PIL import Image

    from boxganizer import config

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    doc = tmp_path / "report.pdf"
    Image.new("RGB", (600, 800), "white").save(doc, "PDF", resolution=80.0)
    return doc


def test_a_pdf_box_gets_a_page_miniature_icon(client: TestClient, document: Path) -> None:
    created = client.post(
        "/api/boxes", json={"name": "Report", "box_type": "pdf", "resource_uri": str(document)}
    ).json()
    assert created["config"]["icon_source"] == "thumbnail"
    thumb = client.get(f"/api/boxes/{created['uuid']}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.content[:4] == b"\x89PNG"


def test_the_pdf_itself_is_served_as_a_pdf(client: TestClient, document: Path) -> None:
    created = client.post(
        "/api/boxes", json={"name": "Report", "box_type": "pdf", "resource_uri": str(document)}
    ).json()
    raw = client.get(f"/api/boxes/{created['uuid']}/raw")
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "application/pdf"
    assert raw.content[:5] == b"%PDF-"


def test_the_types_endpoint_says_what_a_box_is_iconed_from(client: TestClient) -> None:
    types = {entry["key"]: entry for entry in client.get("/api/types").json()}
    assert types["pdf"]["thumbnail_kind"] == "pdf-page"
    assert types["image"]["thumbnail_kind"] == "image"
    assert types["folder"]["thumbnail_kind"] is None


def test_the_pdf_view_embeds_the_browser_viewer(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'registerView("pdf"' in script
    assert "#view=FitH" in script
    assert "pdf-frame" in script
    assert "box-icon--page" in client.get("/static/app.css").text


# -- the directory explorer --------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A small directory tree, plus an archive and a video to open."""
    import zipfile

    from boxganizer import config

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    root = tmp_path / "files"
    (root / "photos").mkdir(parents=True)
    (root / "notes.md").write_text("# Notes\n")
    (root / "clip.mp4").write_bytes(b"\x00" * 32)
    (root / "photos" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / ".hidden").write_text("no")
    with zipfile.ZipFile(root / "bundle.zip", "w") as archive:
        archive.writestr("inner/hello.txt", "hello")
    return root


def _dir_box(client: TestClient, tree: Path) -> dict:
    return client.post(
        "/api/boxes", json={"name": "Files", "box_type": "directory", "resource_uri": str(tree)}
    ).json()


def test_a_directory_box_lists_its_files(client: TestClient, tree: Path) -> None:
    box = _dir_box(client, tree)
    listing = client.get(f"/api/boxes/{box['uuid']}/listing").json()
    names = [entry["name"] for entry in listing["entries"]]
    assert names == ["photos", "bundle.zip", "clip.mp4", "notes.md"]  # folders first
    assert ".hidden" not in names


def test_listing_reports_the_type_each_file_would_become(client: TestClient, tree: Path) -> None:
    entries = {
        entry["name"]: entry
        for entry in client.get(f"/api/boxes/{_dir_box(client, tree)['uuid']}/listing").json()[
            "entries"
        ]
    }
    assert entries["notes.md"]["box_type"] == "markdown"
    assert entries["clip.mp4"]["box_type"] == "video"
    assert entries["bundle.zip"]["box_type"] == "archive"
    assert entries["photos"]["box_type"] == "directory"
    assert entries["clip.mp4"]["kind"] == "video"


def test_the_explorer_navigates_into_a_subdirectory(client: TestClient, tree: Path) -> None:
    box = _dir_box(client, tree)
    sub = client.get(f"/api/boxes/{box['uuid']}/listing", params={"sub": "photos"}).json()
    assert [entry["sub"] for entry in sub["entries"]] == ["photos/a.png"]
    assert sub["parent"] == ""


def test_a_path_cannot_climb_out_of_the_box_directory(client: TestClient, tree: Path) -> None:
    box = _dir_box(client, tree)
    for attempt in ("../secrets", "/etc", "photos/../../etc"):
        response = client.get(f"/api/boxes/{box['uuid']}/listing", params={"sub": attempt})
        assert response.status_code == 403, attempt
        assert response.json()["detail"]["error"] == "path_out_of_range"


def test_a_file_inside_the_directory_is_served_for_preview(client: TestClient, tree: Path) -> None:
    box = _dir_box(client, tree)
    response = client.get(f"/api/boxes/{box['uuid']}/content", params={"sub": "notes.md"})
    assert response.status_code == 200
    assert response.text == "# Notes\n"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_listing_is_refused_for_a_box_that_is_not_a_directory(client: TestClient) -> None:
    box = client.post("/api/boxes", json={"name": "Plain", "box_type": "folder"}).json()
    response = client.get(f"/api/boxes/{box['uuid']}/listing")
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "not_a_directory_box"


def test_an_archive_lists_its_members(client: TestClient, tree: Path) -> None:
    box = _dir_box(client, tree)
    payload = client.get(f"/api/boxes/{box['uuid']}/archive", params={"sub": "bundle.zip"}).json()
    assert [entry["sub"] for entry in payload["entries"]] == ["inner/hello.txt"]


def test_an_unreadable_archive_is_an_error_not_a_crash(client: TestClient, tree: Path) -> None:
    (tree / "broken.zip").write_text("this is not a zip")
    box = _dir_box(client, tree)
    response = client.get(f"/api/boxes/{box['uuid']}/archive", params={"sub": "broken.zip"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "cannot_list_archive"


def test_video_files_are_inferred_as_video(client: TestClient, tree: Path) -> None:
    # A real file: a type's own check requires it to exist, so inference only
    # proposes types that would actually accept the resource.
    assert (
        client.get("/api/types/suggest", params={"resource_uri": str(tree / "clip.mp4")}).json()[
            "suggestion"
        ]["key"]
        == "video"
    )


def test_a_listing_entry_carries_the_absolute_path(client: TestClient, tree: Path) -> None:
    """Right-click -> "new box" needs a real path, not a path relative to the box."""
    box = _dir_box(client, tree)
    entries = client.get(f"/api/boxes/{box['uuid']}/listing").json()["entries"]
    by_name = {entry["name"]: entry for entry in entries}
    assert by_name["notes.md"]["path"] == str(tree / "notes.md")
    # Archive members are not files on disk, so they carry no path.
    archive = client.get(f"/api/boxes/{box['uuid']}/archive", params={"sub": "bundle.zip"}).json()
    assert archive["entries"][0]["path"] is None


def test_creating_a_box_from_a_listed_file_keeps_the_resource(
    client: TestClient, tree: Path
) -> None:
    parent = _dir_box(client, tree)
    created = client.post(
        "/api/boxes",
        json={
            "name": "notes",
            "box_type": "markdown",
            "resource_uri": str(tree / "notes.md"),
            "parent": parent["uuid"],
        },
    )
    assert created.status_code == 201
    box = client.get(f"/api/boxes/{created.json()['uuid']}").json()
    assert box["resource_uri"] == str(tree / "notes.md")
    assert [entry["uuid"] for entry in box["parents"]] == [parent["uuid"]]


def test_the_explorer_front_end_navigates_and_previews(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        "const directoryView",
        "await api.listing(box.uuid, nav.sub)",
        "api.contentUrl(box.uuid, entry.sub)",
        'registerView("video"',
        'registerView("archive"',
        "function formatSize",
        'registerView("file"',
        "function unableMessage",
        "const VIEW_BY_KIND",
    ):
        assert needle in script, needle
    assert "fs-row" in client.get("/static/app.css").text


# -- modify a box (UI 1) and remove a child from a folder (FolderResource) ----


def test_a_box_can_be_modified_through_the_api(client: TestClient, tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# hi")
    created = client.post(
        "/api/boxes",
        json={"name": "Note", "box_type": "text", "resource_uri": str(tmp_path / "note.txt")},
    ).json()
    retyped = client.put(
        f"/api/boxes/{created['uuid']}", json={"resource_uri": str(note), "box_type": "markdown"}
    )
    assert retyped.status_code == 200
    assert retyped.json()["box_type"] == "markdown"


def test_retyping_respects_the_new_types_rules(client: TestClient) -> None:
    created = client.post("/api/boxes", json={"name": "Loose", "box_type": "box"}).json()
    refused = client.put(f"/api/boxes/{created['uuid']}", json={"box_type": "pdf"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "invalid_for_type"


def test_retyping_a_parent_that_holds_boxes_is_refused(client: TestClient) -> None:
    parent = client.post("/api/boxes", json={"name": "Holding", "box_type": "folder"}).json()
    child = client.post("/api/boxes", json={"name": "Held", "box_type": "box"}).json()
    client.post(f"/api/boxes/{parent['uuid']}/children", json={"child": child["uuid"]})
    png = parent["uuid"][:4] + ".png"  # a valid extension is not enough to earn children
    refused = client.put(
        f"/api/boxes/{parent['uuid']}", json={"box_type": "image", "resource_uri": f"/tmp/{png}"}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "children_not_allowed"


def test_renaming_onto_another_box_is_refused_by_name(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "Occupied", "box_type": "box"})
    created = client.post("/api/boxes", json={"name": "Renamable", "box_type": "box"}).json()
    response = client.put(f"/api/boxes/{created['uuid']}", json={"name": "occupied"})
    assert response.status_code == 409
    assert response.json()["detail"] == {"error": "duplicate_name", "name": "occupied"}


def test_an_emptied_description_and_icon_do_not_become_null(
    client: TestClient, tmp_path: Path
) -> None:
    note = tmp_path / "spaced.txt"
    note.write_text("x")
    created = client.post(
        "/api/boxes",
        json={
            "name": "Spaced",
            "box_type": "text",
            "resource_uri": str(note),
            "description": "hello",
            "icon": "⭐",
        },
    ).json()
    assert "uuid" in created, f"create failed: {created}"
    saved = client.put(
        f"/api/boxes/{created['uuid']}", json={"description": "   ", "icon": "  "}
    ).json()
    assert saved["description"] == ""
    assert saved["icon"] == "🔤"  # the text type's own glyph


def test_the_modify_dialog_cannot_forge_a_uuid(client: TestClient) -> None:
    """`uuid` is not a writable field: it is not even accepted."""
    created = client.post("/api/boxes", json={"name": "Forged", "box_type": "box"}).json()
    response = client.put(
        f"/api/boxes/{created['uuid']}", json={"name": "Forged", "uuid": "someone-elses"}
    )
    assert response.status_code in (200, 422)  # ignored or refused, never applied
    assert client.get(f"/api/boxes/{created['uuid']}").json()["uuid"] == created["uuid"]


def test_the_dialog_can_modify_a_box(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    html = client.get("/").text
    for needle in (
        'id="edit-box-modal"',
        'id="eb-type"',
        'id="eb-icon"',
        "function openEditBoxModal(box)",
        "async function submitEditBox(event)",
        "api.updateBox(",
        'label: "Modify…"',
    ):
        assert needle in (script + html), needle
    # the two dialogs share one field machine, so create still works unchanged
    assert "const CREATE_MODAL" in script and "const EDIT_MODAL" in script


def test_a_folder_child_can_be_removed_from_its_folder(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "async function removeFrom(parent, child)" in script
    assert "Remove from ${parent.name}" in script
    assert "removeFrom," in script  # handed to views through ctx


# -- the resource file selector (UI 2) ---------------------------------------


def test_the_file_selector_starts_at_home_when_unconfined(client: TestClient) -> None:
    payload = client.get("/api/fs/browse").json()
    assert Path(payload["path"]) == Path.home()
    assert payload["parent"] is None  # nothing above the confine
    assert payload["root"] == str(Path.home())


@pytest.fixture
def picker_client(tmp_path: Path) -> Iterator[TestClient]:
    """A server whose content root is the test directory, so the picker may walk it."""
    picker_store = BoxStore(tmp_path / "picker.db")
    try:
        with TestClient(create_app(picker_store, content_root=tmp_path)) as test_client:
            yield test_client
    finally:
        picker_store.close()


def test_the_file_selector_walks_and_filters(picker_client: TestClient, tree: Path) -> None:
    sub = picker_client.get("/api/fs/browse", params={"path": str(tree / "photos")}).json()
    assert [entry["name"] for entry in sub["entries"]] == ["a.png"]
    assert sub["parent"] == str(tree)


def test_the_file_selector_hides_dot_files_unless_asked(
    picker_client: TestClient, tree: Path
) -> None:
    names = [
        e["name"]
        for e in picker_client.get("/api/fs/browse", params={"path": str(tree)}).json()["entries"]
    ]
    assert ".hidden" not in names
    shown = [
        e["name"]
        for e in picker_client.get(
            "/api/fs/browse", params={"path": str(tree), "show_hidden": "true"}
        ).json()["entries"]
    ]
    assert ".hidden" in shown


def test_the_file_selector_refuses_to_climb_above_its_confine(client: TestClient) -> None:
    for attempt in ("/etc", "../../etc", str(Path.home().parent)):
        response = client.get("/api/fs/browse", params={"path": attempt})
        assert response.status_code == 403, attempt
        assert response.json()["detail"]["error"] == "path_out_of_range"


def test_the_file_selector_reads_tilde_and_relative_paths_against_the_confine(
    client: TestClient,
) -> None:
    assert client.get("/api/fs/browse", params={"path": "~"}).json()["path"] == str(Path.home())


def test_a_missing_or_non_directory_path_is_an_honest_error(
    picker_client: TestClient, tree: Path
) -> None:
    missing = picker_client.get("/api/fs/browse", params={"path": str(tree / "nope")})
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "no_such_path"
    not_a_dir = picker_client.get("/api/fs/browse", params={"path": str(tree / "notes.md")})
    assert not_a_dir.status_code == 409
    assert not_a_dir.json()["detail"]["error"] == "not_a_directory"


def test_the_picker_is_confined_to_the_content_root(
    picker_client: TestClient, tmp_path: Path
) -> None:
    response = picker_client.get("/api/fs/browse", params={"path": str(tmp_path.parent)})
    assert response.status_code == 403
    assert response.json()["detail"]["root"] == str(tmp_path)


def test_the_resource_field_offers_the_selector(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/static/app.js").text
    assert 'id="file-picker"' in html
    # both dialogs offer it, and it writes back into the field that asked
    assert html.count('class="browse" data-resource="nb-resource"') == 1
    assert html.count('class="browse" data-resource="eb-resource"') == 1
    for needle in (
        "async function openFilePicker(target, startValue)",
        "async function showIn(path)",
        "function pick(chosen)",
        "api.browse(",
        'dispatchEvent(new Event("input"',
    ):
        assert needle in script, needle


def test_the_picker_opens_on_what_the_field_already_holds(client: TestClient) -> None:
    """A directory resource opens on itself, a file on its folder, a refusal falls back."""
    script = client.get("/static/app.js").text
    for needle in (
        "if (start && (await showIn(start))) return;",
        'const parent = start.replace(/\\/[^/]*\\/?$/, "");',
        'await showIn("");',
        "the picker is confined to",
        "error.detail && error.detail.error ===",
    ):
        assert needle in script, needle


# -- the browser pane (BrowserResource) --------------------------------------


def test_the_browser_view_frames_the_address_it_is_given(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        "const browserView",
        'registerView("browser", browserView)',
        'registerView("web", browserView)',
        'frame.className = "browser-frame"',
        "allow-scripts allow-same-origin allow-forms allow-popups",
        "refuses to be shown inside another page",
    ):
        assert needle in script, needle
    # the frame may not push the app itself somewhere else: check the sandbox
    # value itself, not the whole file, since the comment about it names the flag
    sandbox_line = next(
        line for line in script.splitlines() if "allow-scripts allow-same-origin" in line
    )
    assert "allow-top-navigation" not in sandbox_line, sandbox_line
    assert ".browser-frame" in client.get("/static/app.css").text


def test_the_browser_box_can_keep_a_new_address(client: TestClient) -> None:
    created = client.post(
        "/api/boxes",
        json={"name": "Docs", "box_type": "browser", "resource_uri": "https://a.test/"},
    )
    assert created.status_code == 201
    moved = client.put("/api/boxes/Docs", json={"resource_uri": "https://b.test/page"}).json()
    assert moved["resource_uri"] == "https://b.test/page"
    # the type still insists on http(s)
    refused = client.put("/api/boxes/Docs", json={"resource_uri": "ftp://nope/"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "invalid_for_type"


# -- the desktop view ---------------------------------------------------------


def test_the_desktop_view_is_registered(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert 'registerView("desktop"' in script
    assert "DESKTOP_DEFAULTS" in script
    for endpoint in ("desktop/move", "desktop/resize"):
        assert endpoint in script


def test_the_desktop_draws_icons_where_the_box_says(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "function desktopIcon(" in script
    assert "surface.append(desktopIcon(" in script
    assert "tile.style.left" in script and "tile.style.top" in script
    assert "tile.style.width" in script and "tile.style.height" in script


def test_desktop_icons_are_dragged_with_pointer_events_and_open_on_double_click(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text
    assert '"pointerdown"' in script
    assert '"pointermove"' in script
    assert "setPointerCapture" in script
    assert "touch-action: none" in client.get("/static/app.css").text
    assert 'tile.addEventListener("dblclick"' in script


def test_the_desktop_toolbar_offers_the_magnet_and_the_grid_size(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert '"desktop-snap"' in script
    assert '"desktop-grid-minus"' in script and '"desktop-grid-plus"' in script
    assert '"desktop-grid-value"' in script
    assert 'ctx.saveSection("desktop"' in script
    # The page rounds the same way the core does, so an icon does not jump on save.
    assert "Math.floor(value / cell + 0.5) * cell" in script


def test_the_desktop_wallpaper_is_only_claimed_once_it_loads(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "has-background" in script
    assert "probe.onload" in script
    assert "--desk-bg" in client.get("/static/app.css").text


# -- desktop endpoints --------------------------------------------------------


@pytest.fixture
def desk(populated: BoxStore) -> Box:
    """A desktop with two children filed in it."""
    populated.add(Box(name="Surface", box_type="desktop"))
    populated.link("Surface", "Passport")
    populated.link("Surface", "Garage")
    return populated.get("Surface")


def desktop_of(client: TestClient, ref: str = "Surface") -> dict:
    return client.get(f"/api/boxes/{ref}").json()["config"]["desktop"]


def test_placing_an_icon_records_where_it_lands(
    client: TestClient, populated: BoxStore, desk: Box
) -> None:
    child = populated.get("Passport").uuid
    response = client.post(
        "/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": 40, "y": 60}
    )
    assert response.status_code == 200
    assert response.json()["rect"] == {"x": 40, "y": 60, "width": 96, "height": 96}
    assert desktop_of(client)["positions"] == {child: {"x": 40, "y": 60, "width": 96, "height": 96}}


def test_placing_an_icon_files_a_box_that_was_only_dropped_from_the_pane(
    client: TestClient, populated: BoxStore, desk: Box
) -> None:
    assert "Books" not in [box.name for box in populated.children("Surface")]
    client.post("/api/boxes/Surface/desktop/move", json={"child": "Books", "x": 0, "y": 0})
    assert "Books" in [box.name for box in populated.children("Surface")]


def test_the_magnet_pulls_the_icon_to_the_nearest_line(
    client: TestClient, populated: BoxStore, desk: Box
) -> None:
    client.post("/api/boxes/Surface/desktop/settings", json={"grid": 32, "snap": True})
    response = client.post(
        "/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": 45, "y": 45}
    )
    rect = response.json()["rect"]
    assert (rect["x"], rect["y"]) == (32, 32)
    assert desktop_of(client)["snap"] is True


def test_with_the_magnet_off_the_icon_stays_under_the_pointer(
    client: TestClient, populated: BoxStore, desk: Box
) -> None:
    client.post("/api/boxes/Surface/desktop/settings", json={"grid": 32, "snap": False})
    response = client.post(
        "/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": 45, "y": 45}
    )
    assert (response.json()["rect"]["x"], response.json()["rect"]["y"]) == (45, 45)


def test_the_grid_size_is_a_setting_of_the_desktop(client: TestClient, desk: Box) -> None:
    client.post("/api/boxes/Surface/desktop/settings", json={"grid": 64, "snap": True})
    assert desktop_of(client)["grid"] == 64
    response = client.post(
        "/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": 100, "y": 10}
    )
    assert (response.json()["rect"]["x"], response.json()["rect"]["y"]) == (128, 0)


def test_changing_the_magnet_does_not_scatter_what_is_already_placed(
    client: TestClient, populated: BoxStore, desk: Box
) -> None:
    client.post("/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": 45, "y": 45})
    client.post("/api/boxes/Surface/desktop/settings", json={"grid": 16, "snap": True})
    assert desktop_of(client)["positions"][populated.get("Passport").uuid]["x"] == 45


def test_sizing_an_icon_leaves_it_where_it_was(
    client: TestClient, populated: BoxStore, desk: Box
) -> None:
    child = populated.get("Passport").uuid
    client.post("/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": 64, "y": 128})
    client.post(
        "/api/boxes/Surface/desktop/resize", json={"child": "Passport", "width": 200, "height": 120}
    )
    assert desktop_of(client)["positions"][child] == {
        "x": 64,
        "y": 128,
        "width": 200,
        "height": 120,
    }


def test_the_wallpaper_a_desktop_points_at_is_served(
    client: TestClient, populated: BoxStore, tmp_path: Path
) -> None:
    """`serves_content` is what lets the page paint the resource behind the icons."""
    picture = tmp_path / "wall.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pixels")
    populated.add(Box(name="Paned", box_type="desktop", resource_uri=str(picture)))
    response = client.get("/api/boxes/Paned/raw")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_a_coordinate_below_zero_is_refused(client: TestClient, desk: Box) -> None:
    response = client.post(
        "/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": -5, "y": 0}
    )
    assert response.status_code == 422


def test_a_coordinate_that_is_not_a_number_is_refused(client: TestClient, desk: Box) -> None:
    response = client.post(
        "/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": "left", "y": 0}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("grid", [3, 257, 4096])
def test_a_grid_size_nobody_would_use_is_refused(client: TestClient, desk: Box, grid: int) -> None:
    assert (
        client.post(
            "/api/boxes/Surface/desktop/settings", json={"grid": grid, "snap": True}
        ).status_code
        == 422
    )


def test_two_icons_on_a_desktop_are_placed_independently(
    client: TestClient, populated: BoxStore, desk: Box
) -> None:
    client.post("/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": 0, "y": 0})
    client.post("/api/boxes/Surface/desktop/move", json={"child": "Garage", "x": 200, "y": 40})
    positions = desktop_of(client)["positions"]
    assert positions[populated.get("Passport").uuid]["x"] == 0
    assert positions[populated.get("Garage").uuid] == {
        "x": 200,
        "y": 40,
        "width": 96,
        "height": 96,
    }


def test_removing_a_child_from_the_desktop_takes_only_the_link(
    client: TestClient, populated: BoxStore, desk: Box
) -> None:
    child = populated.get("Passport").uuid
    client.post("/api/boxes/Surface/desktop/move", json={"child": "Passport", "x": 10, "y": 10})
    client.delete(f"/api/boxes/Surface/children/{child}")
    assert "Passport" not in [box.name for box in populated.children("Surface")]
    assert populated.get("Passport").name == "Passport"


# -- the mqtt sensor (MQTTResource) ------------------------------------------

SENSOR_ADDRESS = "mqtt://watcher:secretpass@cellar.example:1883"


@pytest.fixture
def sensor(populated: BoxStore) -> Box:
    """A box that listens to a broker. No test below dials it."""
    return populated.add(
        Box(
            name="Cellar temp",
            box_type="mqtt",
            resource_uri=SENSOR_ADDRESS,
            metadata={"topic": "cellar/temp"},
        )
    )


def _reading(value: str, *, retained: bool = False, encoding: str = "utf-8") -> mqtt.Reading:
    return mqtt.Reading(
        topic="cellar/temp",
        value=value,
        received_at=datetime(2026, 10, 25, 8, 4, 5, tzinfo=UTC),
        retained=retained,
        encoding=encoding,
    )


def _scripted(events: list[mqtt.Event]) -> mqtt.EventStream:
    """A broker replaced by a script: the events a test wrote, in order."""

    async def story(target: mqtt.Target) -> AsyncIterator[mqtt.Event]:
        for event in events:
            yield event

    return story


def test_the_sensor_says_what_it_listens_to(client: TestClient, sensor: Box) -> None:
    body = client.get("/api/boxes/Cellar temp/mqtt").json()
    assert body["target"] == {
        "scheme": "mqtt",
        "host": "cellar.example",
        "port": 1883,
        "topic": "cellar/temp",
        "secured": False,
        "credentials": True,
    }
    assert body["address"] == "mqtt://watcher:•••@cellar.example:1883"
    # Nothing has been heard yet, and the endpoint does not wait for a message.
    assert body["reading"] is None


def test_the_way_into_the_broker_stays_in_the_box(populated: BoxStore, sensor: Box) -> None:
    events = [mqtt.Event("value", _reading("21.4"))]
    with TestClient(create_app(populated, mqtt_events=_scripted(events))) as test_client:
        value = test_client.get("/api/boxes/Cellar temp/mqtt")
        stream = test_client.get("/api/boxes/Cellar temp/mqtt/stream")
    assert "secretpass" not in value.text
    assert "secretpass" not in stream.text


def test_a_box_that_listens_to_nothing_is_refused(client: TestClient) -> None:
    response = client.get("/api/boxes/Kitchen/mqtt")
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "not_a_live_box"


def test_an_address_that_names_no_host_is_said_in_words(populated: BoxStore) -> None:
    populated.add(
        Box(name="Broken", box_type="mqtt", resource_uri="mqtt://", metadata={"topic": "t"})
    )
    with TestClient(create_app(populated)) as test_client:
        response = test_client.get("/api/boxes/Broken/mqtt")
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "bad_mqtt_target"
    assert "no host" in response.json()["detail"]["reason"]


def test_the_stream_carries_every_state_and_every_value(populated: BoxStore, sensor: Box) -> None:
    events = [
        mqtt.Event("connecting"),
        mqtt.Event("live"),
        mqtt.Event("value", _reading("21.4")),
        mqtt.Event("value", _reading("21.6", retained=True)),
    ]
    with TestClient(create_app(populated, mqtt_events=_scripted(events))) as test_client:
        response = test_client.get("/api/boxes/Cellar temp/mqtt/stream")
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "event: connecting" in text
    assert "event: live" in text
    assert '"state": "value"' in text
    assert "21.4" in text and "21.6" in text
    assert '"retained": true' in text
    assert '"received_at": "2026-10-25T08:04:05+00:00"' in text


def test_the_last_value_is_kept_for_a_page_that_opens_afterwards(
    populated: BoxStore, sensor: Box
) -> None:
    events = [mqtt.Event("value", _reading("21.4"))]
    with TestClient(create_app(populated, mqtt_events=_scripted(events))) as test_client:
        test_client.get("/api/boxes/Cellar temp/mqtt/stream")
        later = test_client.get("/api/boxes/Cellar temp/mqtt").json()
    assert later["reading"]["value"] == "21.4"


def test_a_broker_that_refuses_arrives_as_an_error_event(populated: BoxStore, sensor: Box) -> None:
    async def refusing(target: mqtt.Target) -> AsyncIterator[mqtt.Event]:
        yield mqtt.Event("connecting")
        raise MqttError("connection refused: bad username or password")

    with TestClient(create_app(populated, mqtt_events=refusing)) as test_client:
        text = test_client.get("/api/boxes/Cellar temp/mqtt/stream").text
    assert "event: error" in text
    assert "bad username or password" in text


def test_the_sensor_view_shows_the_value_and_listens_for_more(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        'registerView("mqtt", mqttView)',
        'request(`/api/boxes/${encodeURIComponent(ref)}/mqtt`)',
        "/mqtt/stream",
        "new EventSource(",
        'value.className = "mqtt-value"',
        "listening",
        "log.prepend(line)",
        "while (log.children.length > MQTT_HISTORY)",
    ):
        assert needle in script, needle
    css = client.get("/static/app.css").text
    assert ".mqtt-view" in css
    assert '.mqtt-state[data-state="live"]' in css


def test_the_sensor_view_holds_the_password_back_and_the_connection_short(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text
    # The address it was given is already redacted; the page says so rather than
    # showing a secret it never received.
    assert "the user and password stay in the box" in script
    # A cell shows one reading and says it is only what the server last heard; the
    # opened box is what holds a stream. The flag has to survive the trip through
    # renderContent, or a wall of sensors becomes a wall of subscriptions.
    assert 'if (opts.cell) {' in script
    assert 'chip.textContent = info.reading ? "last value" : "no value yet";' in script
    assert "renderContent(content, child, { depth: childDepth, cell: true })" in script
    assert "{ embedded, depth, cell }" in script
    # A dropped connection carries no body, and is a retry rather than a refusal.
    assert 'typeof event.data !== "string"' in script
    # And what a live box had open is closed when the page moves on.
    assert "function holdLive(" in script
    assert "function releaseLive()" in script
    assert "releaseLive();" in script
    assert script.index("function renderMain()") < script.index("releaseLive();")
    # A view that is mid-fetch when the pane moves on must abandon, not append.
    assert "function stillOnScreen(token)" in script
    assert "const token = paneGeneration;" in script
    assert "!stillOnScreen(token)" in script


# -- the root slot, and the desktop surface it can show -----------------------


def test_filing_into_the_root_through_the_api_replaces_the_slot(
    client: TestClient, populated: BoxStore
) -> None:
    """What a drop on the root does: the slot holds one box, and the old one lives on."""
    root = populated.add(Box(name="way-in", box_type="root"))
    first = client.post("/api/boxes", json={"name": "First"}).json()
    second = client.post("/api/boxes", json={"name": "Second"}).json()
    client.post(f"/api/boxes/{root.uuid}/children", json={"child": first["uuid"]})
    client.post(f"/api/boxes/{root.uuid}/children", json={"child": second["uuid"]})
    assert [c["name"] for c in client.get(f"/api/boxes/{root.uuid}").json()["children"]] == [
        "Second"
    ]
    # replaced, not deleted
    assert client.get("/api/boxes/First").status_code == 200


def test_a_desktop_holds_as_many_children_as_it_is_given(
    client: TestClient, populated: BoxStore
) -> None:
    desk = populated.add(Box(name="Wide desk", box_type="desktop"))
    for name in ("one", "two", "three"):
        created = client.post("/api/boxes", json={"name": name}).json()
        client.post(f"/api/boxes/{desk.uuid}/children", json={"child": created["uuid"]})
    names = [c["name"] for c in client.get(f"/api/boxes/{desk.uuid}").json()["children"]]
    assert sorted(names) == ["one", "three", "two"]


def test_the_root_shows_the_child_itself_and_keeps_what_a_child_took(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text
    # Asked as the subject of the page, not as a tile: a plain box answers an
    # embedded call with its icon, and a desktop with no toolbar of its own.
    assert "renderContent(block, child, { depth: 1, embedded: false })" in script
    # A drop the child's own view already took is not the root's to answer, or
    # dropping an icon on the desktop shown by the root would replace that desktop.
    assert "if (event.defaultPrevented) return;" in script


def test_the_desktop_surface_answers_right_click_with_its_background(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        'surface.addEventListener("contextmenu"',
        'if (event.target.closest(".desktop-icon")) return;',
        "Set a background image",
        "Remove the background image",
        "openFilePicker((chosen) => writeBackground(box, ctx, chosen)",
        "await api.updateBox(box.uuid, { resource_uri: chosen })",
    ):
        assert needle in script, needle
    # The picker answers a callback now, because a menu holds no field to fill.
    assert 'typeof picker.target === "function"' in script
    assert "picker.target(chosen);" in script


def test_a_desktops_background_is_its_resource_field(
    client: TestClient, populated: BoxStore
) -> None:
    desk = populated.add(Box(name="Desk two", box_type="desktop"))
    saved = client.put(
        f"/api/boxes/{desk.uuid}", json={"resource_uri": "/home/me/wall.png"}
    ).json()
    assert saved["resource_uri"] == "/home/me/wall.png"
    cleared = client.put(f"/api/boxes/{desk.uuid}", json={"resource_uri": ""}).json()
    assert not cleared["resource_uri"]


# -- the way in: /api/root and the landing page -------------------------------


def test_the_root_endpoint_answers_with_the_root_box(
    client: TestClient, populated: BoxStore
) -> None:
    root = populated.add(Box(name="way-in", box_type="root"))
    body = client.get("/api/root").json()
    assert body["uuid"] == root.uuid
    assert body["box_type"] == "root"


def test_the_root_endpoint_finds_it_past_the_end_of_the_box_page(
    client: TestClient, populated: BoxStore
) -> None:
    """250 boxes whose names sort ahead of the root: the page misses it, the endpoint does not."""
    root = populated.add(Box(name="root", box_type="root"))
    for index in range(250):
        populated.add(Box(name=f"a{index:03d}"))
    listed = [box["name"] for box in client.get("/api/boxes").json()]
    assert "root" not in listed
    assert client.get("/api/root").json()["uuid"] == root.uuid


def test_a_library_without_a_root_says_so(client: TestClient) -> None:
    response = client.get("/api/root")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "no_root_box"


def test_the_page_lands_on_the_root_not_the_first_name(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    # the root is asked of the server, which knows the graph...
    assert 'root: () => request("/api/root")' in script
    assert "async function findRoot()" in script
    # ...and the landing page starts from it, not from whatever the list begins with
    assert "const root = await findRoot();" in script
    assert 'state.boxes.find((box) => box.box_type === "root") || state.boxes[0]' not in script
    # the Root button asks the same question
    assert "async function goToRoot() {\n  const root = await findRoot();" in script


# -- the dynamic folder and the cabinet --------------------------------------


def test_a_folder_is_told_what_a_dynamic_folder_asks_for(client: TestClient) -> None:
    client.post(
        "/api/boxes",
        json={"name": "Paperwork", "box_type": "pdf", "resource_uri": "/tmp/p.pdf"},
    )
    created = client.post(
        "/api/boxes",
        json={
            "name": "All PDFs",
            "box_type": "dynamic-folder",
            "config": {"filter": {"type": "pdf"}},
        },
    )
    assert created.status_code == 201
    body = client.get("/api/boxes/All PDFs/filter").json()
    assert body["usable"] is True
    assert body["describes"] == "type is pdf or one below it"
    assert [m["name"] for m in body["members"]] == ["Paperwork"]


def test_a_plain_folder_is_not_asked_for_a_filter(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "Basket", "box_type": "folder"})
    response = client.get("/api/boxes/Basket/filter")
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "not_a_dynamic_folder"


def test_writing_a_filter_updates_the_link_table_through_the_api(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "note", "box_type": "text", "resource_uri": "/n.txt"})
    client.post("/api/boxes", json={"name": "doc", "box_type": "pdf", "resource_uri": "/d.pdf"})
    client.post(
        "/api/boxes",
        json={"name": "Docs", "box_type": "dynamic-folder", "config": {"filter": {"type": "pdf"}}},
    )
    assert [m["name"] for m in client.get("/api/boxes/Docs/filter").json()["members"]] == ["doc"]

    moved = client.put("/api/boxes/Docs/filter", json={"filter": {"type": "text"}})
    assert moved.status_code == 200
    assert [m["name"] for m in moved.json()["members"]] == ["note"]
    # and the links say the same thing as the endpoint
    filed = [child["name"] for child in client.get("/api/boxes/Docs").json()["children"]]
    assert filed == ["note"]


def test_a_filter_with_an_unknown_clause_is_refused(client: TestClient) -> None:
    client.post(
        "/api/boxes", json={"name": "Ouch", "box_type": "dynamic-folder", "config": {}}
    )
    response = client.put("/api/boxes/Ouch/filter", json={"filter": {"colour": "red"}})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "bad_filter"
    assert "colour" in response.json()["detail"]["reason"]
    # the refusal left the folder asking nothing, which is to say: everything
    assert client.get("/api/boxes/Ouch/filter").json()["describes"] == "every box"


def test_an_empty_filter_asks_for_every_box(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "one"})
    client.post("/api/boxes", json={"name": "two"})
    client.post(
        "/api/boxes", json={"name": "Whole", "box_type": "dynamic-folder", "config": {}}
    )
    body = client.put("/api/boxes/Whole/filter", json={"filter": {}}).json()
    assert body["describes"] == "every box"
    # every box means every box: the library as it stands, including the folder itself
    everything = sorted(entry["name"] for entry in client.get("/api/boxes?limit=1000").json())
    assert sorted(m["name"] for m in body["members"]) == everything


def test_the_cabinet_holds_every_box_through_the_api(client: TestClient) -> None:
    created = client.post("/api/boxes", json={"name": "cabinet", "box_type": "cabinet"})
    assert created.status_code == 201
    client.post("/api/boxes", json={"name": "later"})
    members = sorted(child["name"] for child in client.get("/api/boxes/cabinet").json()["children"])
    everything = sorted(entry["name"] for entry in client.get("/api/boxes?limit=1000").json())
    assert members == everything
    assert "cabinet" in members  # a cabinet is a box, so it holds itself


def test_resolving_on_demand_says_what_it_holds(client: TestClient) -> None:
    client.post(
        "/api/boxes", json={"name": "square", "box_type": "box"}
    )  # not a pdf: the folder does not want it
    client.post(
        "/api/boxes",
        json={"name": "PDFs", "box_type": "dynamic-folder", "config": {"filter": {"type": "pdf"}}},
    )
    client.post("/api/boxes/PDFs/children", json={"child": "square"})  # filed by hand
    assert "square" in [
        child["name"] for child in client.get("/api/boxes/PDFs").json()["children"]
    ]
    resolved = client.post("/api/boxes/PDFs/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["members"] == 0
    assert client.get("/api/boxes/PDFs").json()["children"] == []


def test_a_dynamic_folder_says_so_in_the_type_list(client: TestClient) -> None:
    described = {entry["key"]: entry for entry in client.get("/api/types").json()}
    assert described["dynamic-folder"]["resolves_membership"] is True
    assert described["cabinet"]["resolves_membership"] is True
    assert described["cabinet"]["filter_is_fixed"] is True
    assert described["folder"]["resolves_membership"] is False


# -- creating a box from a box -----------------------------------------------


def test_a_box_made_from_another_starts_with_that_parent(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    # a container may hand over its uuid or an object; both mean "file it here"
    assert '"string" ? { parent: options } : options' in script
    assert "await fillParentChoices(choices.parent);" in script
    assert "if (choices.parent) el(\"nb-parent\").value = choices.parent;" in script
    # the panel is one page, so the parent asked for is fetched when it is not on it
    assert "if (preferred && !state.boxes.some((box) => box.uuid === preferred))" in script
    assert "parentOption(select, await api.box(preferred));" in script


def test_creating_from_a_box_leaves_the_view_on_that_box(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "const filedIn = payload.parent;" in script
    assert "await openBox(filedIn, { mode: state.mode });" in script
    assert 'status(`created ${created.name} in it`);' in script
    # ...and only jumps to the child when there was no container to stay on
    assert "await openBox(created.uuid, { mode: \"content\" });" in script


# -- the folder box: drop, search, history ------------------------------------


def test_a_folder_takes_a_box_dropped_from_the_panel(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    start = script.index("const folderView = {")
    end = script.index("function childTile(")
    folder_block = script[start:end]
    assert 'wrap.addEventListener("drop"' in folder_block
    assert "await api.link(box.uuid, dragged);" in folder_block
    # preventing the default is what claims the drop from the root's holder
    assert "event.preventDefault();" in folder_block
    css = client.get("/static/app.css").text
    assert ".children-view.drop-target" in css


def test_a_folder_filters_its_children_by_name_and_type(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        'search.className = "child-search"',
        "function childMatches(child, term)",
        'String(childKind.label || "").toLowerCase().includes(term)',
        "search.addEventListener(\"input\", draw);",
    ):
        assert needle in script, needle
    assert ".child-search" in client.get("/static/app.css").text


def test_a_search_starts_empty_and_its_history_is_kept_in_the_box(client: TestClient) -> None:
    """The box is a look at the shelf; the list of things searched for is the memory."""
    script = client.get("/static/app.js").text
    # the input is built with no value, so every opening starts clean
    assert 'search.placeholder = "name or type";' in script
    # nothing hands the input a saved value: the term is typed, never restored
    assert "search.value = options." not in script
    assert "search.value = saved" not in script
    assert "search.value = folderOptions" not in script
    # history: deduplicated, newest first, capped, saved into config.folder
    assert "const FOLDER_HISTORY = 8;" in script
    assert "function rememberSearch()" in script
    assert "options.history = [term, ...rest].slice(0, FOLDER_HISTORY);" in script
    assert 'ctx.saveSection("folder", { history: options.history }, { rerender: false });' in script
    # and saving it must not redraw the pane, which would clear the term mid-word
    assert "async function saveSection(section, patch, { rerender = true } = {})" in script
    assert "if (rerender) renderMain();" in script


# -- the dynamic folder and the cabinet, in the page --------------------------


def test_a_dynamic_folder_shows_its_filter_and_the_way_to_change_it(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        "async function renderFilterEditor(",
        "await api.filter(box.uuid)",
        'apply.dataset.action = "apply-filter"',
        'again.dataset.action = "resolve-folder"',
        "await api.setFilter(box.uuid, wanted)",
        "await api.resolveFolder(box.uuid)",
    ):
        assert needle in script, needle
    # a cabinet is everything by definition, so it is said and not edited
    assert 'says.textContent = "holds every box in the library, and this one";' in script
    assert ".filter-bar" in client.get("/static/app.css").text


def test_the_desktop_is_one_scaled_canvas_so_icons_stay_on_the_picture(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        "function fitSurface()",
        'surface.dataset.scale = String(scale);',
        "const scale = Math.max(0.2, Math.min(1, ratio));",
        "new ResizeObserver(() => fitSurface())",
        "holdLive(() => watcher.disconnect());",
        # the size it was arranged at is recorded for smaller panes to scale by
        "canvas: wanted }",
    ):
        assert needle in script, needle
    # the wallpaper is painted on the canvas, not on the pane around it
    assert 'surface.classList.add("has-background");' in script
    assert 'wrap.classList.add("has-background");' not in script
    css = client.get("/static/app.css").text
    assert ".desktop-surface.has-background" in css
    assert "transform-origin: top left;" in css
    # a canvas out of the flow needs its pane sized by the cell, not by its content
    assert ".cell-content .desktop-view { width: 100%; height: 100%; }" in css


def test_a_gesture_in_a_scaled_canvas_divides_the_pointer_by_the_scale(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text
    assert "function surfaceScale(node)" in script
    assert "scale: surfaceScale(tile)," in script
    assert "(next.clientX - origin.x) / origin.scale" in script
    assert "(next.clientY - origin.y) / origin.scale" in script
    # a drop from the panel is a screen distance too
    assert "(event.clientX - frame.left) / scale" in script
