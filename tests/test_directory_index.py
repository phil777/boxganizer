"""Indexing a folder: the files inside become resources, and the folder answers for them.

See ADR 16. What is being held to here is that a folder's `index_it` means "bring in what is
inside", that the ordinary change-detection works on those files unchanged, and that nothing
the folder brought in outlives the reason it was there.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxganizer import search as text_search
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app

TEXT = ("# Notes\n\nthe boiler pressure gauge leaks\n", "an order for cement mixer parts\n")


@pytest.fixture
def library(tmp_path: Path) -> Iterator[BoxStore]:
    """A folder of notes, nested, with the things a walk must not read inside it.

    `notes/.git/`, `notes/escape.md` (a link to a file outside the folder), `notes/photo.png`
    and the `outside.md` it points at are all there to be *absent* from the index.
    """
    root = tmp_path / "library"
    notes = root / "notes"
    (notes / "deep" / "deeper").mkdir(parents=True)
    (notes / ".git").mkdir()
    (root / "elsewhere").mkdir()

    (notes / "a.md").write_text(TEXT[0], encoding="utf-8")
    (notes / "deep" / "b.txt").write_text(TEXT[1], encoding="utf-8")
    (notes / "deep" / "deeper" / "c.md").write_text(
        "the anode needs replacing soon\n", encoding="utf-8"
    )
    (notes / ".git" / "config.txt").write_text("git internals about the anode\n", encoding="utf-8")
    (notes / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    outside = root / "outside.md"
    outside.write_text("a file outside the folder, about the anode\n", encoding="utf-8")
    (notes / "escape.md").symlink_to(outside)

    store = BoxStore(tmp_path / "dirs.db", content_root=root)
    yield store
    store.close()


def folder_box(store: BoxStore, name: str = "Notes", path: str = "notes") -> Box:
    where = store_root(store) / path
    box = store.add(Box(name=name, box_type="indexed-directory", resource_uri=str(where)))
    store.set_index_flag(box.resource_id, True)
    return box


def store_root(store: BoxStore) -> Path:
    return Path(store.content_root)  # type: ignore[arg-type]


def names(store: BoxStore, query: str) -> list[str]:
    """Which boxes answer for this word, through their own files or their folders'."""
    answers: list[str] = []
    for resource_id, _score, _passage in store.content_search(query, limit=20):
        for box, _inside in store.boxes_for_text(resource_id):
            if box.name not in answers:
                answers.append(box.name)
    return answers


# -- what a walk brings in ----------------------------------------------------


def test_a_folder_brought_into_the_index_brings_its_files(library: BoxStore) -> None:
    box = folder_box(library)
    report = text_search.rebuild(library)
    assert report.in_index == 3  # a.md, deep/b.txt, deeper/c.md — and not the folder
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    inside = sorted(
        child.reference.replace(f"{store_root(library)}/notes/", "")
        for child in library.linked_resources(int(parent.id))
    )
    assert inside == ["a.md", "deep/b.txt", "deep/deeper/c.md"]
    assert names(library, "cement") == ["Notes"]


def test_the_folder_itself_is_not_counted_as_an_unreadable_file(library: BoxStore) -> None:
    """It has no words because it is a folder, which is a different thing from a failure."""
    folder_box(library)
    text_search.rebuild(library)
    counts = library.index_counts()
    assert counts["folders"] == 1
    assert counts["unreadable"] == 0
    assert counts["indexed"] == 3


def test_what_a_walk_must_leave_out_is_left_out(library: BoxStore) -> None:
    folder_box(library)
    text_search.rebuild(library)
    references = [item.reference for item in library.indexed_resources()]
    assert not any(".git" in ref for ref in references), "version control metadata is not a note"
    assert not any("outside.md" in ref for ref in references), (
        "a link out of the folder indexes nothing"
    )
    assert not any(ref.endswith(".png") for ref in references), "no words come out of a picture"
    assert names(library, "anode") == ["Notes"]  # the real nested note, not the .git one


