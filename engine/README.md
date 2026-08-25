# engine/

The whole system. Python standard library only, and no state of its own. Every
module reads files and writes files, except the four whose docstrings say
otherwise: `snapshot` fetches a page, `install` clones a repository, `diagrams`
installs mermaid-cli on first use, and `facts` asks `typst` and `git` what
version they are — and cannot fail a build when they will not say.

```
config.py      vault discovery, report-maker.toml → absolute paths
vault.py       designs and brand packs, discovered from folders
workspace.py   which reports exist (nested), their metadata, their staleness
brand.py       a brand pack → Typst tokens + mermaid theme + mermaid CSS
brandcmd.py    reading, editing and previewing a pack — the `brand` commands
library.py     stages each design into <vault>/.build/design/<id>/
install.py     designs fetched from a git repository, and their provenance
diagrams.py    mermaid .mmd → branded .svg (headless Chrome via mermaid-cli)
build.py       typst compile → PDF
pages.py       typst compile --format png → page images + pages.json
html.py        one self-contained .html: the pages, the sources, the excerpts
manifest.py    out/manifest.json
sources.py     sources.yml, parsed as data and rewritten a block at a time
snapshot.py    fetch a cited page, archive it beside the report, extract its text
cite.py        one URL → an archived copy and a bibliography entry
verify.py      re-fetch the archive and report what moved
data.py        CSV files registered as sources, and checksummed against the report
datarev.py     the sanctioned way to move a checksum: dated revisions of a CSV
facts.py       what a build knows about itself, for the colophon
check.py       the citation rule, enforced
score.py       evidence density: cited / assessed / unmarked, per section and line
diffing.py     what changed since a revision, in claims rather than lines
search.py      tf-idf over prose, bibliographies, archived pages and diagrams
notes.py       todos.md and notes.md — the thinking that is not the report
gitsync.py     the vault's own git repository: state, commit, push, log
imagehash.py   a rendered page's perceptual hash, for the golden-render test
mcp.py         the vault as an MCP server, with the rule at the point of writing
scaffold.py    new vaults, new reports, new designs
cli.py         the command line over all of the above
typst-less:    templates/ ships the built-in designs and the brand specimen,
               brand/ the default pack
```

## Using it as a library

Every command is a function on a `Config`. Nothing prompts, nothing serves,
nothing blocks — which is the point: an agent or a CI job drives the same code
path a human does.

```python
from pathlib import Path
from engine import config, library, vault, diagrams, build, pages, manifest, check

cfg = config.load(Path("/path/to/vault"))     # or None to search upwards from cwd

vault.templates(cfg)                          # {id: Template}, built-ins + vault
vault.groups(cfg)                             # {group: [Template]}

library.stage(cfg)                            # designs → .build/design/<id>/
diagrams.build(cfg)                           # .mmd → .svg, per design's brand
build.build(cfg, "clients/acme")              # a report, a folder, or all of them
pages.build(cfg, ppi=144)                     # → out/pages/<report-id>/
manifest.build(cfg)                           # → out/manifest.json

findings = check.check(cfg)
errors = [f for f in findings if f.level == "error"]
```

The evidence layer works the same way, and every function that reaches the
network takes the fetcher as an argument:

```python
from engine import cite, score, snapshot, sources, verify, workspace

report = workspace.reports(cfg, "clients/acme/2026-08-12-audit")[0]

sources.parse(report.sources)                 # [Source], with line numbers and raw text
sources.rows(report, snapshots=snapshot.records(report))   # what `sources --json` prints

cite.cite(cfg, "acme/2026-08-12-audit", url)  # fetch → archive → sources.yml
cite.cite(cfg, target, url, fetch=fake)       # …and this is how the tests run

verify.verify(cfg, offline=True)              # [Drift], nothing dialled
score.score(cfg)                              # [ReportScore], with per-line classes
```

```python
from engine import datarev, facts, notes

datarev.revisions(report, "data/prices.csv")  # [Revision], newest first
datarev.reregister(report, path, note="Q3")   # the only call that moves a sha
facts.gather(cfg, report)                     # what the colophon prints
notes.scan(cfg, target, open_only=True)       # the pad, per report
```

