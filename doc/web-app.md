# The web app

`box serve` puts the library in a browser: a left pane to search and pick, a main pane that shows
one box with **its content first**, and one modal to make a box. The library is the same SQLite
store the CLI and the text UI read and write, so a box made here is immediately `box show`-able
there.

This file is the reference: what each kind of box draws, and what its controls do. The
[README](../README.md) gives the tour; the decisions behind the shapes are in
[the ADRs](adr/).

## What each box shows

What a box shows depends on what it is. A view is registered per type key in
`web/static/app.js` (`registerView`), and a type with no view of its own falls back to the detail
panel — the last row of the table.

| Box                | Content view                                              |
| ------------------ | --------------------------------------------------------- |
| image              | the picture, scaled into the space left, ratio untouched   |
| text, markdown     | the file's text; wrap, crop and font size in the right-click menu; vertical scroll always, horizontal when wrap is off. A **markdown** file is rendered instead — headings, emphasis, lists that nest, tables that line up, quotes, fenced code, links — with the plain text one click away and that choice kept in the box. Raw HTML in the file stays text on the screen, and a link whose address would run something keeps its words and loses its reach |
| folder, directory, cabinet | its children, as a list or as icons; double-click opens one in place; **the search field on the tag line narrows what is shown** — by the library's own search, so the child's words and the words inside its files count, not just its name — and it starts empty every time, while the searches you have tried are remembered in the box. A **directory** box shows the top of its folder whenever it is opened, however deep the last browse went; a file in it can be dragged out — onto another box to file a new box inside it, or onto the library list to leave it unfiled — with the type inferred from the file |
| grid               | rows and columns you set (type them or step them), one child per cell, drag to move, drop on an occupied cell to swap, right-click to create a box in a cell |
| desktop            | icons anywhere on the surface: drag them with the pointer, drag a corner to size them, arrow-key them about, and let a magnetic grid of the size you chose pull them onto its lines. The resource, when it is a picture, is the wallpaper — right-click the bare surface to choose one or take it away. Double-click opens a box in place of the desktop. Shown in a smaller pane it is the whole desktop drawn smaller, not a corner of it: canvas, wallpaper and icons scale by one factor, so an icon stays on the same feature of the picture |
| tiling-viewport    | its space split into panes, each showing one box with its own path line and four buttons — settings, close, split side by side, split above and below — and a bar between any two panes you can drag; the split, the ratio and what is open where are saved on the box. Each pane folds **its own** settings row: one gear per box on screen, and the gear in the top line reaches no further than the main screen |
| windowed-viewport  | floating windows over the same space, each with its own path line, settings gear and window buttons — **minimize** (the title line and nothing else), **maximize** (the whole board) and **restore** (back to the rectangle it had). Drag the title to move a window, the corner to resize it, click anywhere in it to raise it above the rest; the settings row holds the **transparency** slider — **0 % invisible, 100 % solid**, and it starts at 0 — so a windowed box shows you the window you last touched and lets the rest of the pile recede; the focused window stays solid whatever the slider says, so the desk can show through without the box you are reading going with it. Everything comes back where you left it |
| dynamic-folder     | the folder list; above it the filter as one expression, a row per clause to add, remove, reorder or negate, what the filter asks for in words, and a button to re-apply it. It shows its members and refuses to receive one: a drop says "not here" in amber and explains itself (ADR 25) |
| mindmap            | the box in a rounded node at the centre, its children as branches growing left and right, each branch curved to its parent in its own colour. Click into it and the keys drive it: **Insert** a node under the one in hand, **Enter** beside it, **arrows** to walk the map, **Ctrl+↑↓** to shuffle a node among its siblings, **Ctrl+←→** to push it deeper into the map or pull it out towards the centre — read off the side the branch grows on, so Ctrl always means away from the middle or towards it, and a branch of the centre changes side instead of rising above it. Double-click, or **F2**, writes the node's name where it stands. A box dragged onto a node that nothing has been written into takes that node's place; onto any other node it is filed inside |
| browser, web       | the page, framed; the address bar saves back to the box |
| mqtt               | the last message on its topic, and every one after it, as the broker sends them — worded and dressed by the box's own templates, with the raw payload in the tooltip. The address is shown with its password hidden, which the box keeps for itself. In a grid cell it is the last value alone: the cell does not listen, the opened box does |
| vnc                | another machine's screen. The panel first asks what is there — the server's name, the protocol it speaks, how big its screen is — and draws nothing until you press **Show the screen**. From then the machine sends a picture when its screen changes and nothing when it does not; your pointer and keystrokes go back to it, mapped through whatever size the pane drew the screen at. **A server that wants a password is asked for one**, in a field that appears when the screen asks and never otherwise; the answer is used for that connection and never written to the library — which is a file that gets copied — though you may point the box at a file that holds it (`password_file`, outside the library, readable only by you). ADRs 15 and 17 |
| ssh                | a console on another machine: the address it goes to, the line to type for a session of your own, a field for one command at a time, and the transcript of what was asked and what answered, exit code and all. Up and down arrows walk what was run here, kept in the box. **There is no password field, and none can be stored** — the type refuses an address carrying one; keys, an agent and `~/.ssh/config` decide who you are. It is a console and not a terminal emulator, and says so on the panel: one command, one answer, no full-screen programs |
| note               | an `edit` switch (a real switch: `role=switch`, `aria-checked`, knob) and a `read-only` / `editing` cue on the sheet; the toolbar is always visible because it is how you write, not a setting | a post-it with a place in the graph: **Markdown** or **Plain text**, and — when markdown — one **`edit`** switch — a real switch, `role="switch"` with `aria-checked`, the word beside it never moving — and the note says its condition on itself with a small `read-only` or `editing` cue, because the toolbar is one thing and the sheet you are about to type into is another. Both settings are kept on the box. The toolbox writes markdown markers into the text and nothing else: `**` around a selection, a `#` or a `-` at the start of every line the selection touches. A box can be named inside a sentence, by picking it from the toolbox or dragging it in, and is written as `[📋 name](boxganizer:uuid)` — an ordinary markdown link on a scheme that reaches nothing until the page decides that clicking it opens the box. Naming a box here files it here too, so a note cannot quietly lose what it points at; deleting the sentence does not throw the box out |
| checklist          | a list to tick. Type under the list and press **Enter** to add a line; click a square to tick it and the count at the top moves. A child box dragged onto a line hangs there as an icon — at the right end of the line, centred on it — and clicking the icon opens that box; lifting it off takes the link and leaves the box filed here. Anything filed in the box but on no line is listed underneath rather than hidden. What you type is written to a file of Boxganizer's own beside the database, in plain `- [ ]` markdown, and reindexed each time it changes, so the items are found by full-text search like the words in any other file |
| anything else      | its icon, and a detail panel with description, resource, metadata and config |