def test_a_file_the_person_asked_to_be_left_alone_is_not_brought_in(library: BoxStore) -> None:
    """The walk reads the same rule everyone else does: `index_it` means what it says."""
    notes = store_root(library) / "notes"
    (notes / "private.md").write_text("my medical results about the anode\n", encoding="utf-8")
    box = folder_box(library)
    text_search.rebuild(library)
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    library.set_index_flag(str(notes / "private.md"), False)
    # an explicit no survives the next walk: the folder does not quietly re-opt it in
    text_search.rebuild(library, only_stale=False)
    private = library.resource(str(notes / "private.md"))
    assert private is not None and private.index_it is False


# -- what a walk notices later ------------------------------------------------


def test_a_new_file_two_levels_down_is_found_by_a_pass(library: BoxStore) -> None:
    """A nested addition does not touch the top folder's mtime, so a walk is what finds it."""
    folder_box(library)
    text_search.rebuild(library)
    assert names(library, "flush") == []
    (store_root(library) / "notes" / "deep" / "deeper" / "d.md").write_text(
        "a fresh note about the flush valve\n", encoding="utf-8"
    )
    library.check_observations()
    text_search.rebuild(library, only_stale=True)
    assert names(library, "flush") == ["Notes"]


def test_a_deleted_file_stops_answering_searches(library: BoxStore) -> None:
    """Words from a file that is gone must not go on being offered."""
    folder_box(library)
    text_search.rebuild(library)
    assert names(library, "anode") == ["Notes"]
    (store_root(library) / "notes" / "deep" / "deeper" / "c.md").unlink()
    library.check_observations()
    text_search.rebuild(library, only_stale=True)
    assert names(library, "anode") == []


def test_a_file_inside_two_folders_survives_one_of_them_being_unindexed(library: BoxStore) -> None:
    """Folders inside folders: one file, two links, one copy of its words.

    `notes` and `notes/deep` are both indexed, so `deep/b.txt` was found by each of them and
    is one resource with two links. Unindexing the outer folder must keep the file and its
    text, because the inner one still holds it up — and unindexing both must let it go.
    """
    outer = folder_box(library, name="Notes")
    inner = folder_box(library, name="Deep", path="notes/deep")
    text_search.rebuild(library)
    shared = store_root(library) / "notes" / "deep" / "b.txt"
    assert names(library, "cement") == ["Notes", "Deep"] or names(library, "cement") == [
        "Deep",
        "Notes",
    ]

    library.set_index_flag(int(outer.resource_id or 0), False)
    text_search.rebuild(library)
    kept = library.resource(str(shared))
    assert kept is not None and kept.index_it is True
    assert names(library, "cement") == ["Deep"]

    library.set_index_flag(int(inner.resource_id or 0), False)
    text_search.rebuild(library)
    assert library.resource(str(shared)) is None
    assert names(library, "cement") == []


def test_opting_a_folder_out_lets_go_the_files_only_it_brought_in(library: BoxStore) -> None:
    box = folder_box(library)
    text_search.rebuild(library)
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    child = library.linked_resources(int(parent.id))[0]

    library.set_index_flag(int(parent.id), False)
    text_search.rebuild(library)

    assert library.linked_resources(int(parent.id)) == []
    # nothing points at that file and no folder claims it now, so its row is gone with them
    assert library.resource(int(child.id)) is None


def test_an_ordinary_box_pointing_at_a_file_keeps_it_when_the_folder_lets_go(
    library: BoxStore,
) -> None:
    """A file the library also holds directly is not the folder's to discard."""
    box = folder_box(library)
    target = store_root(library) / "notes" / "a.md"
    library.add(Box(name="The gauge note", box_type="markdown", resource_uri=str(target)))
    text_search.rebuild(library)
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    library.set_index_flag(int(parent.id), False)
    text_search.rebuild(library)
    assert library.resource(str(target)) is not None
    assert names(library, "gauge") == ["The gauge note"]


