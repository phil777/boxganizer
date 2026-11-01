"""Finding a box by what is written inside it.

A box that holds a text file or a PDF has content beyond its name, and the search
field should reach it. Three things happen here:

**Extraction** — :func:`extract` reads the file behind a box and returns plain text.
Only types that say ``indexes_content`` are read, only inside the same confine the
file endpoints use, and only up to a bounded size: an index is meant to be quick,
and one 2 GB log file should not slow down every search of a thousand boxes.

**Query planning** — :func:`plan` turns what someone typed into a query for the
SQLite full-text index. Three widenings are applied to every word, in order of how
much they cost:

* the word itself, and its prefixed forms — the index stems, so ``running`` finds
  ``run`` without this module doing anything;
* **thesaurus** synonyms, from a small bundled set plus a file the user can extend;
* **fuzzy** forms, by comparing the word against the vocabulary the index has
  actually seen, so a mistyped ``kithcen`` still reaches ``kitchen``.

**Ranking** is left to SQLite's ``bm25``, with the rule that a box whose name matches
outranks a box that merely contains the word — which is why :func:`plan` keeps the
two query parts apart instead of OR-ing everything together.

Nothing here touches the database: :mod:`boxganizer.store` owns the SQL, and this
module owns the meaning of a query.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from boxganizer import boxtypes, config, paths
from boxganizer import resources as address
from boxganizer.models import Box

#: Largest file read into the index. Bigger ones are reported as too big, not guessed at.
MAX_TEXT_BYTES = 2_000_000

#: Most PDF pages read. A 4000-page manual is not worth the second it costs to index.
MAX_PDF_PAGES = 200

#: Words short enough that fuzzy matching them produces nonsense. "cat" → "cot"? No.
MIN_FUZZY_LENGTH = 4

#: How many near-misses a word may bring along.
MAX_FUZZY_ALTERNATIVES = 6

#: How close a vocabulary word must look to count as a misspelling of the query word.
FUZZY_CUTOFF = 0.72

#: Name of the file the user may write to add their own synonyms.
THESAURUS_FILENAME = "thesaurus.json"

# A quoted run first, so it stays one thing; then words, accents and digits included.
_TOKEN = re.compile(r'"[^"]*"|[0-9a-zà-ÿ]+', re.IGNORECASE)

#: Words that connect a query rather than being looked for in it. Typing them is how
#: someone says which boxes they meant; treating them as content would ask for a box
#: that contains the word "and", which is almost never the request.
_OPERATORS = frozenset({"and", "or", "not"})

#: A starter thesaurus: the pairs people actually search with, not a dictionary.
#: It is deliberately small and obvious. The real value is :func:`user_groups`,
#: which lets anyone extend it without touching code.
BUNDLED_GROUPS: tuple[tuple[str, ...], ...] = (
    ("kitchen", "cookroom", "galley"),
    ("cellar", "basement", "vault"),
    ("garage", "parking", "shed"),
    ("garden", "yard", "backyard"),
    ("attic", "loft", "garret"),
    ("house", "home", "dwelling"),
    ("book", "volume", "tome"),
    ("note", "memo", "reminder"),
    ("report", "account", "writeup"),
    ("invoice", "bill", "statement"),
    ("receipt", "ticket", "voucher"),
    ("password", "secret", "passphrase", "credential"),
    ("server", "machine", "host"),
    ("backup", "snapshot", "archive"),
    ("photo", "picture", "image"),
    ("video", "clip", "footage"),
    ("music", "song", "track", "audio"),
    ("meeting", "standup", "sync", "get-together"),
    ("project", "task", "job", "undertaking"),
    ("budget", "costs", "expenses", "spending"),
    ("travel", "trip", "journey", "vacation"),
    ("flight", "plane", "air"),
    ("train", "rail", "railway"),
    ("car", "automobile", "vehicle"),
    ("doctor", "physician", "clinician", "gp"),
    ("medicine", "drug", "medication", "prescription"),
    ("fitness", "exercise", "training", "workout"),
    ("recipe", "dish", "cookery"),
    ("plant", "shrub", "greenery"),
    ("dog", "canine", "puppy"),
    ("cat", "feline", "kitten"),
    ("child", "kid", "children"),
    ("family", "household", "kin"),
    ("friend", "mate", "buddy", "pal"),
    ("email", "mail", "message"),
    ("phone", "mobile", "telephone", "cell"),
    ("work", "job", "employment", "labour"),
    ("money", "cash", "funds", "currency"),
    ("insurance", "cover", "policy"),
    ("tax", "duty", "levy"),
    ("contract", "agreement", "deal", "compact"),
    ("law", "rule", "regulation", "statute"),
    ("start", "begin", "commence", "kick-off"),
    ("finish", "end", "complete", "conclude"),
    ("problem", "issue", "trouble", "defect", "bug"),
    ("error", "mistake", "fault", "bug"),
    ("idea", "concept", "notion", "thought"),
    ("plan", "scheme", "roadmap", "outline"),
    ("test", "trial", "check", "assessment"),
    ("repair", "fix", "mend", "patch"),
    ("learn", "study", "research", "discover"),
    ("teach", "instruct", "coach", "train"),
    ("write", "draft", "compose", "author"),
    ("read", "review", "browse", "study"),
    ("old", "ancient", "antique", "vintage"),
    ("new", "recent", "fresh", "modern"),
    ("big", "large", "huge", "enormous"),
    ("small", "little", "tiny", "compact"),
    ("fast", "quick", "rapid", "swift"),
    ("slow", "sluggish", "gradual"),
    ("cheap", "affordable", "inexpensive"),
    ("expensive", "costly", "dear", "pricey"),
    ("important", "key", "critical", "essential"),
    ("urgent", "pressing", "immediate", "asap"),
)


@dataclass(frozen=True)
class Token:
    """One thing the person typing asked for, or one instruction about how to ask."""

    text: str
    #: True when it came between quotes and has to be found exactly as written.
    phrase: bool = False
    #: "AND", "OR", "NOT" when this token connects two clauses, else "".
    operator: str = ""


@dataclass(frozen=True)
class Extracted:
    """What was read out of a box's file, or the reason there is nothing to read."""

    text: str = ""
    #: Set when the box *could* hold text but nothing was read. Said out loud, never guessed.
    note: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.text)


