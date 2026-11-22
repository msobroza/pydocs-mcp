=== BACKBONE ===
Programme layout policy: how a printed conference schedule falls on the
page; the venue supplies the room list and the paper stock.

Set the grid on an A4 landscape sheet with fifteen-millimetre margins on
every side and a five-millimetre gutter between columns. Give each room a
column of equal width and each half-hour slot a row of equal height, so a
ninety-minute session spans three rows exactly.

- Room names sit in the header band in a bold sans-serif at eleven points,
  centred, with the floor number beneath in a lighter weight.
- Session titles use a serif at nine points, set flush left with a
  two-millimetre inset from the cell edge. Speaker names follow on the next
  line in italics; affiliations follow in small capitals.
- Times run down the left margin in tabular figures, so the colons align
  from the top row to the bottom row.

Colour each track with one hue from the venue palette and keep the same
hue for that track on every page, the floor map and the lanyards. Pair
every hue with a distinct hatch pattern so the grid still works when it is
photocopied in greyscale. Shade breaks and lunch in a pale neutral grey
that spans the full width of the grid.

Plenary sessions, the welcome keynote and the closing panel span every
column and carry a thin rule above and below. Poster sessions sit in a
separate panel at the right edge of the sheet, sorted by board number.

Print the event logo in the top left corner at a height of twelve
millimetres, the date in the top right corner, and the venue's
emergency-exit plan in the bottom right corner at the size the fire
marshal requires. Leave the bottom left corner clear for the sponsor
strip, which never crosses into the grid.