def test_a_walk_that_reaches_its_limit_so_says(tmp_path: Path) -> None:
    from boxganizer import resources as address

    root = tmp_path / "many"
    root.mkdir()
    for index in range(12):
        (root / f"n{index}.md").write_text(f"note {index}\n", encoding="utf-8")
    files, note = address.walk_files(root, confine=tmp_path, limit=5)
    assert len(files) == 5
    assert "stopped after 5 files" in note


def test_the_everyday_door_does_not_walk_an_unchanged_folder(library: BoxStore) -> None:
    """Saving a box must not relist a big tree; the deliberate pass does.

    Discovery is the price, and it is paid at the one moment a person asks for an
    indexation — which is why the panel's action says what it runs.
    """
    box = folder_box(library)
    text_search.rebuild(library)  # walks
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    (store_root(library) / "notes" / "deep" / "deeper" / "c.md").unlink()
    # touch nothing else: the folder's own mtime is what the cheap door looks at
    note = text_search.sync_resource(library, int(parent.id), root=store_root(library))
    assert note == ""
    assert len(library.linked_resources(int(parent.id))) == 3  # nothing re-walked


# -- the answers --------------------------------------------------------------


def test_the_folder_answers_for_its_files_and_names_the_file(library: BoxStore) -> None:
    folder_box(library)
    text_search.rebuild(library)
    hits = library.content_search("cement", limit=5)
    assert hits
    resource_id, _score, _passage = hits[0]
    answers = library.boxes_for_text(resource_id)
    assert [(box.name, inside) for box, inside in answers] == [("Notes", "deep/b.txt")]


def test_a_box_that_points_at_a_file_answers_without_a_file_name(library: BoxStore) -> None:
    target = store_root(library) / "notes" / "a.md"
    library.add(Box(name="Gauge", box_type="markdown", resource_uri=str(target)))
    text_search.rebuild(library)
    hits = library.content_search("gauge", limit=5)
    resource_id, _score, _passage = hits[0]
    assert [(box.name, inside) for box, inside in library.boxes_for_text(resource_id)] == [
        ("Gauge", None)
    ]


def test_the_page_reports_which_file_in_the_folder_answered(library: BoxStore) -> None:
    folder_box(library)
    with TestClient(create_app(library, content_root=store_root(library))) as client:
        text_search.rebuild(library)
        hits = client.get("/api/search", params={"q": "cement"}).json()
        assert [hit["name"] for hit in hits] == ["Notes"]
        assert hits[0]["match"]["how"] == "content"
        assert hits[0]["match"]["in_file"] == "deep/b.txt"
        status = client.get("/api/index/status", params={"check": True}).json()
        assert status["folders"] == 1
        assert status["unreadable"] == 0


def test_a_folder_reports_the_files_it_holds_when_asked_to_be_indexed(
    library: BoxStore,
) -> None:
    """The dialog should not promise words from a folder it cannot read."""
    with TestClient(create_app(library, content_root=store_root(library))) as client:
        answer = client.get(
            "/api/index-suggestion",
            params={"resource": str(store_root(library) / "notes"), "box_type": "directory"},
        ).json()
        assert answer["index_it"] is True
        assert "folder" in answer["reason"].lower() and "inside" in answer["reason"].lower()
        assert answer["shared"] is False


# --- a folder that is not here is not an empty folder (ADR 41) -----------------