Anything printed for a human lives in a `report_*` function next to the data
one — `report_findings`, `report_drift`, `report_scores`, `report_diffs` — and
anything for a machine in `to_json` / `findings_json`. The CLI chooses between
them; nothing else in the engine prints, so nothing else has to be silenced when
`mcp.py` takes over stdout.

## Design decisions worth knowing

**Folders are the data model.** A report is a folder with a `main.typ`; its path
under `reports/` is its id and everything above the last segment is its group. A
design is a folder under `templates/`; nesting groups it. A brand pack is a
folder with a `brand.json`. There is no index, no registry and no database to
drift from what is on disk — `rglob` is the query engine.

**Everything derived is generated.** A colour exists once, in a brand pack. The
Typst tokens, the mermaid theme variables, the mermaid stylesheet, the mermaid
`classDef`s and the HTML export's palette are all produced from it.
Hand-maintaining those five copies is what makes a diagram drift from the report
around it.

**Designs are staged, not imported in place.** Typst can only import files under
`--root`, and `--root` must be the vault. So each design — its own Typst files,
plus whatever it inherits, plus `tokens.typ` for its brand pack — is assembled
into `.build/design/<id>/`, and reports import `/.build/design/<id>/report.typ`.
The engine can then live anywhere, and inheritance costs nothing at compile time.

**The citation rule is a build step, not a convention.** `check.py` reads the
Typst source and the bibliography and fails the build on an uncited figure, a
bare `image()`, or a `@key` with no entry. A rule nobody enforces is a rule that
is already false somewhere in the back half of a long report.

**Evidence is archived, and archived beside the report.** A `@key` that resolves
to a dead link proves nothing, so `cite` keeps the bytes in
`reports/<id>/snapshots/`. Not in a vault-wide cache: moving the report folder
has to move its evidence, or the folder stops being the unit that can be handed
over. Nothing overwrites a snapshot — `verify --refresh` rotates the old copy to
`<key>.<date>.html` and writes a new record.

**Fetching is a parameter, not a call.** `snapshot.http_fetch` is only the
default value of a `Fetcher` argument threaded through `cite` and `verify`, so
the whole test suite runs with the network unplugged and `--offline` is a real
mode rather than a promise. The one thing not negotiable through that seam is
the scheme check: `http` and `https` only, applied to the URL given *and* to
wherever the redirects land, because a `url:` in a bibliography is untrusted
input.

**Reads never raise; writes never reformat.** `sources.py` parses the slice of
YAML hayagriva actually uses and degrades an unparseable block to an entry with
no fields rather than an exception — a confusing bibliography must still list,
still count towards the key set and still be editable. Writing is the mirror
image: `append`, `upsert` and `remove` rewrite the one block they are named for
and leave every other byte alone, comments and ordering included. A tool that
silently reformats on save is a tool people stop running.

**For an agent, the gate moves to the point of writing.** A build-time check is
one a model can walk past for hours. `mcp.py` therefore runs `check` inside
`write_report`, compares the findings against the ones already there, and
restores the file byte for byte when the write introduced a new error. See
"The MCP server" below for the two things that break the protocol outright.

## The shared scanner

Five other modules read Typst source, and they all read it the same way —
through the scanner in `check.py`:

| function | what it gives you |
|---|---|
| `scrub(src)` | comments and code blocks blanked **to spaces**, so every offset still maps to its original line |
| `calls(src, name)` | every `#name(…)` with its span and its argument text, paren-matched |
| `call_span(src, paren)` | the matching close paren, for a call you found yourself |
| `arguments(args)` | that argument text split into positional and named, respecting nesting and strings |
| `string_literal(text)` | a Typst string literal's value, or `None` when it is not one |
| `cited_keys(src)` | every `@key` with its offset, punctuation stripped the way Typst strips it |
| `labels(src)` | the `<labels>` the document defines, which is the only way to tell a cross-reference from a citation |
| `line_of(src, index)` | an offset as a 1-based line |

Reuse them. `score.py` classifies statements with them, `diffing.py` extracts
claims with them, `html.py` finds cited sentences with them, `search.py` blanks
markup with them, and `data.py` reaches for them lazily (`_rules()`) only to
avoid an import cycle. A second scanner would be a second definition of what a
citation is, and the two would disagree on the day it mattered.

