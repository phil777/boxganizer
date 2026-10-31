"""JSON API and the HTML page, exercised with FastAPI's TestClient."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
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
        'class="side-brand"',
        'class="side-actions"',
        'id="nb-type-filter"',
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


def test_an_mqtt_box_can_be_created_with_the_one_thing_the_page_asks_for(
    client: TestClient,
) -> None:
    """The address says it all; the dialog should not have to ask twice.

    This type used to demand `metadata["topic"]` as well, and the page renders no field
    for it, so creating an MQTT box answered 422 with a sentence about metadata nobody had
    been given the chance to fill in.
    """
    response = client.post(
        "/api/boxes",
        json={
            "name": "Cellar temp",
            "box_type": "mqtt",
            "resource_uri": "mqtt://broker.local:1883/cellar/temp",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["box_type"] == "mqtt"
    assert client.get("/api/boxes/Cellar temp").json()["box_type"] == "mqtt"
    # the endpoint reads the topic back out of the address
    read = client.get(f"/api/boxes/{created['uuid']}/mqtt").json()
    assert read["target"]["topic"] == "cellar/temp"


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
    # it takes the path with it, so the trail still says how the user got there
    assert "ctx.open(child.uuid, { trail: ctx.trail() })" in script


def test_no_display_preference_is_read_any_more(client: TestClient) -> None:
    """With one way to view a box, the `display` setting has nothing to decide."""
    script = client.get("/static/app.js").text
    assert '"display"' not in script
    assert '|| "icon"' not in script


def test_there_is_no_icon_mode_left_to_choose(client: TestClient) -> None:
    """The Icon/Content toggle is gone, and with it the second way to look at a box."""
    page = client.get("/").text
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    for gone in ("mode-toggle", 'data-mode="icon"', "Show as icon", "setMode", "state.mode"):
        assert gone not in page, gone
        assert gone not in script, gone
        assert gone not in css, gone
    # the pane renders the box as its content, with nothing to ask first
    assert 'view.render(canvas, box, context, "content", { embedded: false, depth: 0 });' in script
    # ...and the impossible branch is not left behind in the views
    assert 'if (mode !== "content")' not in script
    assert "ctx.mode()" not in script


def test_the_panel_leads_with_the_name_then_the_two_ways_in(client: TestClient) -> None:
    page = client.get("/").text
    brand = page.index('class="side-brand"')
    actions = page.index('class="side-actions"')
    search = page.index('id="search"')
    assert brand < actions < search, "name first, then Root / New box, then the search"
    assert "boxganizer" in page[brand:actions]
    assert 'id="go-root"' in page[actions:search]
    assert 'id="new-box"' in page[actions:search]


def test_the_type_menu_narrows_to_what_is_typed(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        "function typeMatchesTerm(type, term)",
        "function renderTypeOptions(select, term)",
        "function fillTypeChoices()",
        "function resetTypeFilter(selectId)",
    ):
        assert needle in script, needle
    page = client.get("/").text
    assert 'id="nb-type-filter"' in page
    assert 'id="eb-type-filter"' in page
    # the letters filter the menu; the select stays what the form reads
    assert "select.value = chosen;" in script
    assert "el(modal.type).value" in script
    # a single match is the answer, so typing a kind takes it
    assert "if (term && matches === 1)" in script
    # and a choice is never lost by filtering it out of sight
    assert "if (!hit && type.key !== chosen) return;" in script


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
    # The address went bad after it was stored: a host that has since been left out of
    # it cannot be rejected by the type now, so the endpoint is what has to say so.
    broken = populated.add(
        Box(name="Broken", box_type="mqtt", resource_uri="mqtt://broker/cellar/temp")
    )
    populated._execute(
        "UPDATE resources SET reference = 'mqtt://', ref_key = 'mqtt://' WHERE id = ?",
        (broken.resource_id,),
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


def test_a_broker_that_refuses_arrives_as_a_refusal(populated: BoxStore, sensor: Box) -> None:
    """`refused`, and terminal.

    Bad credentials are a fact about the box, not about the broker's mood: reconnecting with
    the same password forever is not waiting, it is noise — and the page has to be able to
    tell the two apart, because one deserves a retry and the other deserves a sentence.
    """

    async def refusing(target: mqtt.Target) -> AsyncIterator[mqtt.Event]:
        yield mqtt.Event("connecting")
        raise MqttError("connection refused: bad username or password")

    with TestClient(create_app(populated, mqtt_events=refusing)) as test_client:
        text = test_client.get("/api/boxes/Cellar temp/mqtt/stream").text
    assert "event: refused" in text
    assert "event: error" not in text
    assert "bad username or password" in text


def test_the_sensor_view_shows_the_value_and_listens_for_more(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        'registerView("mqtt", mqttView)',
        "request(`/api/boxes/${encodeURIComponent(ref)}/mqtt`)",
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
    assert "if (opts.cell) {" in script
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
    saved = client.put(f"/api/boxes/{desk.uuid}", json={"resource_uri": "/home/me/wall.png"}).json()
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
    client.post("/api/boxes", json={"name": "Ouch", "box_type": "dynamic-folder", "config": {}})
    response = client.put("/api/boxes/Ouch/filter", json={"filter": {"colour": "red"}})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "bad_filter"
    assert "colour" in response.json()["detail"]["reason"]
    # the refusal left the folder asking nothing, which is to say: everything
    assert client.get("/api/boxes/Ouch/filter").json()["describes"] == "every box"


def test_an_empty_filter_asks_for_every_box(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "one"})
    client.post("/api/boxes", json={"name": "two"})
    client.post("/api/boxes", json={"name": "Whole", "box_type": "dynamic-folder", "config": {}})
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
    assert "square" in [child["name"] for child in client.get("/api/boxes/PDFs").json()["children"]]
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
    assert 'if (choices.parent) el("nb-parent").value = choices.parent;' in script
    # the panel is one page, so the parent asked for is fetched when it is not on it
    assert "if (preferred && !state.boxes.some((box) => box.uuid === preferred))" in script
    assert "parentOption(select, await api.box(preferred));" in script


def test_creating_from_a_box_leaves_the_view_on_that_box(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "const filedIn = payload.parent;" in script
    assert "await openBox(filedIn);" in script
    assert "status(`created ${created.name} in it`);" in script
    # ...and only jumps to the child when there was no container to stay on
    assert "await openBox(created.uuid);" in script


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
        'search.addEventListener("input", draw);',
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
        "await api.resolveFolder(box.uuid)",
        # the bar holds one expression field, and the builder writes into that same field rather
        # than keeping a private model the text could contradict
        'expression.className = "filter-expression";',
        "async function renderFilterRows(",
        "expression.value = expressionFromRows(rows, join);",
        # the rows come from the server's parse, so the page owns no second grammar
        "data = await api.parseFilter(text);",
        'button.dataset.action = "move-clause";',
        'remove.dataset.action = "remove-clause";',
        'add.dataset.action = "add-clause";',
        "groups clauses with brackets, so it is edited in the field",
        "await api.setFilter(box.uuid, text)",
        "async function commit(text)",
        # a refused expression is answered where it was typed, and the folder is not reloaded
        # into a membership the server never accepted
        "complaint.textContent = error.message;",
        "function storedExpression(filter)",
    ):
        assert needle in script, needle
    # every field of the grammar is reachable from the controls, not only from the keyboard
    for field in (
        'value: "type"',
        'value: "name"',
        'value: "tag"',
        'value: "content"',
        'value: "resource"',
    ):
        assert field in script, field
    for join in ('value: "and"', 'value: "or"', 'value: "not"'):
        assert join in script, join
    # a cabinet is everything by definition, so it is said and not edited
    assert 'says.textContent = "holds every box in the library, and this one";' in script
    css = client.get("/static/app.css").text
    for rule in (
        ".filter-bar",
        ".filter-expression",
        ".filter-rows",
        ".filter-row",
        ".filter-broken",
    ):
        assert rule in css, rule


def test_the_api_reads_an_expression_without_saving_it(client: TestClient) -> None:
    """The bar asks the server what a filter means, instead of keeping its own parser.

    `clauses` is a list for a chain the controls can draw, and null when the expression groups —
    a reorder of a bracketed group would silently change what the folder selects.
    """
    answer = client.post(
        "/api/filters/parse",
        json={"expression": 'type:note and not name:"weekly log"'},
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["usable"] is True
    assert body["join"] == "and"
    assert [(row["field"], row["value"], row["negate"]) for row in body["clauses"]] == [
        ("type", "note", False),
        ("name", "weekly log", True),
    ]

    grouped = client.post(
        "/api/filters/parse", json={"expression": "(type:note or type:pdf) and tag:x"}
    )
    assert grouped.status_code == 200
    assert grouped.json()["usable"] is True
    assert grouped.json()["clauses"] is None

    broken = client.post("/api/filters/parse", json={"expression": "type:note and"})
    assert broken.json()["usable"] is False
    assert broken.json()["broken"]
    assert broken.json()["clauses"] is None


def test_the_desktop_is_one_scaled_canvas_so_icons_stay_on_the_picture(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text
    for needle in (
        "function fitSurface()",
        "surface.dataset.scale = String(scale);",
        "const scale = Math.max(0.2, Math.min(1, ratio));",
        "fitted = fitSurface();",
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


# -- a box created from the desktop lands where the click was ------------------


def _icon_rect(client: TestClient, surface: str, child_uuid: str) -> dict[str, int]:
    """One icon's rectangle, as the desktop's own config records it."""
    return client.get(f"/api/boxes/{surface}").json()["config"]["desktop"]["positions"][child_uuid]


def test_creating_from_the_desktop_places_the_icon_at_the_spot(
    client: TestClient, desk: Box
) -> None:
    created = client.post(
        "/api/boxes",
        json={
            "name": "Where I clicked",
            "parent": "Surface",
            "place": {"x": 250, "y": 130},
        },
    )
    assert created.status_code == 201
    uuid = created.json()["uuid"]
    spot = _icon_rect(client, "Surface", uuid)
    assert (spot["x"], spot["y"]) == (250, 130)
    # and it really is the desktop's child, not merely placed
    assert uuid in [child["uuid"] for child in client.get("/api/boxes/Surface").json()["children"]]


def test_a_spotted_box_on_a_snapped_desktop_lands_on_a_line(client: TestClient, desk: Box) -> None:
    client.post("/api/boxes/Surface/desktop/settings", json={"grid": 32, "snap": True})
    created = client.post(
        "/api/boxes",
        json={"name": "Near a line", "parent": "Surface", "place": {"x": 35, "y": 47}},
    )
    uuid = created.json()["uuid"]
    spot = _icon_rect(client, "Surface", uuid)
    assert spot["x"] % 32 == 0 and spot["y"] % 32 == 0, spot


def test_a_spot_is_refused_when_the_parent_has_no_surface(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "Basket", "box_type": "folder"})
    response = client.post(
        "/api/boxes",
        json={"name": "Nowhere", "parent": "Basket", "place": {"x": 10, "y": 10}},
    )
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "not_a_desktop"


def test_a_spot_without_a_parent_is_refused(client: TestClient) -> None:
    response = client.post("/api/boxes", json={"name": "Adrift", "place": {"x": 5, "y": 5}})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "place_without_parent"


def test_a_desktop_endpoint_refuses_a_box_that_is_not_one(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "Plain folder", "box_type": "folder"})
    response = client.post(
        "/api/boxes/Plain folder/desktop/move", json={"child": "Plain folder", "x": 1, "y": 1}
    )
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "not_a_desktop"


def test_the_page_sends_the_right_click_spot_of_the_canvas(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    # the spot is in canvas pixels: the screen distance divided by the drawn scale
    assert "const scale = surfaceScale(surface);" in script
    assert "(event.clientX - frame.left) / scale - DESKTOP_DEFAULTS.tile / 2" in script
    assert "ctx.newBox({ parent: box.uuid, place: spot })" in script
    assert "place: state.pendingPlace || null," in script
    # and leaving the modal forgets it, so it cannot land in the next box's path
    assert 'el("new-box-modal").addEventListener("close"' in script


def test_the_mindmap_view_is_registered_and_draws_a_map(client: TestClient) -> None:
    """The picture is nodes, curves and a keyboard — the three the type promises."""
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert 'registerView("mindmap"' in script
    assert "mindmapLayout" in script
    # the lines are real SVG paths, and they are curved rather than ruled
    assert 'createElementNS(SVG_NS, "path")' in script
    assert " C " in script
    # a node is a rounded tile carrying the box's own icon and name
    assert "mindmap-node" in script
    assert ".mindmap-node" in css
    assert "border-radius" in css


def test_the_mindmap_answers_the_keys_the_type_promises(client: TestClient) -> None:
    """Insert, Enter, the arrows, and Ctrl doing the two things Ctrl does here."""
    script = client.get("/static/app.js").text
    for key in ('"Insert"', '"Enter"', '"ArrowUp"', '"ArrowDown"', '"ArrowLeft"', '"ArrowRight"'):
        assert key in script, key
    assert "ctrlKey" in script
    # Insert hangs a node under the one in hand; Enter beside it, not under it
    assert "addNode(row || drawn.centre, null)" in script
    assert "addNode(row.parent, row.node.uuid)" in script
    # Ctrl is read against the side the branch grows on, so it always means
    # away-from-the-centre or towards-it whichever way the map is facing
    assert 'const away = (key === "ArrowRight" && row.sign === 1)' in script
    # and a name is written where the node stands
    assert 'contentEditable = "plaintext-only"' in script


def test_a_branch_keeps_one_colour_from_the_centre_down(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "MINDMAP.palette[index % MINDMAP.palette.length]" in script
    # a child is walked with its parent's colour, not with a fresh one
    assert "walk(child, depth + 1, colour, sign, row)" in script


def test_the_page_asks_the_map_endpoints_it_needs(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    for path in (
        "/mindmap",
        "/mindmap/side",
        "/mindmap/order",
        "/mindmap/hierarchy",
        "/mindmap/node",
        "/mindmap/fill",
    ):
        assert path in script, path


def test_a_map_shown_in_a_cell_is_scaled_rather_than_cropped(client: TestClient) -> None:
    """In a grid square, the whole map is drawn smaller: the shape is the point."""
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert "mindmap-fitted" in script
    assert "new ResizeObserver(fit)" in script
    assert ".cell-content .mindmap-view" in css


def test_the_ssh_console_is_registered_and_says_what_it_is(client: TestClient) -> None:
    """A console, not a terminal: the page states the difference instead of hiding it."""
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert 'registerView("ssh"' in script
    assert "sshView" in script
    assert ".ssh-log" in css
    # the transcript announces itself to a screen reader as a log of what happened
    assert 'setAttribute("role", "log")' in script
    assert "no password is stored" in script.lower()


def test_the_ssh_console_asks_only_the_endpoints_that_exist(client: TestClient) -> None:
    script = client.get("/static/app.js").text
    assert "/ssh" in script
    assert "/ssh/run" in script
    # and never anything that would open a session it cannot close
    assert "/ssh/open" not in script
    assert "/ssh/stream" not in script


def test_a_page_may_ask_for_a_password_and_may_never_keep_one(client: TestClient) -> None:
    """Asking and storing are different things, and the page is allowed exactly one of them.

    The old version of this test forbade a password field anywhere in the page, which was a
    way of saying "this software cannot authenticate" — and the user asked for
    authentication. The rule worth keeping is the narrower one: a secret may cross to the
    server for the length of a handshake, and may not be written into a box, because a box
    lives in a file that gets copied, mailed and backed up.
    """
    script = client.get("/static/app.js").text
    vnc_source = script[script.index("async function vncView") : script.index('registerView("vnc"')]
    # one field, shown on demand, and wiped from the DOM once it has been used
    assert vnc_source.count('type = "password"') == 1
    assert "secret.hidden = false;" in vnc_source
    assert 'secret.value = ""; // the bytes are in the server\'s session now' in vnc_source
    # it goes to the open call and nowhere else — never to a box, and never in an address
    assert "api.vncOpen(box.uuid, password)" in vnc_source
    assert "vncStreamUrl: (ref, session) =>" in script
    assert "?session=" in script
    assert "api.updateBox" not in vnc_source
    # SSH still borrows the system client and never sees a secret
    ssh_source = script[script.index("async function sshView") : script.index('registerView("ssh"')]
    assert "password" not in ssh_source.lower().replace("no password is stored", "")


def test_a_screen_that_wants_a_file_for_its_password_offers_the_field(client: TestClient) -> None:
    """Metadata a type will honour, but never asks for, is a feature only in the docs."""
    kinds = {kind["key"]: kind for kind in client.get("/api/types").json()}
    assert kinds["vnc"]["optional_metadata"] == ["password_file"]
    assert "password_file" not in kinds["vnc"]["required_metadata"]
    assert kinds["home-assistant"]["required_metadata"] == ["entity_id"]
    script = client.get("/static/app.js").text
    assert "...(kind.optional_metadata || [])" in script
    assert 'title.textContent += " (optional)";' in script
    assert 'password_file: "~/.vnc/workstation.pass"' in script


def test_the_console_keeps_what_it_ran_without_touching_the_rest_of_the_box(
    client: TestClient,
) -> None:
    """History is saved into `config.ssh`, beside whatever else the config holds."""
    script = client.get("/static/app.js").text
    assert "sshHistory" in script
    assert "pushHistory" in script
    assert "config.ssh" in script or "ssh: {" in script


def test_a_filtered_type_choice_does_not_leak_into_the_next_box(client: TestClient) -> None:
    """The list keeps what is chosen so filtering cannot lose it — so opening the modal
    has to name the type explicitly, or the last type anyone searched for is the one
    the next box is made with."""
    script = client.get("/static/app.js").text
    assert 'el(CREATE_MODAL.type).value = choices.boxType || "box";' in script
    # and the choice still rides along *within* one search, which is why the bug was
    # possible in the first place
    assert "if (!hit && type.key !== chosen) return;" in script


def test_the_canvas_kept_is_the_canvas_and_not_the_picture_on_screen(
    client: TestClient,
) -> None:
    """What is saved is the number the icon coordinates are written in.

    Saving `getBoundingClientRect()` saved that same number multiplied by the scale, so
    each opening folded the desktop a little smaller — visible as a pane that needed a
    scrollbar, then did not, then did again.
    """
    script = client.get("/static/app.js").text
    # the target is at least what the icons occupy, so an old too-small record is healed
    assert "width: Math.max(base.width, availW, extent.width)," in script
    assert "height: Math.max(base.height, availH, extent.height)," in script
    # and what is recorded comes from the target, at full size only
    assert "Math.round(fitted.target.width)" in script
    assert "Math.round(fitted.target.height)" in script
    assert "if (!opts.embedded && fitted && fitted.scale === 1)" in script
    # the screen rectangle is no longer a source of the stored size
    record = script[script.index("if (!opts.embedded && fitted && fitted.scale === 1)") :][:900]
    assert "getBoundingClientRect" not in record


def test_a_dragged_icon_is_painted_while_the_pointer_moves(
    client: TestClient,
) -> None:
    """The tile follows the hand; the database is told only when the hand lets go."""
    script = client.get("/static/app.js").text
    assert "function follow(event, onMove, onCommit, show)" in script
    assert "if (show) show(latest);" in script
    # the move paints and the commit persists: two callbacks on one gesture, the paint
    # handed to `follow` as the argument that runs on every pointer move
    drag = script[script.index('tile.addEventListener("pointerdown"') :]
    drag = drag[: drag.index("grip.addEventListener")]
    assert "placeOnDesktop(box, ctx, child.uuid, where.x, where.y).then(" in drag
    assert "      (where) => {\n        tile.style.left = `${where.x}px`;" in drag
    # sizing the tile is live too, for the same reason
    assert "tile.style.width = `${size.width}px`;" in script


def test_the_roots_slot_does_not_put_a_scrollbar_around_a_desktop(
    client: TestClient,
) -> None:
    """A scaled canvas and a scrollbar are contradictory answers to the same problem."""
    css = client.get("/static/app.css").text
    assert ".root-child > .desktop-view {" in css
    block = css[css.index(".root-child > .desktop-view {") :]
    assert "overflow: hidden;" in block.split("}")[0]
    # and the slot's own generous rule is deliberately weightless, so that holds
    assert ":where(:not(.desktop-toolbar)" in css


def test_nothing_is_the_default_parent_of_a_new_box(client: TestClient) -> None:
    """Root holds one box: preselecting it would evict the way in for a box nobody
    filed anywhere on purpose."""
    script = client.get("/static/app.js").text
    fill = script[script.index("async function fillParentChoices") :]
    fill = fill[: fill.index("\n}\n")]
    assert "select.value = root.uuid" not in fill
    assert "if (preferred) select.value = preferred;" in fill
    # the empty choice is still offered first, and still says what it means
    assert 'none.textContent = "(no parent)";' in script


def test_the_dialog_asks_the_core_about_indexing_rather_than_guessing(
    client: TestClient,
) -> None:
    """The checkbox's default is one rule in one place, not two that drift."""
    script = client.get("/static/app.js").text
    page = client.get("/").text
    assert "/api/index-suggestion" in script
    assert 'id="nb-index"' in page and 'id="eb-index"' in page
    assert 'id="nb-index-reason"' in page and 'id="eb-index-reason"' in page
    # both dialogs carry the answer out to the server
    assert "payload.index_resource = el(EDIT_MODAL.index).checked;" in script
    assert 'index_resource: el("nb-index") ? el("nb-index").checked : null,' in script


