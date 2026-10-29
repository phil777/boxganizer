"""Markdown: what a `.md` file is turned into on the page.

Two rules shape this module.

**The parser is markdown-it, with the extra constructs switched on one at a time.**
The CommonMark preset gives headings, emphasis, lists, block quotes, fenced and
indented code, links and images; `table` and `strikethrough` are the two GitHub-isms
people actually use, and they are enabled explicitly rather than by reaching for a
"GitHub flavour" that would also turn on things nobody asked for.

**Raw HTML is not a markdown feature worth the risk.** `html=False` means a literal
`<script>` in a note stays text on the screen, which is what a person reading a file
about scripts expects. That leaves one hole the parser does not close for you: a link
whose address begins `javascript:`. A box's file is not trusted input — it may have come
off a disk, out of a mail, or from someone else's repository — so an address that would
run something when clicked is taken out and left as the words the author wrote.
"""

from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

#: Extra constructs beyond CommonMark. Both are things people write without thinking
#: twice: a table of numbers, and ~~this~~.
EXTRA_RULES = ("table", "strikethrough")

#: Address schemes that run something when a person clicks them. `data:` is in the list
#: because a `data:text/html` link is a page written inside another page.
_UNSAFE_SCHEME = re.compile(r"^\s*(javascript|vbscript|livescript|data)\s*:", re.IGNORECASE)

#: The markdown that goes out to the page is turned over once before it is parsed, and
#: that turn is not optional enough to leave to the caller.
_parser: MarkdownIt | None = None


def _neutralise_urls(state: Any) -> None:
    """A core rule: no clickable address in this document may run something.

    Pushed onto the parser's core chain rather than applied by whoever calls `render`, so
    there is no way to render markdown safely-by-accident and unsafely-on-purpose: every
    path through this module goes through the same parser, and that parser checks every
    `href` and `src` it is about to emit.
    """
    _walk(state.tokens)


def parser() -> MarkdownIt:
    """The one configured parser.

    Built once and kept: assembling a markdown-it instance costs milliseconds, and every
    box that renders markdown would pay it.
    """
    global _parser
    if _parser is None:
        instance = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
        instance.enable(EXTRA_RULES)
        instance.core.ruler.push("boxganizer_safe_urls", _neutralise_urls)
        _parser = instance
    return _parser


def _safe_url(value: str) -> bool:
    """Whether this address may be clicked.

    Anything that is not an obvious scheme — `http://`, `mailto:`, a relative path, an
    anchor — is judged by what it does *not* start with, because the list of safe shapes
    is open-ended and the list of executable ones is short.
    """
    return not _UNSAFE_SCHEME.match(value or "")


def _walk(tokens: list[Token]) -> None:
    """Take the sting out of a link or image address, in place.

    The link is left there — the words the author wrote, and their emphasis, still render
    — with only the clickable part removed. Deleting the whole line would quietly
    rewrite someone's note, and a reader should see that a link went nowhere rather than
    wonder where the sentence went.
    """
    for token in tokens:
        attrs = token.attrs
        if not attrs:
            continue
        cleaned: dict[str, str] = {}
        for name, value in attrs.items():
            if name in ("href", "src") and not _safe_url(str(value)):
                cleaned[name] = "#"
                cleaned["data-blocked"] = str(value)
            else:
                cleaned[name] = str(value)
        token.attrs = cleaned  # type: ignore[assignment]
        if token.children:
            _walk(token.children)


def render(text: str) -> str:
    """HTML for some markdown, safe enough to put on the page.

    Never raises: a parser that throws on a weird file would leave a box with nothing to
    show. What comes back on a failure is the text escaped inside a `<pre>`, which is
    what the raw view would have shown anyway.
    """
    source = text or ""
    try:
        return str(parser().render(source, {}))
    except Exception:  # markdown-it raises whatever the grammar hits; the box must show
        from html import escape

        return f'<pre class="markdown-fallback">{escape(source)}</pre>'


def summary(text: str, *, length: int = 160) -> str:
    """The first words of a note, with the markup stripped.

    Used where a box is represented by one line — a search result, a child in a list —
    and a heading marker or a pipe table makes noise instead of a sentence.
    """
    tokens = parser().parse(text or "", {})
    words: list[str] = []
    for token in _inline_tokens(tokens):
        if token.type == "code_inline":
            words.append(token.content.strip())
            continue
        content = token.content.strip()
        if content:
            words.append(content)
    flat = re.sub(r"\s+", " ", " ".join(words)).strip()
    return flat[:length] + "…" if len(flat) > length else flat


def _inline_tokens(tokens: list[Token]) -> Any:
    for token in tokens:
        if token.type == "inline" and token.children:
            keep = ("softbreak", "hardbreak")
            yield from (child for child in token.children if child.type not in keep)
        elif token.children:
            yield from _inline_tokens(token.children)
