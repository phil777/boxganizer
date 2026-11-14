"""The reproduction tool's own claims, pinned.

`tools/repro_reindex.py` is the artifact a diagnosis rests on, and its VERDICT line is a claim in
words: "symptom reproduced". It printed that whenever the headline did not move — including on a
library where the pass had just cleared everything and nothing was offered at all. A tool that cries
wolf on a clean run is worse than no tool, because the next "SYMPTOM" gets read as noise. So the
verdict is a function and its cases are tests, rather than a branch exercised by waiting 407 seconds
for the pre-P1 build to finish a pass.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

TOOL = Path(__file__).resolve().parent.parent / "tools" / "repro_reindex.py"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("repro_reindex", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: A library where the pane offers 67 and a pass leaves all 67 offered: the reported bug.
STUCK = {"stale": 67, "untouched": 0, "wanted": 839, "indexed": 700,
         "missing": {"work": 67, "work_stale": 67, "resources": 0}}
#: A library with nothing to offer: one gone resource, zero work, 767 read.
CLEAN = {"stale": 1, "untouched": 65, "wanted": 841, "indexed": 767,
         "missing": {"work": 0, "work_stale": 0, "resources": 1}}
#: The same numbers as the pre-P1 build answers them: no `missing` at all, gone files still
#: inside `untouched`, which is the arithmetic that could never come down.
OLD_BUILD = {"stale": 69, "untouched": 7, "wanted": 839, "indexed": 818,
             "missing_roots": [{"waiting": 64}]}


def test_work_that_survives_the_pass_is_called_the_symptom() -> None:
    tool = _tool()
    line = tool.verdict(STUCK, STUCK)
    assert "SYMPTOM" in line and "67" in line, line


def test_a_clean_library_is_not_reported_as_the_symptom() -> None:
    """The false alarm this tool produced, now impossible.

    Before: two identical status answers and `headline(before) == headline(after)` printed
    "UNCHANGED — symptom reproduced", on a run where the pane had offered nothing to begin with.
    """
    tool = _tool()
    line = tool.verdict(CLEAN, CLEAN)
    assert "SYMPTOM" not in line, line
    assert "not the symptom" in line, line


def test_a_pass_that_took_the_offer_down_is_reported_as_clearing_it() -> None:
    tool = _tool()
    line = tool.verdict(STUCK, CLEAN)
    assert "cleared" in line and "67" in line, line


def test_the_pre_p1_build_still_reads_as_the_symptom() -> None:
    """Without `missing`, the old arithmetic decides — and on that build it was the honest reading.

    Its pane really did say "12 to reindex" and really did not move it, because the gone files sat
    inside `untouched`. Falling back to that formula is what lets `--old-build` reproduce the
    complaint rather than merely describe it.
    """
    tool = _tool()
    assert tool.offer(OLD_BUILD) == 12
    assert "SYMPTOM" in tool.verdict(OLD_BUILD, OLD_BUILD)


def test_the_pane_and_the_old_rule_are_printed_as_two_different_numbers() -> None:
    """ADR 44 changed what the headline means; the tool has to show both or it rewrites history.

    `reindex_diagnosis.md` is un-amended above the split and quotes the old figures, which is only
    honest while the tool prints both side by side.
    """
    tool = _tool()
    assert tool.headline(CLEAN) == "767 indexed", tool.headline(CLEAN)
    # the same numbers, the old arithmetic: `stale + untouched`, with no missing root named in this
    # answer to subtract. Sixty-six things offered that no reindex can do — which is the whole
    # complaint, printed next to the pane's own "nothing offered" so the difference is visible.
    assert tool.headline_pre44(CLEAN) == "66 to reindex", tool.headline_pre44(CLEAN)
    # and with no `missing` the pane rule follows the page's fallback, not silence
    assert tool.headline(OLD_BUILD) == "12 to reindex", tool.headline(OLD_BUILD)


def test_headline_wording_follows_the_pages_own_two_nouns() -> None:
    """The page says "reindex" only for something already read that has since changed.

    A file the index has never opened is not being re-read, and a line that says otherwise teaches
    people to distrust it.
    """
    tool = _tool()
    never_opened = {"stale": 0, "untouched": 5, "wanted": 9, "indexed": 4,
                    "missing": {"work": 5, "work_stale": 0, "resources": 0}}
    assert tool.headline(never_opened) == "5 to index", tool.headline(never_opened)
