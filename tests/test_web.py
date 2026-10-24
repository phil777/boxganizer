"""JSON API and the HTML page, exercised with FastAPI's TestClient."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

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
    assert body["parents"] == ["House"]
    assert body["children"] == ["Passport"]


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
    assert "Books" in response.json()["children"]


def test_linking_a_box_into_its_own_child_conflicts(client: TestClient) -> None:
    response = client.post("/api/boxes/Passport/children", json={"child": "House"})
    assert response.status_code == 409


def test_search_endpoint(client: TestClient) -> None:
    names = [box["name"] for box in client.get("/api/search", params={"q": "cooking"}).json()]
    assert names == ["Kitchen"]


def test_html_page_lists_boxes(client: TestClient) -> None:
    page = client.get("/")
    assert page.headers["content-type"].startswith("text/html")
    assert "Kitchen" in page.text
    assert "Boxganizer" in page.text


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


def test_html_page_filters(client: TestClient) -> None:
    page = client.get("/", params={"q": "passport"})
    assert "Passport" in page.text
    assert "Garage" not in page.text


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


def test_html_page_names_the_type(client: TestClient) -> None:
    client.post("/api/boxes", json={"name": "Ideas", "box_type": "mindmap"})
    assert "mindmap" in client.get("/").text


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
