"""Miniatures: made from the resource, cached, invalidated, and safely absent."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from boxganizer import config, thumbs
from boxganizer.models import Box


@pytest.fixture(autouse=True)
def private_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test's miniatures out of the developer's data directory."""
    data = tmp_path / "data"
    monkeypatch.setattr(config, "data_dir", lambda: data)
    return data / "thumbnails"


def cached(private_cache: Path) -> list[Path]:
    """The miniatures on disk. Asserting non-empty somewhere is the point."""
    return sorted(private_cache.glob("*.png")) if private_cache.exists() else []


def write_image(path: Path, size: tuple[int, int] = (800, 200), colour=(10, 120, 200)) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


def image_box(path: Path, name: str = "Pic") -> Box:
    return Box(name=name, box_type="image", resource_uri=str(path))


# -- making one -------------------------------------------------------------


def test_a_miniature_is_a_png_that_keeps_the_ratio(tmp_path: Path) -> None:
    source = write_image(tmp_path / "wide.png", (800, 200))
    data = thumbs.make_miniature(source)
    assert data[:4] == b"\x89PNG"
    out = tmp_path / "out.png"
    out.write_bytes(data)
    with Image.open(out) as made:
        assert made.size == (160, 40)


def test_a_small_image_is_not_enlarged(tmp_path: Path) -> None:
    source = write_image(tmp_path / "small.png", (20, 10))
    out = tmp_path / "out.png"
    out.write_bytes(thumbs.make_miniature(source))
    with Image.open(out) as made:
        assert made.size == (20, 10)


def test_an_unreadable_file_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "fake.png"
    broken.write_bytes(b"not an image at all")
    with pytest.raises(ValueError, match="cannot make a miniature"):
        thumbs.make_miniature(broken)


def test_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    source = write_image(tmp_path / "a.png")
    with pytest.raises(ValueError, match="no miniature can be made"):
        thumbs.make_miniature(source, kind="spreadsheet")


# -- applying it to a box ---------------------------------------------------


def test_an_image_box_gets_an_icon_from_its_own_file(tmp_path: Path) -> None:
    source = write_image(tmp_path / "pic.png")
    box = thumbs.icon_from_resource(image_box(source))
    assert box.config["icon_source"] == "thumbnail"
    assert box.config["thumb"]["path"] == str(source)
    assert thumbs.thumb_bytes(box) is not None


def test_the_miniature_is_generated_once_and_reused(tmp_path: Path, private_cache: Path) -> None:
    source = write_image(tmp_path / "pic.png")
    box = thumbs.icon_from_resource(image_box(source))
    first = cached(private_cache)
    assert len(first) == 1, "one miniature should exist after the first call"
    thumbs.icon_from_resource(box)
    assert cached(private_cache) == first, "a second call must not regenerate it"


def test_a_changed_source_produces_a_new_miniature(tmp_path: Path, private_cache: Path) -> None:
    source = write_image(tmp_path / "pic.png", (400, 400), (1, 2, 3))
    box = thumbs.icon_from_resource(image_box(source))
    before = thumbs.thumb_bytes(box)
    before_size = box.config["thumb"]["size"]  # read now: the box is updated in place

    write_image(tmp_path / "pic.png", (400, 60), (200, 200, 200))
    refreshed = thumbs.icon_from_resource(box)

    assert refreshed.config["thumb"]["size"] != before_size
    after = thumbs.thumb_bytes(refreshed)
    assert after is not None and after != before
    assert len(cached(private_cache)) == 2


def test_a_missing_source_yields_no_miniature(tmp_path: Path) -> None:
    box = image_box(tmp_path / "gone.png")
    assert thumbs.icon_from_resource(box).config == {}
    assert thumbs.thumb_bytes(image_box(tmp_path / "gone.png")) is None


def test_types_without_a_miniature_are_left_alone(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("plain text")
    box = Box(name="Text", box_type="text", resource_uri=str(source))
    assert thumbs.icon_from_resource(box).config == {}


def test_a_box_with_no_resource_is_left_alone() -> None:
    assert thumbs.icon_from_resource(Box(name="Empty", box_type="image")).config == {}


def test_a_url_is_not_read_as_a_local_file() -> None:
    box = Box(name="Site", box_type="image", resource_uri="https://x.test/a.png")
    assert thumbs.resource_path(box) is None


def test_file_urls_and_plain_paths_both_resolve() -> None:
    assert thumbs.resource_path(Box(name="a", resource_uri="/tmp/a.png")).as_posix() == "/tmp/a.png"
    assert (
        thumbs.resource_path(Box(name="b", resource_uri="file:///tmp/a%20b.pdf")).name == "a b.pdf"
    )


def test_a_deleted_cache_entry_is_rebuilt(tmp_path: Path, private_cache: Path) -> None:
    source = write_image(tmp_path / "pic.png")
    box = thumbs.icon_from_resource(image_box(source))
    cached = thumbs.path_for(box, thumbs.current_source(box))
    assert cached is not None
    cached.unlink()
    assert thumbs.thumb_bytes(box) is not None
    assert cached.exists()


def test_current_source_is_none_when_the_file_moved(tmp_path: Path) -> None:
    source = write_image(tmp_path / "pic.png")
    box = thumbs.icon_from_resource(image_box(source))
    source.unlink()
    assert thumbs.current_source(box) is None


def test_current_source_is_none_without_a_record() -> None:
    assert thumbs.current_source(Box(name="Plain")) is None