## How the page is put together

**Drop a file anywhere in the whole left pane.** There is no strip to aim at any more — the labelled
rectangle at the bottom of the list is gone, and the pane took its place. A file released over the
pane — from the desktop, a file manager, or out of a directory box — becomes a box: the type is
guessed from the file, it is named after the object, and a copy is captured into the library (ADR 35),
because a file dropped into a *library* is being filed, not merely shown. Nothing has to be aimed at:
a target you have to find is one you stop using, and a bigger target is only honest if it shows that
it is a target. So the pane draws an outline while the drag is over it, and every zone that can take
the same class paints its own — a region that swallows a file while the page says nothing is the
failure ADR 45 is written against.

It lights up for what it can finish, and that is not the same as what a drag looks like halfway
through. A file dragged from another window exposes `Files` in its type list while **the file itself
is still withheld until you let go**, so the highlight is decided from the type list, not from the
file list — which is empty at exactly the moment you are deciding whether to drop (ADR 38). Drag a
**box** over the pane and it stays plain, because that is the list's business. A drop that lands on a
row, a folder listing or a viewport is answered by that zone and the pane stands down — one gesture,
one parent.

**Three payloads, three outcomes.** A **file** becomes a captured box, as above; drop it on a folder,
a grid cell, the desktop, a viewport pane, a mindmap (the bare picture files it as a branch, a node
hangs it on that node) or the library list and the same happens *plus* the filing or placement you
aimed at — the box is created first and then placed by the uuid the create returned, which is the only
order in which placing it can mean anything. **A name that is already taken is settled by content,
not by an error**: if the box carrying that name already holds these exact bytes, nothing new is
made — the existing box is filed where you dropped it, and the line says it was already in the
library. Different bytes under the same name are two things that happen to share a filename, so the
newcomer takes the next number (`notes`, `notes 2`). A name you *typed* in the dialog is still
refused when it clashes, because you meant that name and only you can choose what to do about it. A file manager that hands over a **path** instead of bytes
(`file:///…`) is the same case: the path is read as a path, typed by its real suffix — `notes.md` is a
markdown box, not "a file" — named without the extension, and captured like any other file. A **link**
(a URL dragged out of a browser, with or without its title) becomes a link box pointing at that
address: it is named from the link text you selected, or from the last part of the address, and
**nothing is fetched** — not by the page, not by the server, which only ever stores the string you
dropped. Boxganizer indexes things; it
does not download what it is shown, and a dropped URL is the one thing a page could use to make a
server visit machines only you can reach. A dropped link that is also carrying bytes takes the bytes:
an image dragged off a page is you filing the picture, not a pointer to somebody else's copy. Drop
the same link twice and you get two boxes, the second named `… 2`, because names are unique here and
silently reusing the first would mean the drop did nothing.