def test_the_index_suggestion_endpoint_answers_for_the_thing_pointed_at(
    client: TestClient, tmp_path: Path
) -> None:
    answer = client.get(
        "/api/index-suggestion", params={"resource": "", "box_type": "folder"}
    ).json()
    assert answer["index_it"] is False
    assert "nothing to read" in answer["reason"]

    text = client.get(
        "/api/index-suggestion", params={"resource": "/x/y.md", "box_type": "folder"}
    ).json()
    assert text["index_it"] is True

    picture = client.get(
        "/api/index-suggestion", params={"resource": "/x/y.png", "box_type": "image"}
    ).json()
    assert picture["index_it"] is False

    remote = client.get(
        "/api/index-suggestion", params={"resource": "https://x/y.txt", "box_type": "web"}
    ).json()
    assert remote["index_it"] is False and "remote" in remote["reason"]

    # a type that exists to hold words says so whatever the file is called
    by_type = client.get(
        "/api/index-suggestion", params={"resource": "/x/no-extension", "box_type": "text"}
    ).json()
    assert by_type["index_it"] is True


def test_a_shared_file_answers_with_the_decision_already_made(
    client: TestClient, tmp_path: Path
) -> None:
    """Two boxes cannot hold two opinions about whether one file is in the index."""
    from boxganizer import resources
    from boxganizer.models import Box

    store = client.app.state.store
    notes = tmp_path / "shared-notes.txt"
    notes.write_text("one file two boxes", encoding="utf-8")
    store.content_root = tmp_path
    store.add(Box(name="Holder", box_type="folder", resource_uri=str(notes)), index_it=False)

    answer = client.get(
        "/api/index-suggestion", params={"resource": str(notes), "box_type": "text"}
    ).json()
    assert answer["shared"] is True
    assert answer["index_it"] is False, "the text type does not overrule what was settled"
    assert resources.origin_of(str(notes)) == resources.LOCAL


