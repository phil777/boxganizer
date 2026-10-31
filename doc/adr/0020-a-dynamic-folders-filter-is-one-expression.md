# 20. A dynamic folder's filter is one expression, and the builder writes that expression

Date: 2026-11-09
Status: accepted

## Context

A dynamic folder held a mapping of clauses — `name`, `type`, `resource`, `metadata` — every
clause an AND, every match a substring test. It could say "the notes with boiler in the name" and
nothing else. The request was to filter by type / name / tag / content / resource, each as plain
text **or** a regular expression, combined with **and, or, not**; to have "a clean unambiguous
syntax to formulate a complete filter in one search expression"; and to have "a way to create this
expression with UI elements".

Two decisions dominated everything else.

**Widgets only, no text form.** Three dropdowns and a tick box are quick to reach for and cannot
say `not (tag:urgent or type:pdf)`, cannot be typed by someone who knows what they mean, cannot be
pasted, diffed, or put in a shell script — and they store a shape only that one widget can read.
It also makes the UI the only place the language exists, so `box edit` and the API would keep
speaking a different dialect from the page.

**A query language with its own AST and a second evaluation path for the old clauses.** Then the
old folders would be a special case forever, in every code path, including the one that decides
whether a box belongs to a folder — two membership rules is how a folder ends up showing different
contents depending on who asked.

## Decision

### 1. The expression is the filter; the widgets are an editor for it

The stored shape is `{"expression": "type:note and (tag:urgent or name:\"boiler fix\")"}`. The
filter bar round-trips: it parses what is in the field, draws controls from the tree, and writes
the tree back. Editing either side moves the other, because there is one value underneath.

### 2. The grammar, and what each default is for

```
expr    := or
or      := and { ('or' | '|') and }
and     := unary { ('and' | '&')? unary }      # juxtaposition means AND
unary   := ('not' | '!') unary | '(' expr ')' | clause
clause  := [field ':'] value
value   := '/' regex '/' | 're:' text | '"' text '"' | bare word
field   := type | name | tag | content | resource
```

* **A bare word is a name.** `boiler` means `name:boiler`. One word typed into a box that filters
  a library is a search for a thing called that; making it an error, or a magic field, teaches
  people to type `name:` out of superstition.
* **Juxtaposition ANDs.** `type:note urgent` is two conditions with the word left out. Nobody
  writes `and` between two things they mean together, and reading the pair as one search term
  would silently *widen* a filter that was being narrowed.
* **A regex keeps the operators inside it.** `resource:/meter|photo/` is one value: the lexer reads
  a slash-delimited value to its closing slash, honouring `\/`, before it looks for `|`, `&` or a
  bracket. Without that rule the same text is three tokens — `/meter`, `or`, `photo/` — so the
  filter a person wrote for two words would silently become a filter for one word and a syntax
  error, which is the worst kind of mistake to make in a language that is supposed to be typed.
* **A regex is written with slashes**, `/\.pdf$/`, or with an explicit `re:` prefix. A value that
  starts with a slash and never closes is an error naming both spellings, not a literal search for
  a slash: nobody puts a leading slash in a search string meaning the slash, and the case where
  they do has the quote.
* **`type:` reads the type hierarchy**, so `type:resource` takes a `pdf` box — the one rule the old
  clauses already had. An expression that disagreed with the type list about what counts as what
  would make a folder's membership depend on which way the question was written.

### 3. A filter that cannot be read admits nothing

The old clauses had this rule and the grammar inherits it with more reason: a half-parsed
expression would easily degenerate into *no conditions*, and no conditions means **the whole
library in the folder**. A typo would look like the library had changed rather than like the filter
had. So `broken` carries the reason, `matches` returns False for every box, `describe()` prints the
complaint, and the page shows it in the folder rather than showing an empty list or an all-ones.

### 4. The bar edits a list, and stops when the expression is not a list

The filter bar draws one row per clause — negate, field, plain-or-regex, value — plus move up,
move down, remove, and one switch between `and` and `or`. It asks the **server** to parse
(`POST /api/filters/parse`) and draws what comes back, because a JavaScript copy of this grammar
would be a second grammar: the first symptom would be pretty controls disagreeing with the stored
filter about what a folder holds.

A grouped expression — `(a or b) and c`, or `not (a or b)` — has **no row order that survives a
reorder**, so the parse returns `clauses: null` and the bar says the expression is edited in the
field. Drawing it as rows anyway would let one click on a ↓ button change what the folder selects.
The same answer is given for an unreadable expression: no rows, one line naming the reason.

### 5. Tags and words are looked up, never reasoned about here

`filters.py` knows boxes and nothing else — no database, no index, no tag tree. Two fields need
answers from outside:

* **`tag:`** means the tag *or anything below it*, which is a question about the tree;
* **`content:`** is a full-text query, whose planning (thesaurus, fuzzy forms, the FTS match
  string) already lives in `search.py`.

Both are gathered up front by `Filter.needs` and resolved once by the caller into a `Lookup` of
uuid sets — `BoxStore` is that caller; it is the only thing that can answer either question. That is what keeps membership one pass over the library instead of one query per box,
and keeps one definition of what a tag means and one of what a search means.

**A clause with no lookup answered admits nothing.** `tag:urgent` evaluated without a `Lookup` is
not "ignore this clause" — ignoring it would add boxes — and not "match everything" — it selects
nothing, exactly as a broken clause does, because an unanswered question has no evidence in the
box's favour.

### 6. Old stored filters are untouched, and a bare string is still an error

A mapping without an `expression` key parses to the same flat clauses, with `root` left `None`, and
`as_config()` writes it back in the same shape it came in. No migration rewrites anyone's folders,
so no folder changes contents because a version was installed.

`"filter": "pdf"` used to be a refusal with a message, and stays one. Accepting a bare string would
give a value this version **rejected** a meaning it never had — a typo silently becoming
"name contains pdf" in a library somebody already filed. The new form has its own key, which is
what `as_config()` writes, so there is exactly one way to store an expression.

### 7. Words about a filter keep the brackets

`describe()` is for a human deciding whether this folder is right, and `not a or b` reads as
`(not a) or b` to a hurried eye, while `not type contains 'x'` reads as a comparison against a
field called *not type*. So negations and nested `or`s keep their parentheses in the prose, at the
cost of a few more brackets in a sentence. The test asserts the exact wording, because the whole
point is the reading.

## Consequences

**Costs.** A real parser (a tokeniser, four recursive-descent functions, ~200 lines) where a widget
would have been a day less; a `Lookup` the store must build, and a discipline about building it
once per membership pass rather than per box; two accepted spellings for the same idea (`/re/` and
`re:`) which is a deliberate courtesy to two habits of typing.

**What it buys.** The full request: five fields, plain or regex, `and`/`or`/`not`, grouping. A
filter that can be read off a terminal, written in a shell, pasted into a bug report, and edited
with a mouse — by the same person, on the same folder, without either route being a lie about the
other.

**Verification.** 32 tests in `tests/test_filter_grammar.py`: every field, both regex spellings,
the quote that keeps a slash literal, juxtaposition, precedence with and without brackets, negation
of a group, every broken form naming its reason and admitting nothing, unanswered lookups admitting
nothing, exact round-trip of the written expression, and that a stored mapping still parses to the
identical legacy shape it had before this existed.
