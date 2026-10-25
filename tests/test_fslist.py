"""The filesystem listing the explorer is built on."""

from __future__ import annotations

from pathlib import Path

import pytest

from boxganizer import fslist


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "photos").mkdir()
    (tmp_path / "notes.md").write_text("# hi\n")
    (tmp_path / "clip.mp4").write_bytes(b"\x00")
    (tmp_path / ".private").write_text("x")
    return tmp_path


def test_directories_come_first_then_names(tree: Path) -> None:
    assert [e.name for e in fslist.list_directory(tree)] == ["photos", "clip.mp4", "notes.md"]


def test_dot_files_are_hidden_unless_the_box_points_at_a_dot_folder(tree: Path) -> None:
    dotdir = tree / ".config"
    dotdir.mkdir()
    (dotdir / ".env").write_text("x")
    assert ".private" not in [e.name for e in fslist.list_directory(tree)]
    assert [e.name for e in fslist.list_directory(dotdir)] == [".env"]


def test_each_entry_knows_its_kind_type_and_absolute_path(tree: Path) -> None:
    entries = {e.name: e for e in fslist.list_directory(tree)}
    assert entries["clip.mp4"].kind == "video"
    assert entries["clip.mp4"].box_type == "video"
    assert entries["notes.md"].box_type == "markdown"
    assert entries["photos"].box_type == "directory"
    assert entries["notes.md"].path == str(tree / "notes.md")


@pytest.mark.parametrize("sub", ["../escape", "/etc", "photos/../../etc", "..", "a/../../b"])
def test_a_path_never_leaves_the_box_directory(tree: Path, sub: str) -> None:
    with pytest.raises(fslist.OutsideRoot):
        fslist.safe_join(tree, sub)


def test_the_box_directory_itself_is_a_valid_target(tree: Path) -> None:
    assert fslist.safe_join(tree, "") == tree.resolve()
    assert fslist.safe_join(tree, ".") == tree.resolve()


def test_a_symlink_out_of_the_tree_is_refused(tree: Path) -> None:
    (tree / "link").symlink_to("/etc")
    with pytest.raises(fslist.OutsideRoot):
        fslist.safe_join(tree, "link/passwd")


def test_listing_a_file_instead_of_a_directory_is_empty(tree: Path) -> None:
    assert fslist.list_directory(tree, "notes.md") == []


def test_hidden_entries_can_be_asked_for(tree: Path) -> None:
    hidden = [e.name for e in fslist.list_directory(tree, include_hidden=True)]
    assert ".private" in hidden
    assert ".private" not in [e.name for e in fslist.list_directory(tree)]