def test_a_folder_that_is_not_here_is_reported_and_kept(tmp_path: Path) -> None:
    """One pass over a missing folder changes nothing, and names the folder.

    Measured before the guard existed: `wanted` 85 -> 23 and index documents 80 -> 18 on a copy
    of a real library, from a pass nobody had asked to prune anything. The numbers are the test.
    """
    root = tmp_path / "library"
    old = root / "old"
    (old / "deep").mkdir(parents=True)
    for name in ("a.md", "b.txt", "deep/c.md"):
        (old / name).write_text(f"the boiler pressure gauge leaks in {name}\n", encoding="utf-8")
    (root / "live.md").write_text("an order for cement mixer parts\n", encoding="utf-8")
    store = BoxStore(tmp_path / "gone.db", content_root=root)
    try:
        folder = store.add(
            Box(name="Old folder", box_type="indexed-directory", resource_uri=str(old))
        )
        store.set_index_flag(folder.resource_id, True)
        store.add(Box(name="Live", box_type="markdown", resource_uri=str(root / "live.md")))
        text_search.rebuild(store, root=root)

        def counts() -> dict[str, int]:
            found = store.index_counts()
            found["documents"] = int(
                store._rows("SELECT count(*) AS n FROM resource_text")[0]["n"]
            )
            found["rows"] = int(store._rows("SELECT count(*) AS n FROM resources")[0]["n"])
            return found

        before = counts()
        assert before["wanted"] == 5 and before["documents"] == 4
        (old / "a.md").unlink()  # one file really deleted, from a folder that is present
        text_search.rebuild(store, root=root)
        assert counts()["documents"] == before["documents"] - 1, (
            "a deliberate delete must still leave the index"
        )
        kept = before["documents"] - 1
        after_delete = counts()  # the reference for everything below: one file is genuinely gone

        import shutil

        shutil.rmtree(old)
        store.check_observations()
        after_check = counts()
        assert after_check["wanted"] == after_delete["wanted"], "the check un-indexed something"
        assert after_check["documents"] == kept, "the check erased a document"
        assert after_check["rows"] == after_delete["rows"]

        report = text_search.rebuild(store, root=root)
        after = counts()
        assert after["wanted"] == after_delete["wanted"], f"a pass un-indexed rows: {after}"
        assert after["documents"] == kept, f"a pass erased the index: {after['documents']}"
        assert after["rows"] == after_delete["rows"]
        assert after["stale"] >= 3, "gone files should still be remembered as out of date"
        assert any("not a folder" in note for _ref, note in report.skipped), report.skipped

        gone = store.gone_populations()
        assert len(gone) == 1
        assert gone[0]["reference"] == str(old)
        assert gone[0]["files"] == 2, gone  # b.txt and deep/c.md, not the deleted a.md
        assert gone[0]["boxes"] >= 1
        assert store.doc_index_agreement() == {
            "claimed_without_words": 0,
            "words_unclaimed": 0,
        }
    finally:
        store.close()


def test_a_gone_folder_is_named_work_and_not_offered_as_work(tmp_path: Path) -> None:
    """`why: "gone"`, `fixable: false` -- and still in the list, because it is still true.

    The row is not hidden: a person looking at the pending dialog should see the file and read
    why reindexing will not help. What changes is the button, which offers only fixable ones.
    """
    root = tmp_path / "library"
    old = root / "old"
    old.mkdir(parents=True)
    (old / "a.md").write_text("the boiler pressure gauge leaks\n", encoding="utf-8")
    store = BoxStore(tmp_path / "named.db", content_root=root)
    try:
        folder = store.add(
            Box(name="Old", box_type="indexed-directory", resource_uri=str(old))
        )
        store.set_index_flag(folder.resource_id, True)
        text_search.rebuild(store, root=root)
        old.rename(root / "old-moved-elsewhere")
        store.check_observations()
        items = store.needing_index()
        gone = [item for item in items if item["why"] == "gone"]
        assert len(gone) == 1, items
        assert gone[0]["fixable"] is False
        assert "no longer here" in gone[0]["words"]
        assert [item for item in items if item["fixable"]] == []
    finally:
        store.close()


