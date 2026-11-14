"""A rule about the test suite itself, checked by reading its imports.

`make test` runs bare `pytest`, which does not put the repository root on `sys.path`: the
`tests` directory is what gets inserted there, one entry per test file. So
`from tests.x import y` resolves when a person types `python -m pytest` from the root and
fails with `ModuleNotFoundError: No module named 'tests'` under the project's own gate
command. That is not hypothetical — a test written that way reported a green suite on one
invocation and a broken one on the other, and the green number was the one quoted in a report.

Shared helpers belong in `conftest.py` as fixtures: pytest loads that file by path, so a
fixture needs no import at all. The check reads the AST rather than grepping, so a comment or
a docstring that merely mentions the pattern cannot fail the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).parent


def _imported_modules(path: Path) -> list[str]:
    """Every module this test file imports, by dotted name."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_no_test_module_imports_through_the_tests_package() -> None:
    """`tests` is not a package. Use a fixture, or import nothing."""
    offenders: list[str] = []
    for module in sorted(TESTS.glob("test_*.py")):
        for name in _imported_modules(module):
            if name == "tests" or name.startswith("tests."):
                offenders.append(f"{module.name} imports {name}")
    detail = "; ".join(offenders)
    assert not offenders, (
        "these would fail under bare `pytest`, which is what `make test` runs: " + detail
    )


def test_the_shared_commit_counter_is_a_fixture_not_an_import() -> None:
    """The helper that counts durability points lives where no path can break it."""
    conftest = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert "def commit_counter" in conftest, (
        "the commit counter belongs in conftest.py as a fixture, so that any test module "
        "can use it without importing across the tests directory"
    )


def test_the_suite_stays_importable_by_bare_pytest() -> None:
    """No `tests/__init__.py`, and a comment saying why that is load-bearing.

    Adding one would make the forbidden import work under `python -m pytest` while still
    failing under a bare run from another directory — the exact split that let a broken gate
    look green once already. If somebody wants a package here, this is the test that has to
    change on purpose, with the reasoning, rather than by accident.
    """
    assert not (TESTS / "__init__.py").exists(), (
        "see tests/test_suite_layout.py: an __init__.py makes `from tests.x import y` look "
        "as if it works, and it still fails under a bare `pytest` run"
    )