@dataclass(frozen=True)
class Plan:
    """A query, widened and ready for the full-text index."""

    #: The words and phrases as typed, lowercased, with the boolean words taken out.
    terms: tuple[str, ...] = ()
    #: The FTS5 MATCH expression for the *content*, or "" when there is nothing to ask.
    content_match: str = ""
    #: What each term was widened to, so the page can say "also searched for …".
    expanded: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: The words as the caller should look for them in names and fields. Matching there
    #: stays a plain substring search on purpose: widening "cellar" to "basement" in a
    #: *name* would return a box called "Cellar Plans" for the wrong reason — and the
    #: text inside it is searched anyway.
    name_terms: tuple[str, ...] = ()
    #: Runs that were put in quotes, which are looked for exactly as written.
    phrases: tuple[str, ...] = ()

    @property
    def worth_asking(self) -> bool:
        return bool(self.content_match)


# -- extraction ---------------------------------------------------------------


def indexes(box: Box) -> bool:
    """Whether the type says this box's file is made of words.

    The resource row has the last word in practice — see :func:`sync` — but a box on
    its own, before it is stored, is asked of its type: a text or a PDF yes, an image,
    a web page or a topic no.
    """
    if not boxtypes.known(box.box_type):
        return False
    return boxtypes.get(box.box_type).indexes_content and bool(box.resource_uri)


def extract(box: Box, *, root: Path | str | None = None) -> Extracted:
    """Read a box's file as text. A thin turn of :func:`extract_resource`."""
    return extract_resource(
        box.resource_uri or "",
        root=root,
        pdf=box.box_type == "pdf" or None,
    )