def test_a_folder_that_comes_back_needs_nothing_un_set(tmp_path: Path) -> None:
    """Re-arming is restoring the folder and asking -- there is no flag to clear.

    This is why the gone state is derived from the disk on every look instead of recorded: a
    decision kept in the rows outlives the reason it was made, and the person who copied the
    backup back would find their index still refusing to read files that are plainly there.
    """
    root = tmp_path / "library"
    old = root / "old"
    old.mkdir(parents=True)
    (old / "a.md").write_text("the boiler pressure gauge leaks\n", encoding="utf-8")
    store = BoxStore(tmp_path / "return.db", content_root=root)
    try:
        folder = store.add(
            Box(name="Old", box_type="indexed-directory", resource_uri=str(old)
                )
        )
        store.set_index_flag(folder.resource_id, True)
        text_search.rebuild(store, root=root)
        backup = tmp_path / "backup"
        old.rename(backup)
        store.check_observations()
        assert len(store.gone_populations()) == 1
        assert store.needing_index()[0]["why"] == "gone"
        backup.rename(old)  # the drive is plugged back in, the restore finished
        store.check_observations()
        assert store.gone_populations() == []
        # Nothing to do, and that is the payoff: because the words were kept, the returning
        # file is already indexed and no pass is owed. The list being clean is the proof.
        assert [item for item in store.needing_index() if item["why"] == "gone"] == []
        assert names(store, "boiler"), "the index should never have lost this file"

        # and when it genuinely changes, it is ordinary work again -- not still exiled
        (old / "a.md").write_text("the boiler pressure gauge leaks and drips\n", encoding="utf-8")
        os.utime(old / "a.md", (time.time() + 30, time.time() + 30))
        store.check_observations()
        items = store.needing_index()
        assert items and items[0]["why"] == "changed", items
        assert items[0]["fixable"] is True
        text_search.rebuild(store, root=root)
        assert names(store, "drips")
    finally:
        store.close()


def test_the_status_endpoint_names_the_gone_folder(tmp_path: Path) -> None:
    """The API carries the sentence the panel shows, so the page never has to guess.

    Empty before the folder goes, populated after, on both endpoints the panel reads -- and
    with nothing removed from `wanted` in between, which is the part an endpoint is most
    tempting to "fix" by quietly shrinking the number.
    """
    root = tmp_path / "library"
    old = root / "old"
    old.mkdir(parents=True)
    (old / "a.md").write_text("the boiler pressure gauge leaks\n", encoding="utf-8")
    (old / "b.md").write_text("an order for cement mixer parts\n", encoding="utf-8")
    store = BoxStore(tmp_path / "api.db", content_root=root)
    try:
        folder = store.add(
            Box(name="Old", box_type="indexed-directory", resource_uri=str(old))
        )
        store.set_index_flag(folder.resource_id, True)
        text_search.rebuild(store, root=root)
        with TestClient(create_app(store, content_root=root)) as client:
            before = client.get("/api/index/status").json()
            assert before["missing_roots"] == []
            wanted = before["wanted"]
            assert wanted == 3  # two files and the folder

            import shutil

            shutil.rmtree(old)
            # an explicit action, not a draw: reading the status no longer looks at the files
            client.post("/api/index/check")
            status = client.get("/api/index/status").json()
            assert [gone["files"] for gone in status["missing_roots"]] == [2]
            # Both numbers, because they answer different questions: `files` is what lives
            # there, `waiting` is what the panel was about to call work.
            assert [gone["waiting"] for gone in status["missing_roots"]] == [2]
            assert status["wanted"] == wanted, "the endpoint un-indexed something on its own"

            pending = client.get("/api/index/pending").json()
            assert len(pending["missing_roots"]) == 1
            assert {item["why"] for item in pending["items"]} == {"gone"}
            assert all(not item["fixable"] for item in pending["items"])
    finally:
        store.close()


# --- ADR 44: gone-but-referenced resources are a number of their own, not work ---


def _gone_and_a_changed(tmp_path: Path, name: str = "acct.db") -> tuple[BoxStore, Path, Path]:
    """A library with two files under a folder that will go, and one file that will merely change.

    The two populations have to be told apart by the tests as carefully as the panel tells them
    apart: one is a fact about this machine, the other is work.
    """
    root = tmp_path / "library"
    gone = root / "archive"
    live = root / "live"
    (gone / "deep").mkdir(parents=True)
    live.mkdir()
    (gone / "one.md").write_text("the boiler pressure gauge leaks\n", encoding="utf-8")
    (gone / "deep" / "two.md").write_text("an order for cement mixer parts\n", encoding="utf-8")
    keep = live / "three.md"
    keep.write_text("the anode needs replacing soon\n", encoding="utf-8")
    store = BoxStore(tmp_path / name, content_root=root)
    folder = store.add(
        Box(name="Archive", box_type="indexed-directory", resource_uri=str(gone))
    )
    store.set_index_flag(folder.resource_id, True)
    store.add(Box(name="Live", box_type="markdown", resource_uri=str(keep)))
    text_search.rebuild(store, root=root)
    return store, gone, keep