def test_a_box_arrives_with_what_the_index_thinks_of_it(client: TestClient) -> None:
    """No extra query: the resource columns ride along on the box query."""
    from boxganizer.models import Box

    store = client.app.state.store
    store.add(Box(name="Unindexed", box_type="folder"))
    payload = client.get("/api/boxes/Unindexed").json()
    assert payload["resource"] is None and payload["resource_id"] is None

    answer = client.get("/api/search?q=Unindexed").json()
    assert answer[0]["resource"] is None


def test_the_markdown_view_renders_and_can_show_the_source(client: TestClient) -> None:
    """The view is rendered words with a way back to the file, and nothing in between."""
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert 'registerView("markdown"' in script
    assert "markdownView" in script
    # the HTML is what the server said, and nothing is built out of the file's own text
    assert "body.innerHTML = answer.html;" in script
    assert "source.textContent = answer.source;" in script
    assert "/markdown" in script
    # both shapes are styled, and the raw one is the file with its markers intact
    for needle in (".markdown-body", ".markdown-source", ".markdown-bar"):
        assert needle in css


def test_the_markdown_mode_is_remembered_in_the_box(client: TestClient) -> None:
    """A note read as plain text at midnight is plain text in the morning."""
    script = client.get("/static/app.js").text
    assert "markdown: {...(section || {}), mode: key}" in script
    assert "stage.dataset.mode = mode;" in script