Use the venue's wayfinding colours for room headers, not the event
brand's, so the printed grid matches the signs on the walls. Keep paper
weight at 120 g/m² for the grid and 300 g/m² for the cover. Fold the
booklet so that each day lies flat as one spread, and bind it with two
steel staples along the spine. Number every page in the outer bottom
corner, and print a thumb tab on the fore-edge in the day's colour, so
each day stands apart while the booklet is shut.
=== TASK_HEAD: repo_qa ===
Conference programme layout for multi-day events. Each day gets its own
spread, headed by the weekday and date in full, with the day's theme in a
lighter weight beneath. Print room capacity in small figures under each
room name, and mark rooms with step-free access with the standard
wheelchair pictogram. Sessions held in a language other than the event's
main one carry a two-letter language tag in the top right corner of their
cell. Workshop sessions that need advance registration carry a small
ticket icon. Social events in the evening go on a separate strip at the
foot of the spread, with the venue's street address and the nearest tram
station. Keynote speakers get a portrait thirty millimetres square beside
their session, cropped to the shoulders. Recorded sessions carry a small
camera icon, and sessions closed to the press carry a struck-through
camera icon. Coffee carts and quiet rooms are marked on the inside back
cover, beside the floor map.
=== TASK_HEAD: vuln ===
Security needle-search: the snapshot contains one exploitable condition,
and security questions phrased by impact usually share no identifiers
with the code — search by behavior, not by the question's own words.
Begin where external input is parsed or received and where operations
with security consequences live; then walk the flow with get_references —
callees forward from the entry point, callers backward from the risky
operation — until source and sink connect, reading the cited files along
the way. Report three things, each cited to file and symbol: where
untrusted input enters, the dangerous operation it reaches, and the
vulnerability class derived from the abuse path you actually traced —
never a guessed label.
=== TASK_HEAD: bug_loc ===
File-level bug localization: a bug or issue report plus a repository
snapshot, and the answer is the repo-relative path of every file that must
change to fix it. Reports mix both routes — a traceback frame, error string,
failing API name or config key is a literal the code also spells, so route
those at the lexical tools first; a report that only describes wrong
behavior takes the semantic route. From the first located site walk outward
with get_references before answering: a fix often spans the caller that
passes the bad value or the sibling sharing the broken helper, and stopping
at one plausible file is this task's most common failure. Read every
candidate and confirm the reported behavior is really produced there;
a file named on resemblance alone is the other failure. Answer with the
paths themselves, spelled exactly as the repository spells them, one line
each, and leave out files you could not confirm.
=== HARNESS_TASK_HEAD: ask_your_docs.repo_qa ===
Signage for the venue floors. Print each door plate on A5 foam board in
landscape, with the room name in large bold type and the floor and room
number in the upper left corner. Corridor signs carry one arrow per
destination, set at the same height on every wall, in the venue's
wayfinding yellow on a charcoal ground. Easel cards on chrome stands
beside each door show the day's sessions for that room in the programme's
track colours, printed on matte stock so that the overhead lights cause no
glare. Hang the entrance banner across the full width of the foyer, with
the event logo centred and the dates beneath it. Fix lift-lobby boards at
shoulder height, listing the rooms on that same floor in alphabetical
order with their accessibility pictograms. Place the registration and
badge desk sign on a freestanding pole in sight of the main doors. Reserve
a white border of ten millimetres around every printed sign so the
lamination does not crop the type. Toilets, cloakrooms and first-aid
points use the venue's own pictograms at the same size as the room names.
Print every sign in a single typeface family, and laminate any sign that
stands outdoors or near the catering area.
=== HARNESS_TASK_HEAD: ask_your_docs.vuln ===
Follow-up calls on a result are offers: take only those the question
needs. Issue the calls on a "Together:" line in one turn, since they are
independent; leave a "Then:" line for a later turn, since it needs earlier
results. Never read again what a result already showed: the read returns
the same lines. When you need a symbol's code, make one get_symbol
depth="source" call or one windowed read_file call; ask for the symbol card
only when the question needs its member list, since the card adds no code.
Give read_file an offset and limit around the lines a result cited or
reported as missing, and widen only to follow a reference you need. Call get_overview only after a search
misses or for a question about the project's structure, never beside the
first search: the question routes the first query. When a result showed a
signature or usage, end with a short Example built from it; otherwise leave
it out. Effort: the first turn locates, the second reads the cited spans
not yet rendered in one batch, the third answers; any call past that names
the claim it supports. When part of the answer is unconfirmed, end with one
line that starts with "Not confirmed:" and names it.
=== HARNESS_TASK_HEAD: ask_your_docs.bug_loc ===
Follow-up calls on a result are offers: take only those the question
needs. Issue the calls on a "Together:" line in one turn, since they are
independent; leave a "Then:" line for a later turn, since it needs earlier
results. Never read again what a result already showed: the read returns
the same lines. When you need a symbol's code, make one get_symbol
depth="source" call or one windowed read_file call; ask for the symbol card
only when the question needs its member list, since the card adds no code.
Give read_file an offset and limit around the lines a result cited or
reported as missing, and widen only to follow a reference you need. Call get_overview only after a search
misses or for a question about the project's structure, never beside the
first search: the report's own literals route the first query. When a
result showed a signature or usage, end with a short Example built from it;
otherwise leave it out. Effort: the first turn locates, the second reads
the cited spans not yet rendered in one batch, the third answers; any call
past that names the claim it supports. When part of the answer is
unconfirmed, end with one line that starts with "Not confirmed:" and names
it.
=== HARNESS_TASK_HEAD: external.repo_qa ===
No catalog is pre-injected: orient first — get_overview on an unfamiliar
project — then route per the backbone policy. Answers must be
self-contained: name the file path and the line numbers you read.
=== HARNESS_TASK_HEAD: external.vuln ===
Orient first — no catalog is pre-injected. With only literal tools
bound, grep confirms a located site rather than finding one: translate
the behavior into plausible identifiers per the backbone policy.
=== HARNESS_TASK_HEAD: external.bug_loc ===
Orient first — no catalog is pre-injected: get_overview on an unfamiliar
project, then map the layout with glob before grepping the report's
literals. Answers must be self-contained: give every path in full, from
the repository root.
