# report-maker

A headless engine for evidence-grade reports, plus a desktop app over it. A
folder-based **vault** in; branded PDFs, page images, rendered diagrams, archived
sources and a JSON manifest out. No database, no server, no prompts — every
command ends by writing a file, so the same code runs from a shell, a Makefile,
CI, or an agent.

It enforces one rule: **something is either cited, or it is an opinion.**

## Install, then make a vault

This repository is the *tool*. Your reports live in a vault: any folder holding
`report-maker.toml`, anywhere on your disk — the way an Obsidian vault is any
folder holding `.obsidian`. One installation serves every vault on the machine.

```bash
brew install typst                               # the only hard dependency
make install                                     # CLI on PATH, app in /Applications
report-maker doctor                              # what is installed, what is missing

mkdir ~/Documents/Reports && cd ~/Documents/Reports
report-maker init                                # this folder is now a vault
report-maker new "Company audit — Example Ltd"
report-maker all
```

`make install` links the CLI into `~/.local/bin`, builds the app and copies it
into `/Applications`; it announces every step, never uses `sudo`, and re-running
it after a `git pull` is how you update. [INSTALL.md](INSTALL.md) says exactly
what it writes and what it refuses to do. If you only want the command line, the
symlink is the whole of it and you can make it yourself:

```bash
ln -s "$PWD/bin/report-maker" ~/.local/bin/
```

Or open the desktop app and click **Create a vault…** — same scaffolding, same
engine. `examples/demo-vault/` is a sample vault this repo ships for development.

## The vault

Everything is a folder, and the folder structure *is* the data model. There is
nothing else to keep in sync.

```
~/Documents/Reports/             ← a vault; the repo is not one
  report-maker.toml              marks the vault root
  reports/                       nests as deep as you like — the path is the id
    clients/acme/2026-08-12-audit/
      main.typ  sources.yml      the report and its bibliography
      diagrams/*.mmd             mermaid sources
      snapshots/*.html .txt .json   archived copies of every cited page
      data/*.csv                 the numbers a table reads, and their dated revisions
      todos.md  notes.md         the pad — never compiled, never cited
    internal/q3/2026-08-01-review/
  templates/                     designs — nesting groups them
    audits/company/              template.toml · report.typ · starter/
    audits/quick/
  brand/                         brand packs
    brand.json                   the default pack
    assets/
    mono/brand.json              a second pack, named "mono"
  out/                           PDFs, page PNGs, HTML bundles, manifest.json  (generated)
  .build/                        staged designs, themes, brand previews, search index  (generated)
```

`out/` mirrors the report tree: `reports/clients/acme/2026-08-12-audit/` builds
to `out/clients/acme/2026-08-12-audit.pdf`,
`out/clients/acme/2026-08-12-audit.html` and
`out/pages/clients/acme/2026-08-12-audit/`.