**A target only lights for what it can finish, and the ones that refuse look like refusing.** The
outline a zone draws while you hover is a promise made before you let go, so it is decided by the
payload: bytes, a path, a link, or a box dragged from this page. The note editor and a checklist line
take a box and turn away a file, and they show the same amber dashes a dynamic folder uses rather than
a welcome they are about to withdraw — an absent outline reads as "I missed", and a drawn refusal reads
as a decision. A **box** reference is a uuid, because a uuid is the only thing this page ever writes
when it drags one: selected text that happens to spell a box's name is not filed anywhere, and makes no
request at all.

**Something that fits neither gets a sentence, not a shrug.** Drop a box where boxes are not accepted
and the refusal says why; drop a file on the note editor or on a checklist line — which would otherwise
write a bare URL into your words or your attachments — and it tells you to use the strip and changes
nothing at all. A reference the server cannot resolve answers in the same spirit: it names the value it
turned down, says what it looked like (an address, a file name, an id that is no longer here, a name
that never was), and says nothing changed; a name clash says which name and that names are kept unique.
A create that cannot be finished leaves no half-finished box behind (ADR 38).

**The mouse back button goes up one level, and never leaves the app.** Back on the mouse moves the *main
pane* up the hierarchy: along the crumb path you walked if there is one, otherwise to the box's
parent, otherwise to the root — and at the root it does nothing, because there is nothing above it.
It never navigates the tab away, which matters more than it sounds: the alternative takes the panes,
the walk remembered for each of them and anything half-typed with it. Viewport panes keep their own
walks untouched. Keyboard Back and the browser's own Back do the same thing, because by the time the
page hears about them they are the same navigation.

**A stray drop cannot take the page away.** Release a file anywhere the app did not claim — a heading,
a crumb, the gap between panes — and the browser's own behaviour (open the file, navigate away, lose
the tab's panes and any unsaved editing) is cancelled quietly, with no message, because you were not
aiming at the page and a red error for a miss you did not mean to make is noise. A drop that lands on
a zone that *can* finish it is answered by that zone; this guard only ever sees the misses.

Only a *drop* keeps a copy. The right-click "New box from this file" gesture makes the same box from the
same inference and leaves the file where it is, because that gesture says *point at this*, and quietly
duplicating it would surprise you. A `dragover` writes nothing: a highlight is not a change.

A left pane to search and pick — **Root** above it, the search field below, which
finds boxes by their own fields and by the words inside their text and PDF files,
showing the passage that answered and what your words were widened to — and a main
pane to show one box, **its content first**. **New box** opens a modal where you
choose the type (the list is searchable, because there are 39 of them) and configure
it. With a resource named, **Index content** decides whether the file is read into the
full-text search, and **Keep a copy in the library** captures it — a copy beside the database,
named by its sha256 and stored once however many boxes capture the same bytes (ADR 35). Both are
off unless you say otherwise, and the capture box starts unticked every time the dialog opens, so
an unrelated edit can't copy a file by accident. ☰ beside the name at the top of that panel is what belongs to the library rather than
to one box, in this order: **Manage tags…**, **Check the library**, a line, **Index what changed**,
**Re-read the whole library**, a line, and **About** last — About is the entry that leaves the work to
describe the tool, so it sits at the end rather than at the place every click passes through.