def test_the_rendered_page_does_not_build_html_from_a_file(client: TestClient) -> None:
    """innerHTML has exactly one source: the server's sanitised rendering."""
    script = client.get("/static/app.js").text
    start = script.index("async function markdownView")
    view = script[start : script.index('registerView("markdown"', start)]
    assignments = [
        line.strip()
        for line in view.splitlines()
        if "innerHTML" in line and not line.strip().startswith("//")
    ]
    assert assignments == ["body.innerHTML = answer.html;"]


def test_the_checklist_view_is_a_list_you_can_act_on(client: TestClient) -> None:
    """The page keeps its own shape: ticks, lines, icons at the right of the line."""
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert 'registerView("checklist", {' in script
    assert "async function checklistView" in script
    for needle in (
        ".checklist-tick",
        ".checklist-words",
        ".checklist-icons",
        ".checklist-chip",
        ".checklist-filed",
    ):
        assert needle in css, needle
    # the icons are pushed to the right end of the line they belong to, and centred on it
    icons = css[css.index(".checklist-icons {") : css.index(".checklist-chip {")]
    assert "margin-left: auto;" in icons
    assert "align-items: center;" in icons
    # a line's remove button must not eat the space the icons are measured against
    start = css.index(".checklist-remove {")
    remove = css[start : css.index(".checklist-item:hover .checklist-remove", start)]
    assert "position: absolute;" in remove


def test_the_checklist_sends_the_whole_list_and_no_html_is_built_from_it(
    client: TestClient,
) -> None:
    """Saving replaces the file; nothing a person typed becomes markup."""
    script = client.get("/static/app.js").text
    start = script.index("async function checklistView")
    view = script[start : script.index('registerView("checklist"', start)]
    assert "api.saveChecklist(box.uuid, title, items.map(shape))" in view
    assert "innerHTML" not in view
    assert "textContent" in view
    # a box dropped on a line is dropped by uuid, the same currency every view uses
    assert 'event.dataTransfer.getData("text/plain")' in view


def test_a_box_dropped_on_a_line_files_it_there(client: TestClient) -> None:
    """The drop is the whole gesture: the icon appears, and the save files the box."""
    store = client.app.state.store
    box = store.add(Box(name="Spring", box_type="checklist"))
    part = store.add(Box(name="Elbow", box_type="folder"))
    script = client.get("/static/app.js").text
    view = script[script.index("async function checklistView") :]
    view = view[: view.index('registerView("checklist"')]
    assert "item.refs.push(uuid)" in view
    # and the server agrees, without a second call from the page
    answer = client.put(
        f"/api/boxes/{box.uuid}/checklist",
        json={"items": [{"text": "Buy", "refs": [part.uuid]}]},
    )
    assert answer.json()["attached"] == [part.uuid]


def test_the_note_view_writes_markers_and_holds_no_document_model(client: TestClient) -> None:
    """The toolbox marks the text up; it does not replace it with a richer format."""
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert 'registerView("note", {' in script
    assert "async function noteView" in script
    # the toolbox is markers, applied to the selection: `**`, `*`, `~~`, line prefixes
    toolbox = script[script.index("const NOTE_TOOLBOX") : script.index("async function noteView")]
    assert '"**"' in toolbox and '"~~"' in toolbox and '"- "' in toolbox and '"> "' in toolbox
    assert "wrap" in toolbox and "line" in toolbox
    # nothing but the server's rendering reaches innerHTML, and only in the read pane
    start = script.index("async function noteView")
    view = script[start : script.index('registerView("note"', start)]
    assignments = [
        line.strip()
        for line in view.splitlines()
        if "innerHTML" in line and not line.strip().startswith("//")
    ]
    assert assignments == ["body.innerHTML = answer.html;"]
    # the sheet, the editor, the toolbox and the chip are all styled
    for needle in (".note-surface", ".note-editor", ".note-tool", ".note-chip"):
        assert needle in css, needle


def test_a_box_named_in_a_note_is_inert_until_the_page_wires_it(client: TestClient) -> None:
    """`boxganizer:` means nothing to a browser. The click handler is what gives it a box."""
    script = client.get("/static/app.js").text
    start = script.index("function wireBoxLinks")
    wiring = script[start : script.index("\n  // -- writing", start)]
    assert 'a[href^="' in wiring
    assert "anchorEl.removeAttribute(" in wiring
    assert "openBox(uuid)" in wiring


def test_the_vnc_view_asks_before_it_draws_and_never_holds_a_socket(client: TestClient) -> None:
    """The page is a window on a session this machine owns — not the session itself."""
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert 'registerView("vnc", {' in script
    assert "async function vncView" in script
    # nothing connects until the person asks, and the stream is the only thing that does
    view_start = script.index("async function vncView")
    view = script[view_start : script.index('registerView("vnc"', view_start)]
    # a probe first to ask what is there, then an open that may answer "password needed",
    # and only a session that came back from the open is streamed
    assert "api.vnc(box.uuid)" in view
    assert "await api.vncOpen(box.uuid, password)" in view
    assert "if (opened.password_needed) {" in view
    assert "new EventSource(api.vncStreamUrl(box.uuid, opened.session))" in view
    assert "WebSocket" not in view  # no socket in the page: frames in, input out
    # the far screen keeps its own shape
    assert ".vnc-screen" in css and "aspect-ratio" in css
    assert ".vnc-stage" in css


def test_a_click_that_changes_the_buttons_is_never_merged_away(client: TestClient) -> None:
    """Positions collapse, button states do not: a dropped release leaves a stuck window."""
    script = client.get("/static/app.js").text
    start = script.index("function send(payload)")
    sender = script[start : script.index("function pointAt", start)]
    assert "button_mask === payload.button_mask" in sender
    assert "queue.shift()" in sender
    # and the coordinate mapping divides by the drawn size, because the canvas is scaled
    from_here = script.index("function pointAt")
    mapping = script[from_here : script.index("screen.addEventListener", from_here)]
    assert "rect.width) * size.width" in mapping
    assert "rect.height) * size.height" in mapping


def test_keys_are_left_to_the_browser_when_a_combo_belongs_to_it(client: TestClient) -> None:
    """Ctrl+R must still reload the page with a VNC pane open."""
    script = client.get("/static/app.js").text
    start = script.index('stage.addEventListener("keydown"')
    keys = script[start : script.index('stage.addEventListener("keyup"', start)]
    assert "event.ctrlKey || event.metaKey" in keys


def test_a_preview_asks_for_the_file_rather_than_for_a_box_that_does_not_exist(
    client: TestClient,
) -> None:
    """A file in a directory has no uuid; the directory is the ref and the file the subject."""
    script = client.get("/static/app.js").text
    assert 'answer = await api.markdown(opts.owner || box.uuid, opts.sub || "");' in script
    assert "sub: entry.sub," in script
    assert "owner: box.uuid," in script
    # and a preview does not try to write its view mode into a box that is not there
    guard = script.index("if (!box.uuid) return;")
    assert "api.setConfig(box.uuid" in script[guard : guard + 400]


def test_a_route_the_server_does_not_know_says_so_rather_than_not_found(
    client: TestClient,
) -> None:
    """New page against an old server process: the fix is a restart, not a search for a box."""
    script = client.get("/static/app.js").text
    assert 'response.status === 404 && reason === "Not Found"' in script
    assert "restart" in script


