"""Resource analysis: when to propose a better type, and when to keep quiet."""

from __future__ import annotations

import pytest

from boxganizer import infer

DIRS = {"/tmp", "/srv/data"}


def suggest(uri: str, current: str = "box"):
    return infer.suggest(uri, current, is_dir=lambda path: str(path) in DIRS)


@pytest.mark.parametrize(
    ("uri", "current", "expected"),
    [
        ("/tmp/report.pdf", "file", "pdf"),
        ("/tmp/report.pdf", "box", "pdf"),
        ("/tmp/report.PDF", "file", "pdf"),
        ("/tmp/notes.md", "file", "markdown"),
        ("/tmp/photo.png", "file", "image"),
        ("/tmp/archive.zip", "box", "archive"),
        ("https://example.com", "box", "web"),
        ("mqtt://broker:1883/cellar/temp", "box", "mqtt"),
        ("ssh://build-host", "box", "ssh"),
        ("/tmp", "box", "directory"),
        ("/srv/data", "file", "directory"),
    ],
)
def test_a_specific_type_is_proposed(uri: str, current: str, expected: str) -> None:
    found = suggest(uri, current)
    assert found is not None and found.key == expected


@pytest.mark.parametrize(
    ("uri", "current"),
    [
        ("/tmp/photo.png", "image"),
        ("/tmp/doc.pdf", "pdf"),
        ("https://example.com", "web"),
        ("mqtt://b/topic", "mqtt"),
        ("/tmp", "directory"),
        ("/tmp/mystery-thing", "file"),
        ("gopher://old.test", "box"),
        ("", "box"),
        ("   ", "box"),
        (None, "box"),
    ],
)
def test_no_advice_when_the_choice_is_already_right_or_unknowable(
    uri: str | None, current: str
) -> None:
    assert suggest(uri, current) is None


def test_a_choice_that_would_reject_the_resource_is_proposed_around() -> None:
    found = suggest("https://api.test/v1/value", "mqtt")
    assert found is not None and found.key == "web"


def test_the_reason_says_why() -> None:
    found = suggest("/tmp/scan.pdf", "file")
    assert found is not None
    assert ".pdf" in found.reason
    assert found.label == "PDF"
    assert found.icon


def test_a_proposal_is_always_a_registered_selectable_type() -> None:
    from boxganizer import boxtypes

    for uri in ("/a/b.pdf", "/a/b.md", "https://x", "mqtt://x/y", "/tmp"):
        found = suggest(uri, "box")
        assert found is not None
        assert boxtypes.known(found.key)
        assert not boxtypes.get(found.key).is_grouping()


def test_schemes_and_paths_are_split() -> None:
    assert infer.split_resource("file:///tmp/a%20b.pdf") == ("file", "/tmp/a b.pdf")
    assert infer.split_resource("https://x.test/y") == ("https", "/y")
    assert infer.split_resource("/tmp/plain") == ("", "/tmp/plain")