def extract_resource(
    reference: str,
    *,
    root: Path | str | None = None,
    pdf: bool | None = None,
    origin: str | None = None,
) -> Extracted:
    """Read what a resource points at, inside the usual confine.

    How to read it comes from the thing itself rather than from the box pointing at it:
    a name ending `.pdf` is read through the PDF text layer, any other path is tried as
    text and refused if it turns out to be bytes. That is deliberate — two boxes of
    different types pointing at one file get one reading, which is the whole reason the
    index is keyed by resource — and `pdf` lets a caller who knows better than a file
    name overrule it.

    Never raises: a missing, huge, unreadable or confined-away file is still a perfectly
    good thing for a box to point at, and the index has to carry on with the rest. What
    happened comes back in :attr:`Extracted.note`.
    """
    raw = (reference or "").strip()
    if not raw:
        return Extracted(note="the resource holds no address")
    where = origin or address.origin_of(raw)
    if where == address.REMOTE:
        return Extracted(note=f"a {where} resource is read by the box that points at it, not here")

    scheme, sep, rest = raw.partition("://")
    if sep and scheme.lower() == "file":
        target = Path(rest if rest.startswith("/") else f"/{rest}")
    else:
        target = Path(raw)
    # A file Boxganizer wrote — a note, a checklist — is read on its own terms. The
    # content confine is a rule about what other people's paths may be opened, and
    # routinely names one project directory; holding it over the library's own files
    # would quietly make every note unindexable.
    if where != address.GENERATED:
        confine = paths.confine(root)
        if confine is not None and not paths.inside(target, confine):
            return Extracted(note=f"{target} is outside {confine}")
    try:
        if not target.is_file():
            return Extracted(note="the file is not there any more")
        size = target.stat().st_size
    except OSError as exc:
        return Extracted(note=f"the file could not be opened ({exc.strerror or exc})")

    is_pdf = target.suffix.lower() == ".pdf" if pdf is None else pdf
    if is_pdf:
        return _from_pdf(target)
    if size > MAX_TEXT_BYTES:
        return Extracted(note=f"its {size:,} bytes are more than {MAX_TEXT_BYTES:,}")
    return _from_text_file(target)


def _looks_like_text(raw: bytes) -> bool:
    """Whether these bytes are words rather than something else.

    Decoding is not the test: latin-1 decodes every possible byte, so a compiled file
    would "read" as a page of accents and quietly fill the index with noise. A NUL is
    the traditional tell, and a high proportion of control characters catches the rest
    without needing a table of magic numbers.
    """
    sample = raw[:4096]
    if b"\x00" in sample:
        return False
    allowed = set(range(32, 127)) | {9, 10, 13} | set(range(0x80, 0x100))
    odd = sum(1 for byte in sample if byte not in allowed)
    return odd <= len(sample) // 10


def _from_text_file(target: Path) -> Extracted:
    try:
        raw = target.read_bytes()
    except OSError as exc:
        return Extracted(note=f"the file could not be read ({exc.strerror or exc})")
    if not _looks_like_text(raw):
        # Not text after an honest look: an index of binary noise helps nobody.
        return Extracted(note=f"{target.name} is not text, so there is nothing to read")
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return Extracted(text=raw.decode(encoding).strip()[:MAX_TEXT_BYTES])
        except UnicodeDecodeError:
            continue
    return Extracted(note="it is not text in utf-8, utf-16 or latin-1")


def _from_pdf(target: Path) -> Extracted:
    try:
        import pypdfium2 as pdfium
    except ModuleNotFoundError:  # pragma: no cover - the extra is in the base install
        return Extracted(note="PDF text needs pypdfium2")
    try:
        document = pdfium.PdfDocument(str(target))
    except Exception as exc:  # pypdfium raises whatever the parser raises
        return Extracted(note=f"the PDF would not open ({type(exc).__name__})")
    try:
        pages = min(len(document), MAX_PDF_PAGES)
        chunks: list[str] = []
        for index in range(pages):
            page = document[index]
            try:
                chunks.append(page.get_textpage().get_text_bounded())
            except Exception:  # one bad page must not lose the other hundred and ninety-nine
                continue
            finally:
                page.close()
    finally:
        document.close()
    text = "\n".join(chunk for chunk in chunks if chunk.strip())
    if not text:
        # A scanned PDF has no text layer: say so rather than reporting an empty file.
        return Extracted(note="the PDF holds no text layer (a scan, probably)")
    return Extracted(text=text[:MAX_TEXT_BYTES])