The evidence does not. Snapshots and data files sit **inside the report folder**,
because evidence that lives somewhere else is evidence that gets separated from
the thing it supports — see [Evidence](#evidence).

Commands run against the nearest vault above the working directory, or the one
named with `-C`: `report-maker -C ~/work/vault all`. The engine keeps no state of
its own, so nothing in this repository knows or cares which vaults exist.

### This repository

```
bin/report-maker      the CLI entry point
engine/               the engine — Python, standard library only
app/                  the desktop app (Electron)
examples/demo-vault/  a sample vault, for development and the app's smoke test
tests/                engine unit tests
```

## Designs

A design is a folder under `templates/`. Its id is its path, so nesting is
grouping — `templates/audits/company/` is id `audits/company`, group `audits`.

```bash
report-maker templates                                  # what exists, by group
report-maker template new audits/company --from base    # an editable copy
report-maker template show audits/company               # what it inherits
report-maker template install https://github.com/you/house-style   # somebody else's
report-maker new "Acme audit" --into clients/acme --template audits/company
```

Two designs ship with the engine, and a vault design of the same id shadows
them entirely:

| id | what it is |
|---|---|
| `base` | full cover, contents, findings, scorecards, appended references |
| `brief` | two to six pages: a letterhead, then straight into the argument |

Inside a design folder:

```
templates/audits/company/
  template.toml    title, description, kind, extends, brand
  report.typ       the design            (optional — inherited if absent)
  components.typ   extra components      (optional — inherited if absent)
  theme.typ        token helpers         (optional — inherited if absent)
  starter/         main.typ, sources.yml, diagrams/*.mmd — what `new` copies
```

`extends` makes a design a delta rather than a fork: `brief` inherits the
components and tokens of `base` and replaces only `report.typ`. Anything the
template does not define comes from its ancestors, so a design that only changes
the starter, or only the brand pack, is a five-line `template.toml`.

Every design is staged into `.build/design/<id>/` before a build — its own files,
its inherited ones, and `tokens.typ` generated from its brand pack — and a report
imports it from there:

```typst
#import "/.build/design/audits/company/report.typ": report
```

That import line is also the record of which design a report uses; `list --json`
and the manifest read it back.

A design fetched with `template install` keeps a `.installed.json` beside its
`template.toml` recording the repository, the ref and the resolved commit. That
file is the whole registry: `template update` and `template uninstall` refuse to
touch a design that does not have one, because losing your own design to a
command aimed at somebody else's is not a recoverable mistake. An installed
design is still code that runs at build time, and the install says so.

## Commands

Making a report, and building it:

| command | what it does |
|---|---|
| `report-maker init` | make the current directory a vault |
| `report-maker new "Title"` | scaffold a report (`--into <folder>`, `--template <id>`, `--with-diagram`) |
| `report-maker list [target]` | reports, grouped by folder; `--json` for the machine-readable form |
| `report-maker templates` | designs, grouped by folder |
| `report-maker template new <id>` | create an editable design (`--from <id>`, `--thin`) |
| `report-maker template show <id>` | what a design is, and what it inherits |
| `report-maker template install <url>` | fetch a design from a git repository (`update`, `uninstall`, `list --installed`) |
| `report-maker stage` | regenerate every design into `.build/design/` |
| `report-maker diagrams [target]` | mermaid `.mmd` → branded `.svg` (`--prepare <file>` emits the assembled input instead) |
| `report-maker build [target]` | Typst → PDF (`--keep-going` builds the rest of the vault past a broken report) |
| `report-maker pages [target]` | page PNGs plus `pages.json` |
| `report-maker manifest` | `out/manifest.json` |
| `report-maker all [target]` | all of the above, in order, `check` last (`--html` adds the bundles) |
| `report-maker watch <target>` | live rebuild while writing |
| `report-maker todos [target]` | the tasks on a report's pad (`--add`, `--check`, `--uncheck`, `--open`) |
| `report-maker notes <target>` | that report's `notes.md`, as it stands |
| `report-maker doctor` | tool availability |
| `report-maker --version` | which engine this is |
| `report-maker clean` | remove generated output (`--all` also drops mermaid-cli) |

Evidence, and reading the vault back:

| command | what it does |
|---|---|
| `report-maker check [target]` | enforce the citation rule; non-zero exit on any error (`--json`, `--score`) |
| `report-maker sources <target>` | the report's bibliography, with use counts and snapshot state |
| `report-maker cite <target> <url>` | fetch a page, archive it, add it to `sources.yml` |
| `report-maker verify [target]` | re-fetch archived sources and report what moved (`--offline`, `--refresh`) |
| `report-maker score [target]` | how much of each report carries evidence |
| `report-maker diff <target>` | what changed since a git revision, in claims rather than lines |
| `report-maker html [target]` | one self-contained `.html` per report: pages plus evidence |
| `report-maker data add <target> <csv>` | register a CSV so a table can cite it (`list`, `check`) |
| `report-maker data revise <target> <csv>` | keep a dated copy, then move the recorded checksum onto it (`revisions`, `status`) |
| `report-maker data absence <target> <corpus> <query>` | file a search that returned nothing, so the absence can be cited |
| `report-maker find <query>` | search prose, bibliographies, archived pages and diagrams |
| `report-maker index` | build or refresh the search index |
| `report-maker sync` | commit the vault to git, and optionally `--push` |
| `report-maker brand <sub>` | brand packs: `list`, `show`, `new`, `set`, `preview` |
| `report-maker mcp` | serve the vault to an agent over MCP (stdio JSON-RPC) |

A **target** is a report id (`clients/acme/2026-08-12-audit`), a bare slug when
it is unambiguous, or a folder — `build clients/acme` builds everything filed
under it. `make` wraps every command; `R=<target>` narrows it.

## Writing a report

```bash
cd ~/Documents/Reports
report-maker new "Company audit — Example Ltd" --into clients/example --template base
```

Then edit `sources.yml` **before** the prose. If a claim has no key to point at,
either go and find the source or downgrade the sentence to an assessment. That
ordering is the whole trick — it is what makes the rule cheap to keep.

```typst
A fact about the world @example-page. A judgement about it#assess

#srcfig(
  scorecard((("Domain", 4, 5, "Why this score."),)),
  caption: [Scores are our judgement#assess; the facts behind them are cited above.],
  source: [none — assessment, not evidence],
)
```

### The rule

| Statement | Requirement |
|---|---|
| A fact about the world | carries `@key`, resolving to the report's `sources.yml` |
| A measurement we took | cited like any other source, with the command in an appendix |
| A judgement, rating, forecast, recommendation | ends with `#assess`, or sits in `assessment[…]` |
| A verbatim quotation | `srcquote("…", source: [@key], locator: …)` — checked word for word against the archived page |
| A table or figure | built with `srcfig(…, source: [@key])` |
| A number from a file | `srctable("/reports/<id>/data/<name>.csv", source: [@data-<name>])` |
| An image | built with `srcimage(path, caption:, source:, alt:)` — never a bare `image()` |
| A diagram | mermaid in `diagrams/*.mmd`, rendered by `report-maker diagrams`, placed with `diagram(…)`, cited like any figure |

Brand chrome — the logo on the cover and in the running head — is the one
exemption: page furniture, not evidence.

`report-maker check` reads the source and reports every place the rule is
broken:

```
  error   E002  reports/…/main.typ:41  bare image(…) — use srcimage(…) so the image carries a source
  error   E004  reports/…/main.typ:58  srcfig(…) has no `source:`
  error   E006  reports/…/main.typ:72  @market-size is not defined in sources.yml
  error   E009  reports/…/main.typ:96  the quoted words are not in snapshots/acme-pricing.txt
```

| code | meaning |
|---|---|
| E001 | no `sources:` passed to the design — the report cannot cite anything |
| E002 / E003 | bare `image(…)` / `figure(…)` instead of `srcimage` / `srcfig` |
| E004 | a figure or quotation helper with no `source:` |
| E006 | a `@key` with no entry in `sources.yml` |
| E007 | a `.mmd` with no rendered `.svg` — it would compile to nothing |
| E008 | a `locator:` pointing into a source that was never archived |
| E009 | a `srcquote(…)` whose words are not in the archived copy of the page |
| E010 | a `srctable(…)` reading a file that is not there |
| E011 | a data file whose bytes no longer match the sha256 in `sources.yml` |
| E012 | starter residue — a KPI, a cover field or a bibliography entry still saying what the scaffold said |
| E013 | a bare URL in the prose that never became a source |
| E014 | `status: "final"` while errors stand |
| W001 | a source that is never cited (still listed in References) |
| W002 | a bare `table(…)` outside a `srcfig` |
| W003 | an image or diagram with no `alt:` |
| W004 | a `srcquote(…)` with no `locator:` — "somewhere on the site" is not a citation |
| W005 | a data file no table reads |
| W006 | a `srctable(…)` citing something other than the file it reads |
| W007 | a data column that is empty in every row — a source that failed, arriving as data |
| W008 | a data column carrying one value in every row |
| W009 | a numeric data column that is exactly `0` all the way down |
| W010 | a section or finding whose every citation resolves to one source family |
| W011 | a `status:` nobody recognises — read as if the field were absent |

Some of those rules stay silent until the report gives them something to work on,
which is the difference between a linter people keep and one they switch off.
E008 and E009 need a `snapshots/` folder: a vault that has never run `cite` has
nothing to check a quotation against, and the first archived page in a report
turns both rules on for that report. The data rules — E010, E011 and W005 to W009
— need a `data/` folder or a `srctable(…)` in the source, so a vault with no CSV
in it never pays for scanning one, and a table pointed at a folder that is not
there is still caught. `report-maker data check` runs that set on its own when a
data file is the only thing you are looking at.

Every source in `sources.yml` reaches the References section whether or not a
`@key` points at it, so that section doubles as the inventory of what was
reviewed.

### Draft, review, final

E012 and E013 are strict, and a strict linter that cannot be told "I know, I have
not finished" is a linter that gets run with `--warn-only` for ever. So a report
may declare what it is:

```typst
#show: report.with(title: "Company audit — Example Ltd", status: "draft")
```

`draft`, `review` or `final`. While a report says `draft`, every error is still
found and still printed with its file:line — reported as a warning, so an
unfinished report is not a broken build. `final` is the half that earns the
field: the report is making a claim about itself, so the refusal becomes an error
of its own (E014) while any error stands. `review` and no status at all behave
exactly as this always did, and nothing is ever downgraded in a report that calls
itself final.

The status is also printed on the cover, so a reader holding the PDF sees what
the writer declared. Anything else in that field is W011 and is read as if the
field were absent — a typo must never hand a report the leniency of `draft`.

## Evidence

A citation is a promise that a claim rests on something outside the report, and
the web breaks that promise quietly. A pricing page is edited, a press release is
withdrawn, a URL 404s two quarters after the audit shipped — and none of it shows
up in a build, because Typst compiles a `@key` exactly the same whether the page
behind it still exists or not. A citation that resolves to a dead link proves
nothing.

So when a source is cited, its bytes are kept. One command fetches the page,
reads the title, author and publication date out of the markup, picks a key
nobody has used, writes the hayagriva entry, and archives what it fetched:

```bash
report-maker cite clients/acme/2026-08-12-audit https://acme.example/pricing
```

```
  → reports/clients/acme/2026-08-12-audit/sources.yml (acme-pricing)
  → reports/clients/acme/2026-08-12-audit/snapshots/acme-pricing.html (sha256 9f2c1a3b8d04…, 84,210 bytes)

  Cite it with: @acme-pricing
```

That last line is the point. A tool that adds an entry and leaves you to go and
look up what it called it has moved the clerical work rather than removed it.

The archive is three files per source:

```
reports/clients/acme/2026-08-12-audit/snapshots/
  acme-pricing.html     the response body, verbatim — what you show when the claim is disputed
  acme-pricing.txt      the same page as plain text — what `check` reads to verify a quote
  acme-pricing.json     url, fetch time, sha256, status, content type, size
```

Three behaviours are deliberate. Nothing is ever *inferred* into the
bibliography: a page with no author gets no `author:` field, because an invented
attribution is a worse failure than a missing one. Citing the same URL twice does
nothing — it recognises the entry, keeps its key, and returns it, so re-running a
half-finished command is always safe, and running it against an entry that has no
snapshot yet archives the page without touching your wording. And only `http` and
`https` are ever fetched, before and after redirects: a `url:` in a bibliography
is untrusted input, and a vault that could fetch `file:///etc/passwd` would turn
`cite` into an exfiltration tool.

The snapshots live **with the report** rather than in a vault-wide cache. Moving
the folder moves the evidence, `git mv` keeps the history, and a report handed to
somebody else arrives complete. Reports are big folders of small files, and that
is the correct trade: an archive you cannot hand over is an archive that only
works on the machine that made it.

### Does the page still say that?

```bash
report-maker verify clients/acme          # every archived source under that folder
report-maker verify --offline             # report the archive without dialling out
report-maker verify --refresh             # re-archive what changed, keep the old copy
```

Each source comes back in one of six states:

| state | meaning |
|---|---|
| `ok` | the bytes are identical to the day it was archived |
| `changed` | the bytes moved; the report says how far the *text* moved, so a nav-bar tweak reads differently from a rewritten argument |
| `gone` | 404 or 410 — the only state that fails the command |
| `error` | it could not be fetched: a timeout, a bot wall, a 503 |
| `unsnapshotted` | the entry has a URL but nothing was archived |
| `offline` | `--offline`; nothing was fetched |

A page changing is not a failure. The snapshot is still evidence and the report
is still defensible because of it — that is the entire reason the archive exists.
Only a dead link sets a non-zero exit code, and a 403 from a bot wall says
nothing about whether the page still exists.

Nothing ever overwrites a snapshot. `--refresh` writes a new record and moves the
old one aside to `<key>.<fetched-date>.html`, because an archive you are willing
to overwrite is not an archive.

### Quoting

A citation says a page exists. A quote checked against the archive says the page
said *this* — and that is the one rule in the system that can catch a sentence
which merely looks sourced.

```typst
#srcquote(
  "Pricing is unchanged for existing customers through 2027.",
  source: [@acme-pricing],
  locator: "Pricing FAQ, question 4",
  attribution: "Acme Ltd",
)
```

The quote is a Typst **string**, not content, and that restriction is the whole
point: a verified quote is verbatim by definition, so there is no markup to apply
inside it — and a plain string literal is the one form `check` can lift back out
of the file and compare against `snapshots/acme-pricing.txt`. Allow content, and
emphasis and line breaks come with it until there is nothing left to compare.
`claim(…)` remains the helper for a paraphrase, and it takes a `locator:` too.

Comparison is on a normalised form — collapsed whitespace, unified quotes and
dashes, case folded — so retyping a curly apostrophe is not a finding. It is a
substring test and not a similarity score, because a quotation that only nearly
matches *is* a misquotation. When the words are not there, the message prints the
closest passage the archive does contain, which is usually enough to see what
happened.

`locator:` says *where* in the source the words sit — a page, a section, a
heading, a timestamp — so a reader can go and find them without reading the whole
thing. Leaving it out is W004; pointing one at a source that was never archived
is E008.

### Numbers

A number typed into prose is neither cited nor an opinion. It was true of some
export, once; the export moves on and the sentence does not.

```bash
report-maker data add clients/acme/2026-08-12-audit ~/Downloads/prices.csv
```

The file is copied to `reports/<id>/data/prices.csv` and registered in
`sources.yml` as `@data-prices`, carrying its sha256, its shape and its path. The
table reads it at compile time, so there is no second copy of the figures to
drift:

```typst
#srctable(
  "/reports/clients/acme/2026-08-12-audit/data/prices.csv",
  caption: [What the reader should take from these numbers.],
  source: [@data-prices],
)
```

The checksum is what makes it a rule rather than a convention. When the bytes
change under a report that already quotes them, `check` fails with E011 instead
of the report quietly carrying a different number.

Numbers do legitimately get corrected, though, and a rule with no sanctioned way
through it is a rule people route around. `data revise` is that way through, and
it is the only thing in the engine that moves a recorded checksum:

```bash
report-maker data revise clients/acme/2026-08-12-audit data/prices.csv --note "Q3 export"
```

```
  @data-prices  412 rows → 418 rows, +6
  sha256 e5f4126d2b77 → e0769ef6b2a5
  this version is kept as data/prices.2026-08-18.csv

  The bytes this report cited until now were overwritten outside the engine and no
  dated copy of them exists. Recover them from your own backup or version control if
  you need to show what the earlier figure rested on — `data revise` can only keep
  what it is handed.

  Re-read every table and sentence that cites @data-prices — the numbers under them have moved.
```

Read that second paragraph, because it is the honest half. The bytes on disk are
copied to a dated file named for their own modification date, and a dated
revision is never overwritten — two on one day become `-2` and `-3`. It is the
same discipline `verify --refresh` applies to a snapshot, for the same reason: an
archive you are willing to overwrite is not an archive.

But `data revise` can only archive what is in front of it. Edit `prices.csv` in a
spreadsheet and the old numbers are gone before the command ever runs, so what it
keeps is the *incoming* version, and it says so. The version the report cited is
preserved only when something copied it aside **before** the write — which the
app's CSV editor does, and which is why editing a data file in the app leaves two
dated copies where editing it in Excel leaves one. When the outgoing bytes did
survive, the command names that file too:

```
  this version is kept as data/prices.2026-08-18-2.csv
  the version it cited before is kept as data/prices.2026-08-18.csv
```

`data revisions` lists what has been kept, so "the figure changed between the
draft and the final" stops being an argument and becomes a diff of two files —
whenever both halves are actually on disk.

The summary is the receipt. A checksum a tool may quietly refresh is not a
checksum, so moving one is a deliberate act with a visible consequence — and the
last line says the part no tool can do, which is re-read the prose around a table
whose numbers have moved.

Three warnings watch the shape of the columns rather than the shape of the file,
because the failure that actually happens is a table pointed at cells that are
*absent*. A column empty in every row (W007) is a source that failed, arriving as
data; a column carrying one value in every row (W008) is usually a join that
matched nothing; a numeric column that is exactly `0` all the way down (W009) is
what a collector returns when its database is missing. The render side agrees: an
empty cell prints an explicit figure dash with a legend saying what it means,
never a blank and never a zero, because on a branded page blank space in a column
of figures reads as zero and a zero is a fact about the world.

### The colophon

Everything above happens in the vault and stops there. A report whose sources
were all archived and whose quotations were all checked word for word arrives as
the same object as one whose evidence was never fetched at all — the reader
cannot tell, because nothing carries the difference across.

So the facts of the build are gathered immediately before Typst runs and written
to `.build/facts/<report-id>.json`: which Typst compiled it and which engine,
when and from which revision of the vault and whether that revision was clean,
how many sources there are and how many of them are archived and over what dates,
how many quotations were checked verbatim, how much of the prose is cited or
marked as assessment, and which declared data files actually produced rows. A
report that wants them on the page names the file, and the design prints them
after the References:

```typst
colophon: "/.build/facts/clients/acme/2026-08-12-audit.json",
```

The Typst version is there because a document is a rendering, not a value: two
releases can paginate, hyphenate or lay a table out differently, and "it looks
different now" is worth turning into "it was compiled by 0.15.1, this one by
0.13.0". Nothing in that gathering may ever fail a build — a fact that cannot be
read degrades to `unknown` and is named in the gaps, per group, so an unreadable
`sources.yml` does not also blank out the toolchain. An all-`unknown` colophon is
the honest output for a build nobody could describe, and a run that was
incomplete says so on its own face. A method statement typed by hand is a claim
about a build; this is a record of one.

## Reading it back

The PDF is the deliverable. Four commands answer questions it cannot.

**`score`** — how much of a report rests on evidence. Every statement of prose is
`cited`, `assessed` or `unmarked`, per section and per line, and density is the
share that is one of the first two. It never fails a build: a thin draft is a
fact about a draft, not an error. The desktop app paints the same classification
down the edge of the editor.

```
  report                             cited  assessed  unmarked  density  sources
  examples/2026-08-16-brief-example      1         3         1      80%  1/1
  examples/2026-08-16-example            2         4         5      55%  2/2
```

**`diff`** — what changed since a git revision, in the report's own terms:
sources added and withdrawn, claims changed, assessments added, figures moved,
metadata edited. Rewrapping a paragraph churns every line and changes nothing;
rewording a sentence around the same citation is one changed claim. Claims are
matched by similarity, which is why the module exists at all.

**`html`** — `out/<report-id>.html`: the built pages on one tab, and on the
other, one card per source listing the claims that rest on it. Every `@key` is a
button that opens the source together with a window of the archived text around
the quoted passage. The file is genuinely self-contained — page images inlined,
no stylesheet, script or font fetched — so it works from `file://`, from a USB
stick, and from a machine with no network. An evidence bundle that only renders
while a CDN is up is not evidence. Run `pages` first; the export needs them.

**`find`** — one query across four kinds of document: report prose, bibliography
entries, archived pages and diagram labels. The third is the one nothing else can
do, because nothing else kept the pages: a search over `snapshots/*.txt` reaches
into sources that have since been paywalled, rewritten or deleted. Ranking is
tf-idf and nothing more — quotes make a phrase, `-word` excludes, `kind:source`
filters — and the index is one JSON file under `.build/`, rebuilt only for the
files that changed.

## The pad

Every report accumulates material that will never appear in it: the question to
put to the client, the paragraph to rewrite once the pricing page is archived,
the reminder that a scorecard is still a guess. The two bad places for it are a
separate app, where it is immediately divorced from the report it belongs to, and
the report itself, where it either ships to the reader or gets deleted before it
can be useful. So it lives beside the report, in two markdown files:

```
reports/clients/acme/2026-08-12-audit/
  todos.md     a checklist, seeded by `new`
  notes.md     free prose, there once you write one
```

```bash
report-maker todos                                   # the whole vault, open items first
report-maker todos clients/acme --open               # one folder, unfinished only
report-maker todos <report> --add "ask about the 2027 renewal #pricing @2026-09-01"
report-maker todos <report> --check 12                # tick the box on that line
report-maker notes <report>                          # print notes.md
```

**Neither file is ever compiled into the PDF, and the citation rule does not
apply to either of them.** That is the point rather than an oversight: the rule
exists so nothing a *reader* sees can sit between a cited fact and a marked
opinion, and a scratch pad has no reader but the author. A half-formed thought
that had to be cited before it could be written down would simply not get written
down.

One thing leaks the other way, deliberately. A `// TODO:` or `// FIXME:` in a
comment in `main.typ` is the same kind of note, written where the thought
occurred, and a list that omitted it would be a list you could not trust to be
complete — so they are harvested into the same view, read-only. A checkbox in a
Typst comment is prose, not state.

## Brand packs

`brand/brand.json` is the default pack; any `brand/<name>/brand.json` is a named
one a design can ask for with `brand = "<name>"` in its `template.toml`. A pack
is the single source of truth for colour, type and rhythm: from it the engine
generates the Typst tokens, the mermaid theme, the mermaid stylesheet and the
mermaid `classDef`s. Change a colour once and the report, its diagrams and its
figures all move together.

```json
{
  "org": { "name": "Acme Research", "logo": "/brand/assets/logo.svg" },
  "colors": { "accent": "#E31B23", "accent-deep": "#A50F16", "accent-tint": "#FBE9EA" }
}
```

Anything left out falls back to `engine/brand/brand.json`. With no logo, covers
and running heads set the organisation name in display type instead.

```bash
report-maker brand list                          # the packs here, and the built-in default
report-maker brand show mono                     # every resolved key, and where each value came from
report-maker brand new mono --from default       # a pack to edit
report-maker brand set colors.accent '#E31B23' --pack mono
report-maker brand preview --pack mono           # see it
```

`brand show` tags every key with the file it was read from, so an inherited value
is visibly a decision nobody has made yet. `brand set` writes back only the keys
that actually differ from the default, because a pack that restates a default
decides nothing while looking like it does.

`brand preview` renders a **specimen document** into
`.build/brand-preview/<pack>/` — never into `reports/` — and prints the page
PNGs. It exercises the cover, headings, body, KPIs, scorecards, findings,
callouts, claims, a table and a figure, because a colour behaves differently in a
5mm cover band and behind nine-point body text. This is what the desktop app's
brand studio renders while you drag a colour picker.

## The manifest

`out/manifest.json` is how anything downstream reads the vault without walking
it: every report with its id, group, design, brand pack, metadata, artefacts and
staleness, plus the template registry and the list of groups.

## Sending it somewhere

A vault is a folder of files, so the version control you already have is the one
to keep. `report-maker sync` is a thin wrapper over git in the vault's own
repository — enough to commit from the editor, show a branch and a dirty count,
and list the commits that touched one report.

```bash
report-maker sync --state                  # branch, upstream, drift, dirty paths
report-maker sync -m "Acme audit: pricing section"
report-maker sync --push
report-maker sync --log reports/clients/acme/2026-08-12-audit/main.typ
```

It is the only command in the engine that sends anything anywhere, so it is built
as a list of refusals: never `--force`, never without a configured upstream,
never from a detached HEAD, never when the branch is behind, and never staging a
path outside the vault. Every refusal names the command that fixes it — a refusal
that only says no teaches people to reach for `--force` unaided.

## Handing it to an agent

```bash
report-maker -C ~/Documents/Reports mcp
```

An MCP server over stdio, speaking JSON-RPC on stdin and stdout. It exposes the
vault as twelve tools — `list_reports`, `read_report`, `write_report`,
`list_sources`, `add_source`, `check`, `score`, `verify`, `diff`, `build`,
`new_report`, `list_templates` — plus `report://<id>/main.typ` and
`report://<id>/sources.yml` as resources.

`write_report` is the load-bearing one. A model writing a report is the case the
house rule was written for: it produces a page of confident prose in a second,
and every sentence reads like a fact whether or not anything stands behind it. A
build-time gate is a gate an agent can walk past for hours before it closes. So
`write_report` runs `check` itself, compares the findings against the ones that
were there before, and **restores the file byte for byte** when the write
introduced a new error. The agent does not get to decide whether to run the
linter; writing *is* running it. The comparison is against the previous state
rather than against zero, so a report that is already failing can still be edited
towards a fix.

## The desktop app

`app/` is an Electron front end over the same CLI: a vault switcher, a file tree,
a CodeMirror editor with an evidence rail down its edge, panels for sources,
search, notes and designs, a problems drawer, a dashboard, a brand studio and
Chromium's PDF viewer.

```bash
make app                          # dev, with hot reload (installs deps on first run)
make open V=~/Documents/Reports   # build it and open it on that vault
make app-smoke                    # build it, screenshot the window, exit
make app-dist                     # package it (macOS dmg + zip, unsigned)
make install                      # build it and put it in /Applications
```

It opens with **no vault**, like an editor with no document: open a folder, or
create one anywhere on your disk. It holds no logic and no state beyond your
settings and the list of folders you have opened — every question it asks about a
vault is a `report-maker` subprocess, so it can never disagree with the CLI. `⌘S`
saves, `⌘B` saves and builds the report the open file belongs to, then reloads
the PDF. See [app/README.md](app/README.md).

## Requirements

- **Typst** — required. `brew install typst`.
- **Node + a system Chrome** — only for mermaid diagrams. mermaid-cli installs
  itself into `.build/mermaid/` on first use and drives the system browser
  headlessly; it never downloads its own.
- **git** — only for `sync`, `diff` and `template install`.
- **Python 3.11+** — standard library only.

## Tests

```bash
make test                      # engine
make app-build                 # the app: typecheck both projects + bundle
make app-smoke                 # the app: launch it and screenshot the window
```

They cover vault discovery, theme generation, the citation linter, the
bibliography parser, the snapshot archive, drift detection, the semantic diff,
the search index and the git wrapper — the places where a failure would be
silent, and the build would go green with the rule quietly no longer true.
Nothing in the suite touches the network: every fetch goes through an injected
fetcher, so `cite` and `verify` are tested with the plug pulled.

`tests/test_render.py` is the exception to "tests read source files": it renders
page 1 of each example report and compares a perceptual hash against
`tests/golden/<id>.hash`, which is the only way to catch the failure where the
engine is happy, `check` is green, and the cover page has moved two centimetres.
It skips when Typst is not installed. A failure there is not automatically a bug
— look at the page, and if the new design is right, re-record:

```bash
REPORT_MAKER_UPDATE_GOLDEN=1 python3 -m unittest tests.test_render
```