def test_the_index_choice_is_one_line_and_says_little(client: TestClient) -> None:
    """A tick box and its sentence on one line: it is one question, not two."""
    page = client.get("/").text
    assert page.count("<span>Index content</span>") == 2  # the create dialog and the edit one
    assert "Keep this file in the full-text search index" not in page
    css = client.get("/static/app.css").text
    start = css.index("label.index-choice {")
    rule = css[start : css.index("}", start)]
    assert "flex-direction: row;" in rule
    assert "align-items: center;" in rule
    # both dialogs, not just the one the report was about
    selectors = css[:start].rsplit("*/", 1)[1]
    assert "#new-box-modal" in selectors and "#edit-box-modal" in selectors
    # the checkbox keeps its own size: the dialog gives every input field padding
    box = css.index('#new-box-modal label.index-choice input[type="checkbox"],')
    block = css[box : css.index("}", box)]
    assert "padding: 0;" in block and "width: auto;" in block


def test_the_panel_carries_a_menu_of_actions_on_the_library_itself(client: TestClient) -> None:
    """Not every action belongs to the box on screen."""
    page = client.get("/").text
    assert 'id="side-menu"' in page
    assert 'class="burger"' in page
    assert 'aria-haspopup="true"' in page
    # on the name's line, at its right end
    css = client.get("/static/app.css").text
    start = css.index(".burger {")
    assert "margin-left: auto;" in css[start : css.index("}", start)]
    script = client.get("/static/app.js").text
    assert 'label: "About", run: showAbout' in script
    assert 'run: () => runIndexation("outstanding")' in script
    assert 'run: () => runIndexation("everything")' in script


def test_about_reports_the_version_the_server_is_running(client: TestClient) -> None:
    """The version comes from the server: the page must not know its own number twice."""
    page = client.get("/").text
    assert 'id="about-modal"' in page
    assert 'id="about-version"' in page
    script = client.get("/static/app.js").text
    assert "api.health()" in script
    assert "version ${health.version}" in script
    assert client.get("/api/health").json()["version"]


def test_the_index_is_reported_on_every_panel_and_can_be_run_from_the_page(
    client: TestClient,
) -> None:
    """The count leads, the index ends the line, and the action that fills it is one click away.

    (This rule changed: the two used to be crammed together at the right edge, index first.
    See `test_the_status_line_is_two_halves_and_one_of_them_is_a_button` for why.)
    """
    page = client.get("/").text
    assert 'id="index-status"' in page
    assert 'id="box-count"' in page
    assert page.index('id="box-count"') < page.index('id="index-status"')
    css = client.get("/static/app.css").text
    start = css.index("#side-status {")
    rule = css[start : css.index("}", start)]
    assert "justify-content: space-between;" in rule

    status = client.get("/api/index/status").json()
    assert {"wanted", "indexed", "outstanding", "stale", "folders", "unreadable"} <= set(status)
    assert status["running"] is False

    run = client.post("/api/index/run", json={"scope": "outstanding"})
    assert run.status_code == 200
    assert run.json()["started"] is True

    # The pass is backgrounded, so the answer is only that it started; the numbers are
    # polled. A request that read the whole library would look like a frozen page.
    script = client.get("/static/app.js").text
    assert "indexTimer = setInterval(() => refreshIndexStatus(), 1200)" in script
    assert "refreshIndexStatus({ check: true })" in script


def test_checking_the_status_looks_at_the_files_when_asked(
    client: TestClient, tmp_path: Path
) -> None:
    """`?check=1` is the difference between what the database believes and what is true."""
    from boxganizer import search as text_search
    from boxganizer.models import Box as _Box
    from boxganizer.store import BoxStore as _BoxStore

    note = tmp_path / "note.md"
    note.write_text("the first words about the boiler\n", encoding="utf-8")
    store = _BoxStore(tmp_path / "st.db", content_root=tmp_path)
    try:
        store.add(_Box(name="Note", box_type="markdown", resource_uri=str(note)))
        text_search.rebuild(store)
        with TestClient(create_app(store, content_root=tmp_path)) as fresh:
            assert fresh.get("/api/index/status").json()["stale"] == 0
            note.write_text("the second words about the boiler\n", encoding="utf-8")
            assert fresh.get("/api/index/status").json()["stale"] == 0  # what it was told
            assert fresh.get("/api/index/status", params={"check": True}).json()["stale"] == 1
    finally:
        store.close()


def test_moving_an_icon_does_not_rebuild_the_desktop_around_it(client: TestClient) -> None:
    """The tile is already where the hand left it: nothing else needs to be drawn."""
    script = client.get("/static/app.js").text
    # the move and the size commit into the picture the view holds, not into a re-render
    assert "rect.x = where.x;" in script
    assert "rect.y = where.y;" in script
    assert "rect.width = size.width;" in script
    drag = script[script.index('tile.addEventListener("pointerdown"') :]
    drag = drag[: drag.index('grip.addEventListener("pointerdown"')]
    assert "ctx.reload()" not in drag
    grip = script[script.index('grip.addEventListener("pointerdown"') :]
    grip = grip[: grip.index('tile.addEventListener("click"')]
    assert "ctx.reload()" not in grip
    # a box dropped from the panel had no tile here, so that one does redraw
    drop = script[script.index('surface.addEventListener("drop"') :]
    drop = drop[: drop.index('surface.addEventListener("contextmenu"')]
    assert "if (placed) ctx.reload();" in drop


def test_the_pane_itself_files_a_box_that_no_view_claimed(client: TestClient) -> None:
    """Aiming at a few pixels of list should not be the price of filing a box.

    Views keep the drops they mean something by — the pane handler only takes what no
    view prevented the default on — but the rest of the pane is the box, so the pane
    takes it.
    """
    script = client.get("/static/app.js").text
    assert "function wirePaneDrops()" in script
    assert "wirePaneDrops();" in script
    from_here = script.index("function paneAcceptsDrops()")
    wiring = script[from_here : script.index("async function init()", from_here)]
    # a view that claimed the drop wins; the pane only picks up what is left
    assert "if (event.defaultPrevented) return;" in wiring
    # and only a type that holds boxes offers to hold one
    assert "kind.allows_children" in wiring
    assert "await api.link(box.uuid, uuid);" in wiring
    # it does not file a box into itself
    assert "uuid === box.uuid" in wiring
    css = client.get("/static/app.css").text
    start = css.index("#canvas.drop-target {")
    assert "outline:" in css[start : css.index("}", start)]


def test_a_box_that_holds_no_boxes_offers_no_drop_zone(client: TestClient) -> None:
    """A markdown box cannot hold another box, so it must not light up as if it could."""
    script = client.get("/static/app.js").text
    from_here = script.index("function paneAcceptsDrops()")
    guard = script[from_here : script.index("function wirePaneDrops()", from_here)]
    assert "if (!box) return null;" in guard
    assert "kind && kind.allows_children ? box : null" in guard


def test_a_dialog_reports_its_own_refusal(client: TestClient) -> None:
    """A status bar behind an open modal is a message nobody receives.

    The report that came in was "when I create a MQTT box I get 422 in the access log" —
    the page had been told exactly what was wrong and had written it somewhere the person
    could not see, so the dialog now carries its own error line.
    """
    page = client.get("/").text
    assert 'id="nb-error"' in page
    assert 'id="eb-error"' in page
    assert 'role="alert"' in page
    script = client.get("/static/app.js").text
    assert 'function modalError(modal = CREATE_MODAL, message = "")' in script
    create = script[script.index("async function submitNewBox") :]
    create = create[: create.index("/**", create.index("async function submitNewBox") + 10)]
    assert "modalError(CREATE_MODAL, error.message);" in create
    assert "status(error.message, true);" not in create
    edit = script[script.index("async function submitEditBox") :]
    edit = edit[: edit.index("/* ------", script.index("async function submitEditBox") + 10)]
    assert "modalError(EDIT_MODAL, error.message);" in edit


def test_a_dialog_shows_the_address_a_type_wants_and_hides_what_it_cannot_use(
    client: TestClient,
) -> None:
    """Browse and the index tick mean nothing until the resource is a file."""
    script = client.get("/static/app.js").text
    assert "field.placeholder = kind.resource_hint" in script
    assert "browse.hidden = !local;" in script
    assert "choice.hidden = !local;" in script
    assert "renderMetadataFields(modal, kind);" in script
    # the metadata a type asks for is rendered, so a required value is never unaskable
    assert "name = `metadata:${key}`" in script
    assert "metadata: collectMetadata(CREATE_MODAL)" in script
    css = client.get("/static/app.css").text
    assert ".modal-error {" in css and ".resource-hint {" in css and ".metadata-fields {" in css