# -- the thesaurus ------------------------------------------------------------

_LOOKUP: dict[str, tuple[str, ...]] | None = None


def user_groups() -> tuple[tuple[str, ...], ...]:
    """Synonym groups from ``thesaurus.json`` next to the database.

    The file is a JSON list of lists, or an object mapping a word to its synonyms —
    both are accepted, because either is obvious to write::

        [["cellar", "basement"], {"soho": ["london", "office"]}]

    A file that does not parse is ignored rather than fatal: the search still works
    with the bundled words.
    """
    location = config.data_dir() / THESAURUS_FILENAME
    if not location.is_file():
        return ()
    try:
        loaded: Any = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    groups: list[tuple[str, ...]] = []
    if isinstance(loaded, dict):
        for word, related in loaded.items():
            if isinstance(related, list):
                rows = [str(word)] + [str(item) for item in related]
                groups.append(tuple(item.strip().lower() for item in rows if str(item).strip()))
    elif isinstance(loaded, list):
        for group in loaded:
            if isinstance(group, list):
                words = [str(item).strip().lower() for item in group if str(item).strip()]
                groups.append(tuple(words))
    return tuple(group for group in groups if len(group) > 1)


def _lookup() -> dict[str, tuple[str, ...]]:
    global _LOOKUP
    if _LOOKUP is None:
        table: dict[str, set[str]] = {}
        for group in (*BUNDLED_GROUPS, *user_groups()):
            words = [word for word in group if word]
            for word in words:
                table.setdefault(word, set()).update(other for other in words if other != word)
        _LOOKUP = {word: tuple(sorted(others)) for word, others in table.items()}
    return _LOOKUP


def reset_thesaurus() -> None:
    """Forget the loaded thesaurus, so a file written after startup is picked up."""
    global _LOOKUP
    _LOOKUP = None


def synonyms(word: str) -> tuple[str, ...]:
    """The bundled and user words that mean this one, never including it."""
    return _lookup().get(word.strip().lower(), ())


# -- planning -----------------------------------------------------------------


def tokenize(query: str) -> tuple[str, ...]:
    """The words of a query, lowercased, in the order they were typed.

    Quoted runs are kept whole and boolean words are left out: both are things the
    person typing meant as instructions rather than as something to find.
    """
    return tuple(item.text for item in tokens(query) if not item.operator)


def tokens(query: str) -> tuple[Token, ...]:
    """Read what was typed as words, phrases and the connections between them.

    ``basement "water damage" NOT pump`` is three instructions, not four words. The
    quoting is the part a person reaches for when a substring search gave them the
    wrong thing, so it is honoured: a quoted run is looked for as an exact phrase.
    """
    items: list[Token] = []
    for match in _TOKEN.finditer(query or ""):
        text = match.group(0)
        if text.startswith('"') and text.endswith('"') and len(text) > 1:
            phrase = text[1:-1].strip().lower()
            if phrase:
                items.append(Token(text=phrase, phrase=True))
            continue
        word = text.lower()
        if word in _OPERATORS:
            items.append(Token(text=word.upper(), operator=word.upper()))
            continue
        items.append(Token(text=word))
    return tuple(items)