**The list beside it reads either way, and remembers which.** Two icon buttons at the top of the pane
switch between the flat list and a tree: in the tree, each box that was filed in exactly one folder is
folded under the folder it was filed in, with a `[+]` before the name, one level at a time however
deep it goes. A box filed in two folders stays at the top — it has no single place to hide, and picking
one would be the page inventing a fact about the library — and the folders `box init` makes to hold
everything (the root, and the cabinet) are not counted as places you filed something, or the top level
would be the thing that disappeared (ADR 45). Three buttons set the order: by name, by type then name,
by how much each box holds; an arrow at the far right turns it around, ascending or descending, and the
order applies *inside* each opened level, because that is the question a folded level asks. The root
stays first whatever the order says. With a search term in the field the hits are shown flat: a tree
that folded away the boxes the search just found is a search nobody trusts. The view, the order, the
direction and which folders were open are kept for the next visit, on this browser and not in the
library — a database that travels should not carry one person's window shape with it.
Along the bottom,
in front of the box count, the index reports itself all the time — `75 indexed`, or `2 to
reindex`, or `reading 7 of 12…` — with the detail on hover, including which files hold nothing to
read. **Saving a box does not wait for its file to be read**: the rows are written, the answer comes
back, and the reading happens behind that line with a bar that counts **files**, so you can see how
much of the folder you just pointed at has been gone through. The bar's numbers are the sentence's
numbers — `7 / 12` beside `reading 7 of 12…` — and a total that has not been worked out yet is not
presented as one: while the count is unknown the line reads `reading…` and the bar says `0 read`, so a
pass that turned out to have nothing to do cannot look like a pass that is stuck. Pressing the line opens a dialog titled **Index status**: four
counts (files followed for the index, up to date, need reindexing, not indexed yet), and when there
are any, a line saying how many watched resources are **gone from this machine**, with the folders
they were in named. Gone is not work. Those files are not on this computer, so `Reindex` cannot reach
them; counting them as work is how a library ends up offering 833 things to do forever, and removing
them from the index to quiet the number would be losing the record that you once had them. They stay
in the count of what is followed, the dialog says what is missing, and `update index status` is the
button that looks at the files — a check is an action, so it is no longer something a page load does
(ADRs 41, 42 and 44). Put the folder back and ask again: nothing had to be un-set.
 Taking a box *out* of the index is
immediate — a stale document staying findable is a wrong answer, not a slow one. A file the index
refuses is named there rather than left as a stalled bar (ADR 32). A **box** dropped where no view
claimed it files into the box you are looking at, so the whole pane takes it; a **file** dropped where
nothing claimed it is cancelled instead of opening in the browser, because what that navigation costs
you is this tab (ADR 38). The outline that says *drop here* is the page's to take away, not the
element's that drew it: a view that claims a drop stops the event, so the pane behind it is never
told, and one drag cancelled with Escape never fires a drop at all. Every ending clears every zone
(ADR 27), which is why a red outline no longer follows you into the next three boxes.

**Deleting a box is on its right-click menu** and asks twice: the item rewrites itself into
*Really delete — what was filed inside stays in the library*, and only the second click runs
(ADR 28). What survives is stated rather than guessed at. The links from the box go with it; every
box that was filed inside stays in the library, still filed wherever else it was; a file the box
pointed at is untouched; and the words a note or checklist wrote for itself stay in their file
beside the database, named in the sentence you get back (ADR 14). The **root** is never offered for
deletion, and the server refuses it in the same words if asked by another route: deleting it would
leave nowhere to file anything. `box rm` asks the same question and answers with the same facts.

If the box had **captured** a copy (ADR 35), what happens to that copy is decided by the server and
reported back, because only it knows whether this was the last box pointing at it. The last one moves the
copy to a `trash/` folder beside `files/`, at the same bucket path, where it can still be found and moved
back; if another box still points at the same bytes, the copy stays exactly where it is and the message
says so. A copy whose contents no longer match the hash in its name — edited by hand — is left alone and
reported rather than moved, because it may be the only instance of something. Nothing is destroyed until
`box trash empty` is confirmed, and `box check` counts what is waiting (ADR 36). A box that merely pointed
at a document still leaves that document exactly where it was.

**Every box has a search of its own**, on the tag line: the field sits on the left and the tags stay
right of centre. It asks the library's existing full-text question restricted to that box and whatever
is filed below it — same ranking, same passage, same widening, one extra set clause — so nothing is
re-read to answer it, and a cycle in the graph terminates instead of looping (ADR 33). An
**indexed-directory** asks that question one step further: its field searches the files in the folder
and below them, by name and by what they say, and the listing becomes the answer (ADR 46).