def test_the_type_list_says_which_types_this_install_cannot_run(client: TestClient) -> None:
    """A box type that needs a missing package must be visible as one while choosing."""
    kinds = {kind["key"]: kind for kind in client.get("/api/types").json()}
    assert kinds["mqtt"]["dependency"] == "aiomqtt"
    assert set(kinds["mqtt"]) >= {"available", "unavailable_reason"}
    assert kinds["folder"]["available"] is True
    script = client.get("/static/app.js").text
    assert "— not in this install" in script
    assert "modalWarning(modal, kind.available === false" in script
    page = client.get("/").text
    assert 'id="nb-warning"' in page and 'id="eb-warning"' in page
    css = client.get("/static/app.css").text
    assert ".modal-warning {" in css


def test_a_refused_mqtt_box_stops_retrying_and_keeps_the_reason(client: TestClient) -> None:
    """The report: "it always displays 'the stream dropped', and never connects".

    EventSource fires an `error` with no body when a connection ends, and the page answered
    that with its own generic sentence — on top of whatever the server had just explained,
    and with an automatic reconnect to a box that could never work. A refusal is now its own
    event: the page closes the stream and leaves the server's words where they are.
    """
    script = client.get("/static/app.js").text
    view = script[script.index("const mqttView = {") : script.index('registerView("mqtt"')]
    assert '"connecting", "live", "value", "retrying", "refused"' in view
    assert "if (refused) return;" in view
    assert "source.close();" in view
    assert "let lastWords" in view
    # the generic line survives only as what it now means: a drop, with nothing newer said
    assert 'lastWords || "the stream dropped; going back to the broker"' in view
    # the state is gone from the label map too, so nothing can print a state that no longer
    # arrives
    states = script[script.index("const MQTT_STATES = {") :]
    assert "refused:" in states[: states.index("};")]
    assert "error:" not in states[: states.index("};")]


def test_the_status_line_is_two_halves_and_one_of_them_is_a_button(client: TestClient) -> None:
    """ "37 boxes" starts the line; what the index is behind ends it, and opens when clicked.

    Both halves used to sit at the right edge, with the index first, so the line read as one
    long complaint about the index and the count — the fact worth knowing — ended it. And the
    number was a claim nobody could interrogate: "2 to reindex" with no way to ask which two.
    """
    page = client.get("/").text
    count_at = page.index('id="box-count"')
    status_at = page.index('id="index-status"')
    assert count_at < status_at, "the count comes first: it is the sentence, not the tail"
    assert '<button type="button" id="index-status"' in page
    assert 'aria-label="What the index is waiting for"' in page
    assert 'id="pending-reindex"' in page and 'id="pending-look"' in page
    css = client.get("/static/app.css").text
    line_rule = css[css.index("#side-status {") :]
    line_rule = line_rule[: line_rule.index("}")]
    assert "justify-content: space-between;" in line_rule
    assert "flex-end" not in line_rule, "the count must not be pushed to the right with it"

    script = client.get("/static/app.js").text
    assert 'indexPending: () => request("/api/index/pending")' in script
    assert 'el("index-status").addEventListener("click", openIndexModal)' in script
    words = script[script.index("function indexWords(") :]
    words = words[: words.index("\n}\n")]
    # "out of date" is gone: the line says what a pass would do about it
    assert "out of date" not in words
    assert "to reindex" in words and "to index" in words
    # and a number with nothing behind it is not a control
    assert "spot.disabled = waiting === 0;" in script


def test_the_pending_list_tells_work_apart_from_a_dead_end(
    client: TestClient, tmp_path: Path
) -> None:
    """A file that has nothing to index would sit in this list forever.

    So it is named — that is useful — in its own group, marked unfixable, and left out of the
    number on the panel. A list that counted it as work would be a promise this feature cannot
    keep, and a person would press "Reindex them" every morning for a year.
    """
    store = BoxStore(tmp_path / "pend.db", content_root=tmp_path)
    try:
        note = tmp_path / "note.md"
        note.write_text("words about plumbing", encoding="utf-8")
        box = store.add(Box(name="Note", box_type="markdown", resource_uri=str(note)))
        with TestClient(create_app(store, content_root=tmp_path)) as test_client:
            assert test_client.get("/api/index/pending").json()["items"] == []
            note.write_text("words about plumbing and boilers", encoding="utf-8")
            os.utime(note, (note.stat().st_atime, note.stat().st_mtime + 5))
            # the rows are what this answers from, so look at the files first — which is what
            # the dialog's "Look at the files again" button does, and what opening the panel
            # with `check=1` already did
            test_client.get("/api/index/status?check=1")
            body = test_client.get("/api/index/pending").json()
            items = body["items"]
            assert [item["why"] for item in items] == ["changed"]
            assert items[0]["fixable"] is True
            assert [held["name"] for held in items[0]["boxes"]] == ["Note"]
            assert items[0]["count"] == 1
            assert body["wanted"] >= 1
            assert box.name == "Note"

            # a file with nothing searchable in it is named, and not offered as work
            store._execute(
                "UPDATE resources SET indexed_at = '2026-01-01T00:00:00+00:00', bytes = 4096,"
                " stale = 0",
            )
            store._execute("DELETE FROM resource_text")
            body = test_client.get("/api/index/pending").json()
            assert [item["fixable"] for item in body["items"]] == [False]
            assert "no text" in body["items"][0]["words"]

        script = client.get("/static/app.js").text
        assert "items.filter((item) => item.fixable)" in script
        assert "Reindexing will not change this one." in script
        assert 'it has\n      " no row until something walks the folder' not in script
    finally:
        store.close()


def test_a_view_in_a_cell_fills_the_cell(client: TestClient) -> None:
    """The bug: a checklist drew a 317px strip in a 539px cell.

    The rule named the views that existed when it was written — image, text, children, grid —
    and every view added since arrived shrink-to-fit inside a box the width of the pane.
    """
    css = client.get("/static/app.css").text
    block = css[css.index(".cell-content > * {") :]
    block = block[: block.index("}")]
    assert "width: 100%;" in block and "height: 100%;" in block
    assert "min-width: 0;" in block and "min-height: 0;" in block
    # the enumeration is gone, because that was the bug rather than the fix
    assert ".cell-content .image-view," not in css
    assert ".cell-content .icon-view { width: 100%; }" not in css


def test_a_boxs_settings_are_hidden_until_asked_for_and_take_room_when_shown(
    client: TestClient,
) -> None:
    """The drawer is above the content, not over it.

    Every view carries a row of controls somewhere, and together they cost the box the top of
    its own pane. So they start closed behind one gear in the top line — which hides itself when
    the box has no settings row, because a control with nothing to control is decoration.
    """
    page = client.get("/").text
    assert 'id="toggle-config"' in page
    assert 'aria-expanded="false"' in page
    assert page.index('id="toggle-config"') > page.index('id="breadcrumb"')
    assert (
        "hidden"
        in page[
            page.index('id="toggle-config"') : page.index(">", page.index('id="toggle-config"'))
        ]
    )

    css = client.get("/static/app.css").text
    # the pane is a column, so opening the row takes height from the box below it
    canvas = css[css.index("#canvas {") : css.index("}", css.index("#canvas {"))]
    assert "display: flex;" in canvas and "flex-direction: column;" in canvas
    assert "#canvas > .box-config {" in css
    assert "#canvas > :not(.box-config) {" in css
    closed = css[css.index("#canvas:not(.config-open) .box-config {") :]
    closed = closed[: closed.index("}")]
    assert "max-height: 0;" in closed and "visibility: hidden;" in closed
    # hidden must mean hidden to a keyboard, not merely invisible
    assert "pointer-events: none;" in closed
    assert "position: absolute" not in closed, "the drawer may not lie over the box"
    assert "prefers-reduced-motion" in css

    script = client.get("/static/app.js").text
    assert 'const CONFIG_STORAGE_KEY = "boxganizer.configHeader";' in script
    assert 'localStorage.getItem(CONFIG_STORAGE_KEY) === "1"' in script
    assert 'el("canvas").classList.toggle("config-open", configHeaderOpen)' in script
    # the gear is re-checked after the view settles, because views fill themselves in late
    assert 'observe(el("canvas"), { childList: true, subtree: true })' in script
    assert "gear.hidden = rows.length === 0;" in script
    assert "applyConfigHeader();" in script[script.index("function renderMain()") :]