def test_gone_files_are_counted_separately_and_nothing_else_moves(tmp_path: Path) -> None:
    """The separate count, and the two numbers it must not disturb.

    `wanted` and `stale` are asserted *unchanged on purpose*: the easy way to make the headline
    honest is to stop counting the hopeless files, and that is the silent decision ADR 41 refused
    and ADR 44 keeps refused. Only the offered-work split moves.
    """
    import shutil

    store, gone, _keep = _gone_and_a_changed(tmp_path)
    try:
        before = store.index_counts()
        assert store.missing_watch()["resources"] == 0

        shutil.rmtree(gone)
        store.check_observations()

        missing = store.missing_watch()
        # 2 files and the folder itself: the panel's `stale` counts all three, so a count that
        # covered only the files would leave one resource unexplained in the headline.
        assert missing["resources"] == 3, missing
        assert missing["stray"] == 0
        assert [root["resources"] for root in missing["roots"]] == [3], missing
        # the folder's box depends on what was inside it, so "0 boxes" would be a lie
        assert missing["boxes"] == 1, missing
        # both numbers agree about the total they are splitting, and none of it is work
        assert missing["candidates"] == missing["work"] + 3, missing
        assert missing["work"] == 0

        after = store.index_counts()
        assert after["wanted"] == before["wanted"], "the count of what is wanted moved"
        assert after["stale"] == before["stale"] + 3, (
            "the gone files were not remembered as stale"
        )
        assert after["indexed"] == before["indexed"], "a document was lost by noticing a folder"
        assert store.doc_index_agreement() == {
            "claimed_without_words": 0,
            "words_unclaimed": 0,
        }
    finally:
        store.close()


def test_work_and_hopeless_are_split_by_what_a_pass_can_actually_read(tmp_path: Path) -> None:
    """One changed file and two gone ones: the split is 1 / 2, not 3 / 0.

    This is the arithmetic behind the headline the user sees. On their library the same split came
    out 3 readable against 64 hopeless, which is why pressing Reindex 21 times changed nothing.
    """
    import os
    import shutil

    store, gone, keep = _gone_and_a_changed(tmp_path, "split.db")
    try:
        shutil.rmtree(gone)
        keep.write_text("the anode needs replacing and the gate too\n", encoding="utf-8")
        os.utime(keep, (keep.stat().st_atime, keep.stat().st_mtime + 40))
        store.check_observations()

        missing = store.missing_watch()
        # 2 gone files + the gone folder = 3 unreachable; the live changed file is the only work
        assert missing["resources"] == 3, missing
        assert missing["work"] == 1, missing
        assert missing["stray"] == 0
        assert missing["candidates"] == 4

        # and a pass does the work and leaves the hopeless alone
        text_search.rebuild(store, root=root_of(store), only_stale=True)
        store.check_observations()
        after = store.missing_watch()
        assert after["work"] == 0, after
        assert after["resources"] == 3, "a pass changed what is on the disk"
        assert store.index_counts()["wanted"] == 4, "a pass un-indexed something"
    finally:
        store.close()


def root_of(store: BoxStore) -> Path:
    return Path(store.content_root)  # type: ignore[arg-type]