## Adding a design

`templates/<id>/report.typ` defines a `report(…)` show-rule function; anything it
does not define is inherited from the template named in `extends`. The staged
directory is flat, so a design's own files import their siblings by name
(`#import "theme.typ"`), and can reach another design by its staged path
(`#import "/.build/design/base/report.typ": running-header`) — which is how
`brief` borrows the base running chrome instead of copying it.

Staging globs `*.typ`, so dropping a file into a template folder is all it takes
to add a component: `data.typ` arrived that way, and nothing had to be edited to
make `srctable` available to every design that inherits from `base`.

## Adding a rule to the checker

Rules live in `check_report()` and share the scanner above; `add(level, code,
index, message)` records a finding at the right line. Add the rule, then add a
test that fails without it — `tests/test_engine.py` for the core rules,
`tests/test_check_quotes.py` for the ones about quotations. A linter with an
untested rule is a linter that quietly stops firing.

Two conventions the newer rules follow:

**A rule that needs evidence stays silent until there is some.** E008 and E009
only run when `snapshots/` exists at all (`snapshot.dir_for(report).is_dir()`),
because a linter that turns an entire existing vault red is a linter people
switch off. The first snapshot in a report turns both rules on for that report.

**A rule about a subsystem lives with the subsystem, and is still reported by
`check`.** The data rules (E010, E011, W005 to W009) are in `data.py` and W010 is
in `score.py`, because the definition each rests on — what a data file is, what a
source family is — belongs to that module and a second copy in the linter would
be a second answer. They leave those modules as plain tuples and become
`check.Finding`s at the boundary in `to_findings`, built field by field so
`Finding` can grow without silently dropping them.

Living elsewhere is not the same as being optional. `check._data_findings` calls
`data.findings` on every report that has a `data/` folder *or* a `srctable(` in
its source, so E011 — the rule standing between a refreshed export and a
signed-off report — fires from `check`, from `all`, from the app and from MCP.
The guard is what keeps the promise that a vault with no CSV never pays for
scanning one; `report-maker data check` remains the narrow command for when a
data file is the only thing in question.

## Quote checking

E009 is the only rule that can catch a sentence which merely looks sourced, and
it is three small functions:

```python
check.fold(text)               # whitespace collapsed, quotes and dashes unified
check.quote_found(q, text)     # fold + casefold, then substring — not similarity
check.closest_span(q, text)    # the nearest passage, when one is above 0.75
```

Substring rather than ratio is deliberate: a quotation that only nearly matches
*is* a misquotation, and the writer has to see it. `closest_span` exists so the
message can show what the page does say, which is almost always enough to
explain the failure. It slides a window the length of the quote in words and
lets `difflib`'s cheap upper bounds reject nearly all of them, so it stays
affordable on a page-sized snapshot.

The comparison is against `snapshots/<key>.txt`, and a key may legally contain
characters a filesystem will not take — `snapshot._filename` maps them, which is
why messages name the file rather than the key.

## Revising a data file

`data.py` records a CSV's sha256 in `sources.yml` and E011 fires the moment the
bytes stop matching. That rule only works because nothing in the engine may
re-hash a file on save: a checksum a tool quietly refreshes is not a checksum,
and the feature would keep all of its machinery and lose its guarantee.

`datarev.py` is the one sanctioned way through it, and `reregister` is the only
function anywhere in the engine that moves a recorded sha. Two rules hold the
archive up, both borrowed from `verify --refresh`:

- **A dated revision is never overwritten.** `_free_name` appends `-2`, `-3` on a
  collision, because a revision lost to a filename clash defeats the point.
- **`archive` dates a copy by the file's own mtime**, not by today. The honest
  answer to "as of when were these the numbers?" is when they were last written,
  and it is the same date `data.source_entry` puts in the entry, so the filename
  and the bibliography agree about which version they mean. `copy2` keeps the
  mtime for the same reason.

`archive` is a no-op against the *newest* revision only. Re-running a
half-finished save must not litter the folder, but an older revision holding the
same bytes means the numbers went away and came back — history worth keeping.