def test_the_settings_row_is_marked_by_convention_not_by_a_list_of_views(
    client: TestClient,
) -> None:
    """Every view that has a controls row says so; the CSS never has to know their names.

    The list-of-views mistake was made once already in this stylesheet — `.image-view,
    .text-view, .children-view, .grid-board` — and every view added afterwards was quietly
    wrong. A class on the row is the same lesson applied before the damage.
    """
    script = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    for view in (
        "children-toolbar",
        "desktop-toolbar",
        "grid-toolbar",
        "fs-bar",
        "filter-bar",
        "markdown-bar",
        "mindmap-toolbar",
        "mqtt-head",
        "note-bar",
        "ssh-head",
        "vnc-controls",
        "browser-bar",
    ):
        assert f'"{view} box-config"' in script, view
    assert script.count("box-config") >= 12
    # a row that *reports* is not a row that *configures*: the checklist head shows a tally
    assert '"checklist-head box-config"' not in script
    # and the stylesheet mentions no view name to find the drawer
    assert ".children-toolbar" in css  # existing rules may still style it
    drawer = css[
        css.index("/* A box's settings row, wherever it lives.") : css.index(".main-head .gear")
    ]
    for name in ("children-toolbar", "fs-bar", "note-bar", "vnc-controls", "ssh-head"):
        assert name not in drawer, name


def test_a_folder_list_takes_the_room_left_and_scrolls_in_itself(client: TestClient) -> None:
    """The pane should not scroll because a folder has forty children.

    It also means a drawer opening above the list takes room from the list, which is the whole
    point of "it does not come over it", and an empty folder keeps its floor: an empty folder is
    still somewhere to drop a box onto.
    """
    css = client.get("/static/app.css").text
    block = css[css.index(".children {") :]
    block = block[: block.index("}")]
    assert "flex: 1 1 auto;" in block and "min-height: 0;" in block and "overflow: auto;" in block
    view = css[css.index(".children-view {") :]
    view = view[: view.index("}")]
    assert "display: flex;" in view and "flex-direction: column;" in view
    assert "min-height: 3.5rem;" in view, "an empty folder must still be a drop target"


def test_tags_are_painted_outside_the_canvas_and_carry_no_view_logic(client: TestClient) -> None:
    """One chip line for thirteen views.

    Every view drawing its own chips is thirteen chances to colour them differently, and the
    colour here is derived from the tag's place in a hierarchy the page does not walk.
    """
    page = client.get("/").text
    assert 'id="tagline"' in page
    assert page.index('id="tagline"') < page.index('id="canvas"')
    assert 'id="tags-modal"' in page and 'id="tags-tree"' in page and 'id="tag-new"' in page
    css = client.get("/static/app.css").text
    assert ".tag-chip.inherited" in css
    assert ".tag-chip.inherited {" in css and "dashed" in css[css.index(".tag-chip.inherited {") :]
    script = client.get("/static/app.js").text
    # the chip line asks for one tag at a time, never rewriting the set it last saw
    assert ".takeTag(box.uuid, chip.name)" in script
    assert "await api.giveTag(box.uuid, word)" in script
    assert "held.explicit.filter" not in script
    # suggestions come from the library, and a new word is taken at face value
    assert 'entry.setAttribute("list", "tag-options")' in script
    assert "ensureTagOptions" in script
    # it is in the menu of things that are about the library, not the box on screen
    assert '{ label: "Manage tags…", run: openTagsModal }' in script
    # and an inherited chip is drawn without a control that could not work
    chips = script[
        script.index("async function renderTagLine") : script.index(
            "async function ensureTagOptions"
        )
    ]
    assert 'chip.explicit ? "" : " inherited"' in chips
    assert "if (chip.explicit) {" in chips


def test_a_directory_box_opens_at_the_top_and_lets_a_file_be_dragged_out(
    client: TestClient,
) -> None:
    """Two halves of one complaint about the explorer box.

    The browse position used to be remembered per box, so a reopened directory showed some
    folder three levels down and looked like a box with the wrong contents; and the only way to
    make a box out of a file inside one was the context menu. Now opening is always the top of
    the folder, and a file can be dragged onto a box — or onto the library list, which is the
    difference between "put this here" and "put this somewhere".
    """
    script = client.get("/static/app.js").text
    # the memory is dropped when a box is opened, not when the pane re-renders it
    open_box = script[script.index("async function openBox") :]
    assert "dirNav.clear();" in open_box[: open_box.index("\n}\n")]
    # a file drags as a file, not as a box uuid
    assert 'const FILE_DRAG_TYPE = "application/x-boxganizer-file";' in script
    assert "row.draggable = true;" in script
    assert 'event.dataTransfer.effectAllowed = "copy";' in script
    # the type is asked of the server, because that is where the inference lives
    assert "api.suggestType(payload.path)" in script
    assert "async function createBoxFromDraggedFile(payload, parent)" in script
    assert "resource_uri: payload.path" in script
    # both drop targets: the box on screen, and the library itself
    pane = script[script.index("function wirePaneDrops") : script.index("async function init()")]
    assert "types.includes(FILE_DRAG_TYPE)" in pane
    assert 'const sideList = el("side");' in script
    assert "await createBoxFromDraggedFile(dragged, null);" in script
    assert "request(`/api/types/suggest?resource_uri=${encodeURIComponent(resourceUri)}`)" in script


def test_a_dragged_file_becomes_a_box_of_the_inferred_type(tmp_path: Path) -> None:
    """The endpoint the page leans on: a path in, a sensible type out."""
    store = BoxStore(tmp_path / "drag.db", content_root=tmp_path)
    try:
        note = tmp_path / "rates.md"
        note.write_text("# Rates\n", encoding="utf-8")
        with TestClient(create_app(store, content_root=tmp_path)) as test_client:
            suggested = test_client.get(
                "/api/types/suggest", params={"resource_uri": str(note)}
            ).json()
            assert suggested["suggestion"]["key"] == "markdown"
            made = test_client.post(
                "/api/boxes",
                json={"name": "rates", "box_type": "markdown", "resource_uri": str(note)},
            )
            assert made.status_code == 201
            detail = test_client.get("/api/boxes/rates").json()
            assert detail["box_type"] == "markdown"
            assert detail["resource_uri"] == str(note)
    finally:
        store.close()


def test_the_pane_answers_a_right_click_no_view_claimed(client: TestClient) -> None:
    """The box's menu belongs to the box, not only to the pixels it happens to paint.

    A folder with no children has no row to right-click, and a short list leaves a strip of
    empty pane below it; both used to answer with nothing, which reads as the box having no
    menu rather than as the click having missed. The pane is the fallback, exactly as it is for
    drops: a view claims the click or the pane answers, never both.
    """
    script = client.get("/static/app.js").text
    assert "function wirePaneMenu()" in script
    assert "wirePaneMenu();" in script
    start = script.index("function wirePaneMenu")
    end = script.index("/* -- the pane files what is dropped on it")
    body = script[start:end]
    assert "if (event.defaultPrevented) return;" in body
    assert "boxMenuItems(box, extra)" in body
    assert 'closest("input, textarea, [contenteditable]")' in body
    assert 'String(window.getSelection() || "").length' in body
    assert "openNewBoxModal({ parent: box.uuid })" in body