**If the box has a list to narrow, that list is the answer, and nothing unrolls over it.** In a folder,
a directory or a cabinet, typing closes the displayed rows down to what matches —
list, icons or file listing — and says `2 of 4 inside` while it does. A second copy of those same
matches floating on top would hide the rows you are about to click, in the only place that shows them
in order and in context, so the popup is withdrawn wherever a surface is listening (ADR 39). The page
decides by asking whether anything underneath is filtering, not by consulting a list of types. In an
indexed-directory the list is doing more than narrowing (ADR 46), and it withdraws the popup for the
hits it drew itself; a hit that is some *other* box, with no file row to sit in, still gets the popup.

**A match deeper than a row is shown by the row that holds it, and the row says so.** A box three levels
down keeps the drawers above it on screen — a match you cannot reach is not a filter but a dead end — and
the ancestor now carries a line naming what matched below it: *1 match inside: Boiler Notes*. In a
directory you get the same for a subtree (`1 match inside`) and, for a file whose contents matched while
its name says nothing about the word, *the words are in this file*. Without those lines a surviving row
looks like a filter that is broken, because the term appears nowhere on it.

**A box with no list keeps the window.** A note, an image, a sensor: there is nothing on screen to
narrow, so results unroll under the field, each row saying *via* which boxes you would reach it, and a
row that matched inside a file naming the file and quoting the words. Escape closes it, clicking away
closes it, and the rows are reachable with the arrow keys and **Enter**. Escape anywhere clears the field
and brings the whole shelf back.

**A box opened out of a search opens *in* that search.** Click a result in the left pane and the box
opens carrying the term with it: it lands in the field of whatever kind of box it is — whatever kind of box it is, list-bearing or not — and
the box shows the children that answered rather than the whole shelf, so you arrive where the match was
with the reason still on screen. Where there is no list to narrow, the window is already unrolled on the
term instead. The term is read at the click, not remembered from when the list was drawn, and it joins
the box's list of searched-for terms. It is not remembered as a *filter*: navigate away and back with an
empty search box and the shelf is whole again.
 There is no second search box: the folder used to carry its own `name or type` field, and one