`reregister` starts from the fields already in `sources.yml` rather than from a
fresh `source_entry`, so a title somebody chose by hand, or a field this module
has never heard of, survives a revision; only `date` and `note` are its to move.
It returns a summary rather than printing one, and the summary is the receipt —
`_headline` renders "412 rows → 418 rows, +6" once, here, so the CLI and the app
cannot describe the same change differently. Nothing in this module reads or
writes `main.typ`: a revision changes what the numbers are, never what the report
says about them.

`status()` is the question a CSV editor has to ask before it lets anybody type.
`matches` is true only when both checksums are known and equal — an unregistered
file is not "matching", and collapsing the two would let a UI paint it green.

## The colophon

`facts.py` gathers what a build knows about itself into
`.build/facts/<report-id>.json`, written immediately before Typst runs so a
design can read it with `json()`. Four groups: `toolchain`, `provenance`,
`evidence`, `inputs`.

It exists because everything else here stops at the vault. The pages are
archived, the quotes are checked, `verify` says which have moved — and the person
holding the PDF gets the same object either way. A caveat lives in the document
somebody happened to write it in, and the reader of the *next* document never
sees it; a colophon cannot drift that way, because it is generated from the run
that produced the file it is printed in.

Two rules make it safe to run on every build. **Nothing here may ever fail one:**
a fact that cannot be gathered degrades to `unknown` and names itself in `gaps`,
applied per group, so an unreadable `sources.yml` does not also blank out the
toolchain. **The file is written even when every fact failed**, because the
design reads a path and a missing one is a compile error — an all-`unknown`
colophon is the honest output for a build nobody could describe.

`build.compile_report` takes `with_facts`, on by default. Gathering reads every
snapshot record and hashes every registered CSV, which is worth skipping on a
vault of hundreds of reports where no design prints a colophon; a report that
does print one and is built without them fails in Typst on a missing file, which
is the legible outcome. `watch` writes them once, at the start: Typst re-reads on
every keystroke, so a colophon under `watch` states the facts of the session, and
`build` stays the command whose output is the one to hand over.

## Building past a failure

`build.build` is a list comprehension over `compile_report`, so the first report
that will not compile ends the run. That is the right default for one report and
the wrong one for a vault of two hundred, where the first failure hides whether
the other 199 are fine and one stale report makes the whole vault unbuildable.

`--keep-going` is `cli._build_all`, not a second mode inside `build.py`: it walks
`reports(cfg, target)` itself and calls the same public `build.compile_report`
that `build.build` calls, so the two paths cannot compile a report differently.
Failures are collected, named once at the end, and the command still exits
non-zero. Nothing is forgiven, only deferred.

## The prepared diagram

`diagrams.prepare` writes the file mermaid is actually given: the `.mmd` plus the
`classDef`s generated from the brand pack. `prepared_json` hands the same thing
to the app's live editor, which renders in Chromium rather than through
mermaid-cli, together with the config and the stylesheet *inlined* — a renderer
sandboxed inside the app cannot read arbitrary files, and one that could would
still be free to read the wrong pack's.

The editor is not allowed to assemble its own input. mermaid writes presentation
into inline `style` attributes, so a diagram styled by the stylesheet alone looks
right in a browser and arrives unstyled in the PDF. A preview that can disagree
with the output is worse than no preview, so both sides render the same bytes.
`_assert_html_labels_off` is checked here too: with `htmlLabels` on, every label
goes into a `<foreignObject>` Typst cannot draw, and the diagram reaches the PDF
as boxes with no words in them.

## The pad

`notes.py` reads two optional markdown files beside a report — `todos.md` and
`notes.md` — and neither is ever compiled or checked. That is the module's whole
premise rather than a gap in coverage: the citation rule exists so nothing a
*reader* sees can sit between a cited fact and a marked opinion, and a scratch
pad has no reader but the author. Plain markdown for the same reason folders are
the data model: readable in any editor, diffable in any review, greppable from
any shell, and no schema to keep in sync.

`harvest` is the one thing that leaks the other way. A `// TODO:` or `// FIXME:`
in `main.typ` is the same kind of note written where the thought occurred, and a
list that omitted it could not be trusted to be complete — so it appears in the
same view, read-only. `toggle` refuses on it: a checkbox in a Typst comment is
prose, not state, which is why `SOURCES` has three entries and `WRITABLE` two.