def test_the_note_says_whether_it_can_be_typed_into_without_opening_the_drawer(
    client: TestClient,
) -> None:
    """Read and Write were two buttons, and their state was hidden behind a gear.

    One control with two states is what the pair always was: only one of them could ever be the
    next thing pressed. And because that control lives in the settings drawer, which is closed by
    default, the note itself now carries the answer — a person returning to a note could not tell
    whether clicking it would change it, and learned by changing it.
    """
    script = client.get("/static/app.js").text
    note = script[script.index("function paintBar()") :]
    note = note[: note.index("function paintEditor") if "function paintEditor" in note else 3000]
    assert 'mode.dataset.toggle = "mode";' in script
    assert 'writing ? "Read" : "Write"' in script
    assert 'mode.setAttribute("aria-pressed", String(writing))' in script
    assert 'toggle(shape, "mode", "Read"' not in script
    assert 'toggle(shape, "mode", "Write"' not in script
    # the cue is on the note, replaced rather than accumulated, and styled as a warning/ok pair
    assert 'wrap.querySelectorAll(".note-cue").forEach((old) => old.remove());' in script
    assert 'className = writing ? "note-cue editing" : "note-cue readonly"' in script
    css = client.get("/static/app.css").text
    assert ".note-cue.readonly" in css and ".note-cue.editing" in css
    assert (
        ".note-view {" in css
        and "position: relative"
        in css[css.index(".note-view {") : css.index("}", css.index(".note-view {"))]
    )


def test_the_page_shows_the_worded_value_and_the_editor_for_it(client: TestClient) -> None:
    """The page draws what the server decided the reading means, and offers the templates.

    The `innerHTML` here is deliberate and single: `sensors.sanitise` rebuilt that string from an
    allow-list before it was sent, which is the arrangement — one rule, in the language that owns
    it — and the test is where the arrangement is written down.
    """
    script = client.get("/static/app.js").text
    for needle in (
        "function sensorTemplateEditor(box, ctx)",
        "value.innerHTML = shown.styled;",
        "problem.hidden = !(shown && shown.problem);",
        "`the broker sent: ${reading.value}`",
        "area.name = `sensor:${key}`;",
        '"extract_template", "The value in what arrived"',
        '"display_template", "How the value is worded"',
        '"style_template", "How the words are dressed"',
        "await api.updateBox(box.uuid, { config });",
        "templates refused:",
    ):
        assert needle in script, needle

    css = client.get("/static/app.css").text
    for rule in (
        ".sensor-problem",
        ".sensor-templates",
        ".sensor-template",
        ".sensor-good",
        ".sensor-bad",
    ):
        assert rule in css, rule


def test_the_page_draws_the_owners_scale_and_offers_the_controls_for_it(client: TestClient) -> None:
    """Four pictures, sixteen presets and a picker — and the server, not the page, decides where
    the value sits on the scale.

    The split is the point of the test: `numeric.position`, `colour` and `clamped` come from
    `sensors.numeric`, so an opened pane, a grid cell and `box value` cannot disagree about what
    8.4 on a 0-10 scale looks like. The page only paints, and refuses a scale the server refused.
    """
    script = client.get("/static/app.js").text
    for needle in (
        "function sensorPicture(info, { cell = false } = {}) {",
        "function gaugeMarkup(info)",
        "wrap.className = `sensor-picture sensor-${info.representation}`;",
        'wrap.dataset.clamped = info.clamped || "";',
        "const presets = (state.sensorPalette && state.sensorPalette.palette) || [];",
        'function colourChooser(value, onPick, role = "colour") {',
        'picked.type = "color";',
        # one function owns the colour, so a pick cannot be undone by a redraw of the control
        "const choose = (next) => {",
        "function sensorScaleEditor(box, ctx) {",
        "function textColourEditor(box, host) {",
        "scale refused:",
        '["hbar", "Horizontal bar"]',
        '["vbar", "Vertical bar"]',
        '["gauge", "Gauge"]',
        '["progress", "Progression bar"]',
        "state.sensorPalette = await api.sensorPalette();",
        "async function loadSensorPalette()",
        'add.dataset.action = "add-interval";',
        'remove.dataset.action = "remove-interval";',
        'add.dataset.action = "add-text-rule";',
    ):
        assert needle in script, needle

    palette = client.get("/api/sensors/palette").json()
    assert len(palette["palette"]) == 16
    assert palette["representations"] == ["gauge", "hbar", "vbar", "progress"]
    assert {entry["name"] for entry in palette["palette"]} >= {"green", "red", "grey"}

    css = client.get("/static/app.css").text
    for rule in (
        ".sensor-picture",
        ".sensor-track",
        ".sensor-vbar",
        ".sensor-gauge-svg",
        ".sensor-clamp",
        ".sensor-swatch",
        ".sensor-interval",
    ):
        assert rule in css, rule


def test_a_scale_that_does_not_cover_its_range_is_refused_by_the_api(client: TestClient) -> None:
    """The refusal travels to the form unchanged, because the sentence names the hole."""
    created = client.post(
        "/api/boxes",
        json={"name": "Scaled sensor", "box_type": "mqtt", "resource_uri": "mqtt://b:1883/t"},
    )
    refused = client.put(
        f"/api/boxes/{created.json()['uuid']}",
        json={
            "config": {
                "numeric": {
                    "min": 0,
                    "max": 10,
                    "representation": "hbar",
                    "intervals": [{"upto": 6, "colour": "green"}],
                }
            }
        },
    )
    assert refused.status_code == 422
    assert "would have no colour" in refused.text

    accepted = client.put(
        f"/api/boxes/{created.json()['uuid']}",
        json={
            "config": {
                "numeric": {
                    "min": 0,
                    "max": 10,
                    "representation": "hbar",
                    "intervals": [
                        {"upto": 6, "colour": "green"},
                        {"upto": 10, "colour": "#d0021b"},
                    ],
                }
            }
        },
    )
    assert accepted.status_code == 200, accepted.text


def test_a_live_reading_arrives_worded_by_the_box_itself(populated: BoxStore, sensor: Box) -> None:
    """`shown` is the server's answer, so an owner's template is run once, in one language.

    The page is not trusted with what the Jinja means, and it is not trusted to be the thing that
    decides whether a styling template may put a `<script>` on screen: `sensors.present` renders
    and sanitises here, before the bytes leave. Both the snapshot endpoint and the stream say the
    same words about the same message, which is the point of doing it once.
    """
    from boxganizer import sensors

    populated.update(
        replace(
            sensor,
            config={
                "extract_template": "{{ data.pressure }} {{ data.unit }}",
                "style_template": (
                    '<b class="sensor-value">{{ display }}</b><script>alert(1)</script>'
                ),
            },
        )
    )
    events = [mqtt.Event("value", _reading('{"pressure": 1.42, "unit": "bar"}'))]
    with TestClient(create_app(populated, mqtt_events=_scripted(events))) as probe:
        stream = probe.get("/api/boxes/Cellar temp/mqtt/stream").text
        body = probe.get("/api/boxes/Cellar temp/mqtt").json()

    assert '"extracted": "1.42 bar"' in stream, stream
    assert "alert" not in stream
    shown = body["shown"]
    assert shown is not None
    assert shown["extracted"] == "1.42 bar"
    assert shown["styled"] == '<b class="sensor-value">1.42 bar</b>'
    assert shown["problem"] == ""
    assert sensors.present(sensor, "x").styled == "x", "no templates, no dressing"


def test_a_box_refuses_a_template_that_could_not_run(populated: BoxStore, sensor: Box) -> None:
    """The typo is answered in the form that made it, not later, on the box."""
    from boxganizer.errors import InvalidBoxContent

    with pytest.raises(InvalidBoxContent, match="extract_template"):
        populated.update(replace(sensor, config={"extract_template": "{{ data.pressure"}))

    # a template that compiles is accepted, and reaches the box unchanged
    populated.update(replace(sensor, config={"extract_template": "{{ data.pressure }}"}))
    assert populated.get(sensor.name).config == {"extract_template": "{{ data.pressure }}"}


def test_only_a_sensor_is_asked_for_templates(client: TestClient) -> None:
    """A note has no reading to word, so a template on one is nobody's business and stays put."""
    note = client.post(
        "/api/boxes",
        json={"name": "A note", "box_type": "note", "config": {"style_template": "<b>x</b>"}},
    )
    assert note.status_code == 201, note.text
    refused = client.post(
        "/api/boxes",
        json={
            "name": "Broken sensor",
            "box_type": "mqtt",
            "resource_uri": "mqtt://b:1883/t",
            "config": {"style_template": "{% if %}"},
        },
    )
    assert refused.status_code == 422
    assert "style_template" in refused.text
