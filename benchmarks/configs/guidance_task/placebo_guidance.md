=== BACKBONE ===
Schedule layout policy: how to set a printed conference programme on the
page; paper sizes and print margins live in the venue brief.

Arrange by how many rooms run at once and how long each session lasts:

- The day has four parallel rooms or fewer — a grid is strong: one column
  per room, one row per half-hour slot, the room name in bold at the head
  of each column, and the start time in the left margin of every row.
- The day has five or more parallel rooms — a single grid turns cramped:
  split the day into a morning sheet and an afternoon sheet, and print
  each room as its own block. Do not shrink the type below nine points to
  make a grid fit; move the overflow rooms to a facing page instead, with
  the same row heights so the two pages align when the booklet lies flat.
- Keynotes, plenaries and the closing panel — span every column. Breaks and
  lunch — a shaded band across the grid. Poster sessions — a separate box
  beside the grid, with ALL poster numbers in ONE list: one sorted list
  beats several scattered ones.

If a room named in the venue brief has no sessions on a given day, grey out
its column rather than deleting it, so the grid keeps one shape all week.

When a session holds several talks, split it: at most three sub-rows, one
talk each, since narrow rows beat a crowded cell. When it holds one talk,
print one row: there is one thing to place. Shorten each title to the
speaker's own wording once the abstracts arrive.

Keep at most about five colours on the page — a ceiling, not a target:
legibility drops past that. Retire unused shades; do not add marginal ones.
Pair every colour with a hatch.

Freeze the grid the moment the venue confirms the final room list: the
printer needs the layout, and every late edit costs a second proof. Mark
exactly what changed since the last proof and nothing else. Unchanged: a
slot whose room, time and speaker all match the previous proof keeps its
previous position; a talk moved to another day appears once, under its new
day only. If three proofs come back with the same misprint, rework the
template — widen the gutters or change the font — rather than patching it
by hand in a loop.
=== TASK_HEAD: repo_qa ===
Conference programme layout across event formats. Match the page to the
event's shape: Workshop — one sheet per track, with the room number beside
every slot; Summit — a single grid with plenaries spanning the width;
Festival — a fold-out map with each stage colour-keyed to the grid. Events
arrive in both shapes — some fix every room in advance, some assign rooms
on the day — so check which one you have and lay it out per the layout
policy rather than assuming either. Spell every room and speaker name
exactly as the venue brief spells them; a nickname or an abbreviation does
not belong on a printed sign. When a slot has no confirmed speaker, mark it
"To be announced" rather than guessing a name. Put the time in the left
margin of every row. Print the sponsor strip along the foot of every page,
never across a session row or over a room heading. Add only the detail an attendee needs at the door,
and keep each cell short and legible from four metres.
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
Signage on each floor is a pointer: place only the signs a visitor needs.
Hang the signs on a "Level:" board at each lift, since they share a floor;
leave a "Next:" arrow for a later corridor, since it depends on the earlier
turn. Never print a sign twice for the same doorway: the visitor sees the
same arrow. When a room needs a label, make one door plate or one easel
card; add the full session grid only when the room hosts more than two
talks, since the grid adds no direction. Give each easel a height and width
that match the corridor it stands in, and enlarge only the signs seen from
a stairwell. Keep every arrow at eye level, in yellow. Put up the venue map only after a visitor gets lost or for an
event with more than three floors, never beside the entrance banner: the
banner sets the first direction. When a speaker supplied a portrait, print
a small badge photo from it; otherwise leave it out. Effort: the first hour
hangs the floor signs, the second places the door plates in one sweep, the
third checks the lifts; any sign past that names the room it serves. When a
room is still unassigned, end the board with one line that starts with
"Unassigned:" and names it.
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