`scan` always counts every item and filters only what it returns, so `--open`
narrows the list without changing the tallies a badge is drawn from.

## The MCP server

`mcp.py` speaks line-delimited JSON-RPC 2.0 on stdin and stdout. Two things
break the protocol outright, and both are handled centrally:

**Nothing but frames may reach stdout.** Every command in the engine prints, and
one stray line desynchronises the client's parser for the rest of the session.
Every tool call therefore runs inside `redirect_stdout`, and what it captures
goes to stderr. Tools that actually want the output — `build` — capture it
themselves, inside that outer guard.

**A fault the model caused is data, not a transport error.** A bad report id, a
missing argument, a vault that is not a vault: all come back as an `isError`
tool result the model can read and correct. Only genuine protocol faults become
JSON-RPC errors, because those say the client is broken, not the reasoning.

Adding a tool is one `Tool(...)` entry with its JSON schema and one
`_tool_<name>` function taking `(server, args)`. If it writes, follow
`_tool_write_report`: run the check, then `introduced(cfg, before, after)`, then
restore on anything new. Two details in there are worth keeping if you write
another such tool. A finding is identified by `(code, path, message)` and
deliberately *not* by its line, or adding a paragraph would make every edit look
like a regression. And the comparison is a multiset difference rather than a set
one, so a report that cited one missing key and now cites two has introduced an
error rather than none.

## git

`gitsync.py` is a wrapper thin enough to read in one sitting, built as a list of
refusals rather than features: never `--force`, never without a configured
upstream, never from a detached HEAD, never when the branch is behind, never a
path outside the vault. `push_refusal(state)` is the single place those live, so
the app and the CLI cannot answer the question differently.

`state()` is the exception to everything else here: the app polls it on a timer,
so it must be cheap and must never raise. A folder that is not a repository is a
fact, not an error — `repo=False`, which is what the app's "initialise git here"
explainer is drawn from. Nothing fetches, so `behind` is only as fresh as the
last fetch; the remote's own rejection is the backstop, and its message is
passed through verbatim rather than summarised.

## The golden render

Every other test reads source files, and none of them can see the failure that
matters most to a document tool: the design broke. `tests/test_render.py`
renders page 1 of each example report and compares a perceptual hash against
`tests/golden/<platform>/<id>.hash`. The platform segment is `sys.platform`, and
it is there because a golden is a recording of one machine rather than a fact
about the repository: the page is a rendering, a rendering is a function of the
font book that produced it, and the brand names faces macOS has and Linux does
not. One flat directory quietly asserted the opposite, and the first CI run on
two operating systems was the thing that noticed.

`imagehash.py` is the PNG decoder that makes that possible without a dependency:
`zlib` inflates, the five PNG filters are undone scanline by scanline, and the
page is box-filtered down to a 9×8 grid whose left-to-right differences are the
64-bit hash. The box filter is load-bearing — with nearest-neighbour sampling the
pixel a cell lands on may be inside a glyph in one Typst version and beside it in
the next, and the hash would drift for reasons that have nothing to do with the
design. Anything outside the subset Typst emits (8-bit, non-interlaced, grey,
RGB, RGBA, palette) raises rather than guessing.

Tolerance is 6 bits, measured: rerendering the demo vault at 150 ppi instead of
110 moves the hash by one. A failure is not automatically a bug — look at the
page, and if the new design is right, re-record with
`REPORT_MAKER_UPDATE_GOLDEN=1` — on every platform that has a folder, or the
others start failing the moment the design moves on purpose.

The module skips in three cases, and each of them is "this machine cannot
answer" rather than "the answer is yes": Typst is not installed, this platform
has no recordings, or a family the brand asks for first is missing from `typst
fonts`. The last is the subtle one. With the brand's faces absent Typst renders
in its own fallbacks, and the hash stops responding to the brand at all
— editing `fonts.display` would not move a single bit — so a pass there would be
structurally blind to the whole class of change the test exists to catch. Past
those three, a report with no golden is a failure and not a skip: the platform
demonstrably has recordings, so an unrecorded report is one nobody approved.
