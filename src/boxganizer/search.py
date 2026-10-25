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
    """Whether this box's file should be read into the index."""
    if not boxtypes.known(box.box_type):
        return False
    kind = boxtypes.get(box.box_type)
    return kind.indexes_content and bool(box.resource_uri)


def extract(box: Box, *, root: Path | str | None = None) -> Extracted:
    """Read a box's file as text, inside the usual confine.

    Never raises: a box whose file is missing, huge, unreadable or outside the
    confine is still a perfectly good box, and the index has to carry on with the
    other nine hundred. What happened goes into :attr:`Extracted.note`.
    """
    if not box.resource_uri:
        return Extracted(note="the box points at no file")
    if not indexes(box):
        return Extracted(note=f"a {box.box_type} box is not read for text")

    confine = paths.content_root(root)
    scheme, _ = paths.split_resource(box.resource_uri)
    if scheme not in ("", "file"):
        return Extracted(note=f"a {scheme}:// resource is not a local file")

    target = paths.local_path(box.resource_uri)
    if not paths.inside(target, confine):
        return Extracted(note=f"{target} is outside {confine}")
    try:
        if not target.is_file():
            return Extracted(note="the file is not there any more")
        size = target.stat().st_size
    except OSError as exc:
        return Extracted(note=f"the file could not be opened ({exc.strerror or exc})")

    if box.box_type == "pdf" or boxtypes.get(box.box_type).key == "pdf":
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
            return Extracted(text=raw.decode(encoding))
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
                textpage = page.get_textpage()
                chunks.append(textpage.get_text_bounded())
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
    return Extracted(text=text)


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
            if len(word) >= len(probe) - 2
            and len(word) <= len(probe) + 2
            and word[:1] == probe[:1]
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
    """What a rebuild of the index did."""

    boxes: int = 0
    indexed: int = 0
    skipped: tuple[tuple[str, str], ...] = ()

    def summary(self) -> str:
        read = f"{self.indexed} box file{'' if self.indexed == 1 else 's'} indexed"
        if not self.skipped:
            return f"{read}, out of {self.boxes}"
        return f"{read}, out of {self.boxes}; {len(self.skipped)} nothing to read"


def sync(store: Any, box: Box, *, root: Path | str | None = None) -> str:
    """Bring one box's indexed text up to date, and say what could not be read.

    Called after a box is created or changed, from whichever interface did it. A box
    that no longer points at readable text is dropped from the index rather than left
    in it: a search that keeps finding a file that is gone is worse than one that
    never found it.
    """
    if not indexes(box):
        store.drop_text(box.uuid)
        return ""
    found = extract(box, root=root)
    store.index_text(box.uuid, found.text)
    return "" if found.usable else found.note


def rebuild(store: Any, *, root: Path | str | None = None) -> Report:
    """Re-read every box that has text. What `box index` does.

    Needed because the index lives in the database and the files do not: a library
    copied to another machine, a database written before this existed, or a file
    edited outside Boxganizer are all cases where the rows have to be made true
    again in one go.
    """
    skipped: list[tuple[str, str]] = []
    boxes = store.list_boxes()
    indexed = 0
    for box in boxes:
        note = sync(store, box, root=root)
        if note:
            skipped.append((box.name, note))
        elif indexes(box):
            indexed += 1
    return Report(boxes=len(boxes), indexed=indexed, skipped=tuple(skipped))


def confine_from_env() -> Path:
    """The root the indexer reads inside, from the same variable the page uses."""
    return paths.content_root(os.environ.get(paths.CONTENT_ROOT_ENV, ""))