def _shares_a_stem(term: str, word: str) -> bool:
    """Whether two words differ only by a ending, which the index already folds together.

    SQLite stems what it stores, so "February" is in the vocabulary as "februari" and
    "cells" as "cell". Those are not misspellings and must not be reported as ones: a
    difference confined to the last character is the stemmer, and the prefix on the
    typed word reaches it anyway. "cellrr" against "cellar" differs in two characters
    and is a typing mistake, so it is kept.
    """
    shared = 0
    for first, second in zip(term, word, strict=False):
        if first != second:
            break
        shared += 1
    return max(len(term), len(word)) - shared <= 1


def _stem_probes(term: str) -> tuple[str, ...]:
    """The typed word, and the same word with an ending taken off.

    The index stores stems — "flashing" is in it as "flash" — so a mistyped inflection
    matches nothing until the ending comes off the typed word as well. Only the usual
    suffixes are removed, and only while enough word is left to mean something: this is
    a comparison aid for one typo, not a stemmer.
    """
    probes = {term}
    for suffix in ("ies", "ing", "ed", "es", "s"):
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            base = term[: -len(suffix)]
            probes.add(f"{base}y" if suffix == "ies" else base)
    return tuple(sorted(probes))


def fuzzy_forms(term: str, vocabulary: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Vocabulary words that look like *term* — the misspelling case.

    Compared only against the words the index has really seen, so the answer is
    useful rather than encyclopaedic. Each reading of the word (whole, and with an
    ending removed) is limited to vocabulary words within two characters of its length
    sharing a first letter, which keeps a big vocabulary cheap; candidates that differ
    only at the end are dropped, because that is the stemmer and the prefix on the
    typed word reaches it already.
    """
    if len(term) < MIN_FUZZY_LENGTH:
        return ()
    found: list[str] = []
    for probe in _stem_probes(term):
        near = [
            word
            for word in vocabulary
            if len(word) >= len(probe) - 2 and len(word) <= len(probe) + 2 and word[:1] == probe[:1]
        ]
        for word in get_close_matches(probe, near, n=MAX_FUZZY_ALTERNATIVES, cutoff=FUZZY_CUTOFF):
            if word != term and not _shares_a_stem(term, word) and word not in found:
                found.append(word)
    return tuple(found)


def _phrase(word: str) -> str:
    """One word as an FTS5 string, quotes doubled so it cannot break the expression."""
    return '"' + word.replace('"', '""') + '"'


def _group(
    term: str,
    vocabulary: Callable[[str], Sequence[str]] | None,
    expanded: dict[str, tuple[str, ...]],
) -> str:
    """The clause for one word: itself with its stems, plus whatever may stand in for it.

    The word carries a trailing ``*`` so the stems the index folded together are all
    reached ("paint" finds "painted"). Synonyms and near-miss spellings are asked for
    whole, exactly as the list has them, because widening a prefix would drag in
    unrelated words that merely start the same way.
    """
    alternatives = [term]
    for syn in synonyms(term):
        if syn not in alternatives:
            alternatives.append(syn)
    if vocabulary:
        seen = list(vocabulary(term))
        # Only guess when the index has never seen this exact word. A library holding
        # "February" must not be told it searched for "februari" as well: the stem is
        # already reached by the prefix on the word itself, and the near-miss lists
        # that produces are noise rather than help.
        if term not in seen:
            for near in fuzzy_forms(term, seen):
                if near not in alternatives:
                    alternatives.append(near)
    clauses = [f"{_phrase(term)}*"] + [_phrase(word) for word in alternatives if word != term]
    widened = tuple(alternatives[1:])
    if widened:
        expanded[term] = widened
    return "(" + " OR ".join(clauses) + ")"


def plan(query: str, vocabulary: Callable[[str], Sequence[str]] | None = None) -> Plan:
    """Turn what was typed into a content query, widened as far as each word needs.

    ``vocabulary`` is asked word by word for the indexed words worth comparing
    against — the store answers that from the index's own vocabulary, so a fuzzy
    match can only land on a word this library actually contains. Leaving it out
    means no fuzzy widening, which is the right degradation for an empty index
    rather than a failure.
    """
    expanded: dict[str, tuple[str, ...]] = {}
    parts: list[str] = []
    phrases: list[str] = []
    words: list[str] = []
    joiner = "AND"  # how the next clause relates to the ones already asked for
    negate = False

    for item in tokens(query):
        if item.operator:
            # An operator with nothing before it joins nothing, so it is dropped; one
            # that arrives after a clause says how the next one is to be read.
            if not parts:
                continue
            if item.operator == "NOT":
                negate = True
            else:
                joiner = item.operator
            continue
        if item.phrase:
            clause = _phrase(item.text)  # exact, unstemmed, unexpanded: as written
            phrases.append(item.text)
        else:
            clause = _group(item.text, vocabulary, expanded)
            words.append(item.text)
        if negate:
            parts.append(f"NOT {clause}")  # in FTS5 this replaces AND, it does not follow it
            negate = False
        elif parts:
            parts.append(f"{joiner} {clause}")
        else:
            parts.append(clause)
        joiner = "AND"

    if not parts:
        return Plan()
    # The name search is the plain words, joined by spaces: the store matches each as a
    # substring, and a quoted phrase is a substring too. Deliberately unwidened — a box
    # called "Cellar Plans" should not be offered for "basement" on its name alone.
    name_terms = tuple(words + phrases)
    return Plan(
        terms=name_terms,
        content_match=" ".join(parts),
        expanded=expanded,
        name_terms=name_terms,
        phrases=tuple(phrases),
    )


def explain(plan: Plan) -> str:
    """A one-line account of what the query was widened into, for the page or terminal."""
    if not plan.terms:
        return "nothing to search for"
    if not plan.expanded:
        return " ".join(plan.terms)
    pieces = [" ".join(plan.terms)]
    # ASCII arrows only: this string goes into an HTTP header, and headers are latin-1.
    also = ", ".join(
        f"{term} -> {'/'.join(words)}" for term, words in sorted(plan.expanded.items())
    )
    pieces.append(f"also: {also}")
    return "; ".join(pieces)


@dataclass(frozen=True)
class Report:
    """What a pass over the index did."""

    resources: int = 0
    indexed: int = 0
    unchanged: int = 0
    skipped: tuple[tuple[str, str], ...] = ()
    #: How many resources hold words in the index once the pass is done. Counted from the
    #: database rather than added up as it goes, because a folder read brings in files that
    #: were not on the list this pass was built from, and a tally that misses those is a
    #: number nobody can act on.
    in_index: int | None = None

    def summary(self) -> str:
        """What was read, what did not need reading, and what could not be read.

        All three are said because all three are answers someone needs: a rebuild that
        reports nothing is either instantaneous or broken, and only one of those is fine.
        """
        read = f"{self.indexed} file{'' if self.indexed == 1 else 's'} read into the index"
        held = f", {self.unchanged} already up to date" if self.unchanged else ""
        tail = f"; {len(self.skipped)} nothing to read" if self.skipped else ""
        # Where the library stands when the pass is over, rather than what this pass
        # touched: a folder brings in files that were not on the list the pass was built
        # from, so "3 read" and "41 in the index" are both true and only one of them tells
        # you whether the search will find your words tomorrow.
        whole = (
            f"; {self.in_index} file{'' if self.in_index == 1 else 's'} in the index"
            if self.in_index is not None
            else ""
        )
        return f"{read}, out of {self.resources}{held}{tail}{whole}"


def _confine_of(store: Any) -> Path | None:
    """The directory this store reads inside, when it was told one.

    Asked of the store rather than of the environment on every call, so that one library
    has one confine: the page passes it when it builds the app, `box index --root` names
    it, and everything that happens afterwards — a box added, a file re-read, a rebuild —
    agrees about what may be opened.
    """
    found = getattr(store, "content_root", None)
    return Path(found) if found else None


def expand_directory(
    store: Any,
    resource_id: int,
    *,
    root: Path | str | None = None,
    walk_always: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Index a folder by indexing the files inside it, and keep that in step with the disk.

    A folder has no words of its own, so `index_it` on a folder row can only mean one
    thing: bring what is inside into the index. The files become resources like any other —
    rows of their own, with their own mtime and their own text — which is what lets the
    ordinary "has this changed since it was read" mechanism work on them unchanged. The
    folder is not their owner, only where they were found: the links are recorded in
    `resource_link`, a file under two indexed folders is one row with two links, and a
    folder that stops being indexed takes its links with it and leaves the files alone.

    Two things a plain reindex does not have to answer, and a folder does:

    **What appeared?** A new file is a file that is walked over and has no row yet, so it is
    made, linked and read. That is only noticed on a walk, and a walk costs a directory
    listing per folder — so the everyday door (`sync_resource`, called when a box is saved)
    walks only when the folder itself changed, while a pass over the library walks
    whatever it reaches. Deleting a file changes the folder's mtime; editing one does not,
    and the file's own row notices that instead.

    **What disappeared?** A linked file that is no longer on disk has its words taken out of
    the index — otherwise a search keeps offering a note somebody deleted, which is the
    failure mode that makes people stop trusting the search box. The row itself goes only if
    nothing else needs it: no box points at it, and no other folder still claims it.

    `on_progress(done, total)` is called around the reading of each file, with the total the walk
    already knows — the list is complete before the first read. The background queue uses it so the
    bar counts **files**, which is what a person is waiting for, rather than queue items, of which a
    folder is exactly one no matter how much work it is (ADR 32).

    Returns a note when something stopped the walk, empty when it finished.
    """
    parent = store.resource(resource_id)
    if parent is None:
        return ""
    if not parent.index_it:
        # opted out: the links go, the files stay, and their text goes only with them
        for child in store.linked_resources(int(resource_id)):
            store.unlink_resource(int(resource_id), int(child.id))
            store.drop_resource_if_alone(int(child.id))
        store.record_index(int(resource_id), body=None, seen=address.stat(parent.reference))
        return ""

    confine = Path(root) if root is not None else _confine_of(store)
    target = Path(address.normalise(parent.reference))
    fresh = parent.stale() is False
    if fresh and not walk_always:
        return ""  # folder unchanged, and the files inside answer for themselves

    files, note = address.walk_files(target, confine=confine)
    known: set[str] = set()
    indexed = 0
    for position, path in enumerate(files):
        if on_progress is not None:
            on_progress(position, len(files))
        key = address.normalise(str(path))
        known.add(key)
        # A file the person explicitly left out stays left out. The folder's `index_it` says
        # what the folder wants for the files inside it, and a no said about one file is a
        # better answer than a walk that quietly overrules it every pass.
        already = store.resource(str(path))
        if already is not None and not already.index_it:
            continue
        record = store.register_resource(str(path), index_it=True)
        if record is None or record.id is None:
            continue
        store.link_resource(int(resource_id), int(record.id))
        if sync_resource(store, int(record.id), root=confine, walk_always=False) == "":
            indexed += 1
        if on_progress is not None:
            # counted whether or not this one yielded text: the reader is waiting on the walk, not
            # on the harvest, and a bar that skips the empty files never reaches its total.
            on_progress(position + 1, len(files))

    # What the folder used to hold and does not hold now. `known` is the whole current list,
    # so a file that merely stopped being indexable (renamed to a `.jpg`) is dropped here too
    # — its words no longer describe anything a search should return.
    for child in store.linked_resources(int(resource_id)):
        if address.normalise(child.reference) in known:
            continue
        store.record_index(int(child.id), body=None, seen=address.Fingerprint())
        store.unlink_resource(int(resource_id), int(child.id))
        store.drop_resource_if_alone(int(child.id))

    # The folder's own row records the walk, so an unchanged folder is not listed again on
    # the next pass. It holds no text and is not counted as if it should: see `index_counts`.
    store.record_index(int(resource_id), body=None, seen=address.stat(parent.reference))
    del indexed  # the count is the caller's business; the report counts resources
    return note


def sync_resource(
    store: Any,
    resource_id: int,
    *,
    root: Path | str | None = None,
    walk_always: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Read one resource into the index if it is worth reading, and say what stopped it.

    This is the whole of the reindexing rule in one place, called from every door:

    * a resource nobody opted in is not read, and is taken out of the index if it was in
      it — the checkbox means what it says;
    * a file whose size and modification time are what they were when it was last read
      is not read again. That is what the observations on the resource row are for:
      opening a box is not a reason to re-read two megabytes;
    * a file that has changed, or has gone away, is read, and what comes back replaces
      what the index held — including nothing at all, which is recorded too, so the
      next pass does not mistake an unreadable file for an unread one.
    """
    resource = store.resource(resource_id)
    if resource is None:
        return ""
    if resource.is_dir:
        # A folder is not read; it is walked, and the files that come out are indexed as
        # the resources they are. See `expand_directory`.
        return expand_directory(
            store,
            int(resource_id),
            root=root if root is not None else _confine_of(store),
            walk_always=walk_always,
            on_progress=on_progress,
        )
    if not resource.index_it:
        if resource.indexed:
            store.record_index(resource.id, body=None, seen=address.Fingerprint())
        return ""
    if resource.indexed and resource.stale() is False:
        return ""  # same file, same index: nothing to do, and nothing read to find out
    found = extract_resource(
        resource.reference,
        root=root if root is not None else _confine_of(store),
        origin=resource.origin,
    )
    seen = address.stat(resource.reference)
    store.record_index(
        resource.id,
        body=found.text if found.usable else None,
        seen=address.Fingerprint(
            mtime=seen.mtime,
            bytes=seen.bytes,
            digest=address.digest(found.text) if found.usable else None,
        ),
    )
    return "" if found.usable else found.note


def sync(store: Any, box: Box, *, root: Path | str | None = None) -> str:
    """Bring the text behind one box up to date. The box is a handle on its resource."""
    if box.resource_id is None:
        return ""
    return sync_resource(store, int(box.resource_id), root=root)


def rebuild(store: Any, *, root: Path | str | None = None, only_stale: bool = False) -> Report:
    """Read every resource the index is wanted for. What `box index` does.

    Needed because the index lives in the database and the files do not: a library
    copied to another machine, a database written before the index existed, and a file
    edited outside Boxganizer are all cases where the rows have to be made true again.

    `only_stale` is the same pass narrowed to the files something has been noticed
    about, which is what a scheduled reindex wants rather than a whole library read on a
    timer.
    """
    if only_stale:
        watched = list(store.check_observations())
        # Folders are added whatever their own mtime says. A file created two levels down
        # does not touch the mtime of the folder above, so a narrowed pass that trusted
        # mtimes would never list the folder and so would never notice the file — and
        # "Index what changed" would be a button that misses the changes people mean.
        # Listing a folder is not reading it: this is the cheap half of the work.
        seen = {int(item.id or 0) for item in watched}
        watched.extend(
            item
            for item in store.indexed_resources()
            if item.is_dir and int(item.id or 0) not in seen
        )
    else:
        watched = store.indexed_resources()
    skipped: list[tuple[str, str]] = []
    indexed = 0
    unchanged = 0
    for resource in watched:
        before = resource.indexed and resource.stale() is False
        note = sync_resource(
            # A pass over the library is the deliberate one, so a folder is walked rather
            # than trusted to its mtime: a file added two levels down does not change the
            # mtime of the folder above, and discovery is what this pass is for.
            store,
            resource.id,
            root=root,
            walk_always=True,
        )
        if note:
            skipped.append((address.display(resource.reference), note))
        elif before:
            unchanged += 1
        else:
            indexed += 1
    return Report(
        resources=len(watched),
        indexed=indexed,
        unchanged=unchanged,
        skipped=tuple(skipped),
        in_index=store.index_counts()["indexed"],
    )


def confine_from_env() -> Path:
    """The root the indexer reads inside, from the same variable the page uses."""
    return paths.content_root(os.environ.get(paths.CONTENT_ROOT_ENV, ""))