def test_restoring_the_folder_moves_the_files_back_into_work(tmp_path: Path) -> None:
    """Re-arming is still just restoring the folder: the count has nothing to un-set.

    Checked in both directions in one test, because the whole reason the population is derived
    from the disk rather than stored is that a restore has to work without a repair step.
    """
    import shutil

    store, gone, _keep = _gone_and_a_changed(tmp_path, "restore.db")
    try:
        backup = tmp_path / "backup-archive"
        shutil.move(str(gone), str(backup))
        store.check_observations()
        assert store.missing_watch()["resources"] == 3

        shutil.move(str(backup), str(gone))
        store.check_observations()
        back = store.missing_watch()
        assert back["resources"] == 0, back
        assert back["roots"] == []
        assert back["work"] == store.index_counts()["stale"]
    finally:
        store.close()


def test_both_index_endpoints_carry_the_separate_number(tmp_path: Path) -> None:
    """Status and pending agree, because both ask the same question of the same store."""
    import shutil

    store, gone, _keep = _gone_and_a_changed(tmp_path, "api-missing.db")
    try:
        with TestClient(create_app(store, content_root=root_of(store))) as client:
            assert client.get("/api/index/status").json()["missing"]["resources"] == 0
            shutil.rmtree(gone)
            client.post("/api/index/check")
            status = client.get("/api/index/status").json()
            pending = client.get("/api/index/pending").json()
            assert status["missing"]["resources"] == 3, status["missing"]
            assert status["missing"]["boxes"] == 1
            assert status["missing"]["roots"][0]["display"] == "archive"
            assert pending["missing"]["resources"] == 3
            # the headline's source of truth: work excludes them, `wanted` does not
            assert status["missing"]["work"] == status["stale"] - status["missing"]["resources"]
            assert status["wanted"] == 4
    finally:
        store.close()


def test_the_gone_line_names_the_boxes_that_still_point_at_them(tmp_path: Path) -> None:
    """Which boxes, not just how many.

    The dialog said "65 resources are gone and 1 box still points at them". The number is honest
    and useless on its own: the person reading it has to go find the box, and the box is the thing
    they can actually do something with — restore the folder, or stop indexing it. So the same read
    that counts them also hands over who they are, and `boxes` stays the count it was, derived from
    that same set rather than from a second query that could disagree with the list.

    Two populations are covered because they are gathered differently: a box filed on a gone folder
    is found by prefix, and a box filed on one deleted file under a folder that is still here is
    found by looking at the disk.
    """
    import shutil

    store, gone, keep = _gone_and_a_changed(tmp_path, "holders.db")
    try:
        # a second box, filed on a file *inside* the folder that will go: prefix-matched, and one
        # of the resources the folder takes with it
        inside = store.add(
            Box(
                name="Old ledger",
                box_type="markdown",
                resource_uri=str(gone / "deep" / "two.md"),
            )
        )
        store.set_index_flag(inside.resource_id, True)
        text_search.rebuild(store, root=root_of(store))
        assert store.missing_watch()["holders"] == []

        shutil.rmtree(gone)
        keep.unlink()  # the stray: its folder is still here, its file is not
        store.check_observations()

        missing = store.missing_watch()
        named = {entry["name"]: entry for entry in missing["holders"]}
        assert set(named) == {"Archive", "Old ledger", "Live"}, missing["holders"]
        assert missing["boxes"] == len(missing["holders"]), (
            "the count and the list were read from different facts"
        )
        assert len({entry["uuid"] for entry in missing["holders"]}) == 3, "a box named twice"
        # the folder's box and the file's box both report the address they point at, which is the
        # only thing that tells the reader what to restore
        assert named["Archive"]["reference"] == str(gone)
        assert named["Old ledger"]["reference"] == str(gone / "deep" / "two.md")
        assert named["Live"]["reference"] == str(keep)
        assert all(entry["uuid"] for entry in missing["holders"]), "a holder nobody can open"
        # and the names arrive in the order the dialog will draw them
        assert [entry["name"] for entry in missing["holders"]] == ["Archive", "Live", "Old ledger"]
        # the split this list is a detail of has not moved
        assert missing["work"] == 0, missing
        assert missing["resources"] == 4, missing
    finally:
        store.close()