field per box replaced it (ADR 34, with the window's role narrowed by ADR 39).

Where a result opens depends on where you asked. From the library, it becomes the current box and the
box you searched from stays in the crumb line. Inside a viewport, it replaces that pane's box and the
crumb line shows the route down to it, each crumb clickable — as if you had clicked through the
children yourself, and never the pane's earlier wanderings. A search in one pane never moves another.
When the match is a file rather than a box, the box that holds it opens, the field is already filled
with the word, and the window is already unrolled on it, because you wanted the file, not a box you
must now search again.

What each kind of box draws is set out in [What each box shows](#what-each-box-shows) — the four rows worth knowing before you open anything: a **note** is a sheet you can switch between editing and reading, a **folder** is a list or a set of icons, a **grid** is rows and columns you size, and a **viewport** lends its space to other boxes. Anything with no view of its own shows its icon and its detail panel.

A box created **from** another box — "New box here" on a folder, "New box in X" in
a right-click menu, a cell of a grid — has that box already chosen as its parent,
and after creating it the view stays on the parent, because that is where the user
was looking. The parent is fetched by uuid if the left panel does not show it, so
this holds in a library bigger than one page and while a search has the panel
narrowed.

Display mode, list-or-icons, text appearance, the grid layout, the desktop surface
and the searches a folder has remembered are saved in the box's own `config`
column, so they come back tomorrow in another browser. A grid remembers cells by box **uuid**, and a desktop its icon
positions, so renaming a box does not scramble either; shrinking a grid leaves
children linked and shows them as "Not placed", and so does a desktop whose icons
nobody has placed yet.

Every sensor box — `sensor`, `mqtt`, `http`, `home-assistant` — turns what arrived into a value
using three templates the owner writes in the box's own settings drawer: *the value in what
arrived* (`{{ data.pressure }}`), *how the value is worded* (`{{ extracted }} {{ data.unit }}`), and
*how the words are dressed* (`<b class="{{ "sensor-bad" if number > 1.3 else "sensor-good" }}">`).
They are Jinja, run by the server in a sandbox with no loader, and the styling one is rebuilt
against an allow-list of formatting tags, page classes and CSS properties before it reaches the
page: `<script>`, `<img onerror>`, `href="javascript:"`, `onclick` and `position: fixed` do not
survive it. A template that fails says so beside the reading, which stays on screen. ADR 21.

When the value is a number, the box can also be given a **scale**: `min`, `max`, the range split
into colour intervals that must **fully cover it** — a stop short of `max` is refused, with the
reading that would fall through named — and one of four pictures: a gauge, a horizontal bar, a
vertical bar or a progression bar. Colours come from 16 presets served by the API, or from the
colour picker. A value outside the range is clamped to the end of the bar **and says so** (`above
10`), because a sensor pinned at the top of its scale otherwise looks exactly like a sensor at the
top of its range. For a value that stays text, the alternative to a styling template is a list of
regular expressions, each with a text and a background colour, first match wins, plus a default.
The server decides where the value sits; the page only paints it, so an opened box, a grid cell and
`box value` cannot disagree. ADR 22.

**View ports** are the way to look at two boxes at once. A viewport box is never replaced by what
you open in it: a *tiling* viewport splits its space into panes (split, drag the bar, close a pane
and its sibling takes the room back — the last pane refuses), a *windowed* one floats windows you
move, resize, raise and close — and a window can be minimized to its title, maximized over the whole
board, and restored to the rectangle it had, which is kept rather than recomputed. A window fades to
whatever the **transparency** slider says — **0 % is invisible, 100 % is solid**, and it starts at 0 —
while the focused one stays solid either way, so the desk can show through the pile without the box you
are reading going with it. The stored number is the same one an existing box already carried, so an old
library opens looking the way you left it. Every pane and window brings its own gear: a box's settings row folds
and unfolds inside the space it occupies, so changing a grid's columns does not unfold the checklist
beside it, and the gear in the page's top line folds only the box on the main screen. What a viewport
shows, it holds: those boxes are its children, so the tree view and the side list agree with the page.
Opening a box **inside a pane stays inside that pane**, however deep it is embedded: a folder in a grid
cell in a pane answers to that pane's path line, and the settings its own view writes land on the
folder — not on the grid, and not on the viewport around it. **Where you walked inside a pane is
remembered** through every operation on the viewport — a split, closing a neighbour, dragging the bar,
a drop, a reload — so a pane three folders deep stays three folders deep instead of snapping back to
the box the layout names (ADR 29). The walk lives in the browser tab and the layout lives in the
library, and they are not the same fact: what a space was *told* to show is shared, how you got there
is yours. So a second tab on the same library opens each pane at the box the layout names, and a pane
you were told to show something else in — a drop, another tab — starts a new walk from there. Dragging a box onto another box **copies the link**
(it is filed in both); **holding Shift moves it** (filed here, taken off where it came from, in one
request so it is never nowhere). ADR 23.

An MQTT box is the one kind that talks to something outside the machine: while
its page is open the server subscribes to `mqtt://` or `mqtts://host[:port]/topic`
and pushes every message to the page. `aiomqtt` ships with the install; if it is not there, the
box type says *"not in this install"* while you are still choosing it, the dialog names the module
and prints the command that repairs this particular install, and the opened box shows **"cannot
listen"** with that sentence instead of a message about a dropped stream — a box that cannot
work is refused, not retried forever. `box value NAME` reads one message and exits, for scripts,
and reports the same way. ADR 18.

`box serve` and `box tui` import their own framework when you ask for them, so `box --help`
and every filing command work even in an install whose web or text UI library has gone missing;
asking for one that cannot start prints the line that repairs it.

The database lives at `$BOXGANIZER_DB`, else
`$XDG_DATA_HOME/boxganizer/boxes.db`, else `~/.local/share/boxganizer/boxes.db`.
Point at another file anywhere with `--db path/to/boxes.db`.

Boxes whose words Boxganizer writes itself — a checklist — keep them next to that file, in
`files/`, one file per box named after the box's uuid. They are markdown, they are indexed
through a resource row like any other file, and they are not deleted when the box is: the
row can be re-created, the words cannot.

A **folder** marked for indexing is not read — there is nothing to read in a folder — it is
walked, and every text file inside it becomes a resource of its own: its own modification
time, its own entry in the index, its own answer to "has this changed". The folder keeps a
link to each one it brought in, so a file deleted from the folder stops answering searches,
and a file the library also holds directly is never discarded by the folder. Hidden entries,
symlinks and files nothing can read words out of are left alone, and a hit inside a folder
answers with the folder and names the file that spoke. ADR 16.
