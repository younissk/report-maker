# report-maker — repository instructions

A headless report engine over a folder-based vault, plus two front ends over it:
an Electron app and a web version. Reports are Typst, built by `engine/`, which
is pure Python and has no third-party dependencies. See [README.md](README.md)
for the commands, [engine/README.md](engine/README.md) for the internals, and
[web/README.md](web/README.md) for the server.

```
engine/               the engine — Python, standard library only
bin/report-maker      the CLI entry point
app/                  the desktop app (Electron)
web/                  the web version (stdlib HTTP server + React client)
examples/demo-vault/  a sample vault, for development and the app's smoke test
tests/                engine unit tests            web/tests/ is the web suite
```

**This repository is the tool, not a vault.** A vault is any folder holding
`report-maker.toml`, anywhere on the user's disk; the repo holds the engine, the
two front ends, and `examples/demo-vault/` — a sample vault for development and
for the app's smoke test. Never scaffold a vault at the repository root, and
never add report content outside `examples/demo-vault/`. Commands take the vault
with `-C`: `report-maker -C examples/demo-vault all`, or `make <target> V=<vault>`.

Folders are the data model, and there is no index to keep in sync:

- `reports/<any/nesting>/<YYYY-MM-DD-slug>/` — the path is the report id, the
  folders above it are its group, and `out/` mirrors the same shape. Its
  `diagrams/`, `snapshots/` and `data/` folders are part of the report and travel
  with it, as do its optional `todos.md` and `notes.md` — see [The pad](#the-pad).
- `templates/<group…>/<name>/` — a design. Nesting groups it. A vault design
  shadows a built-in of the same id.
- `brand/brand.json` plus `brand/<name>/brand.json` — brand packs. A design
  names the one it uses in its `template.toml`.

## The citation rule

**Something is either cited, or it is an opinion.** There is no third category,
and nothing in a report may sit in between. It applies to body text, tables,
figures, images, captions, callouts, executive summaries and scorecards alike.

| Statement | Requirement |
|---|---|
| A fact about the world | carries a `@key` citation resolving to the report's `sources.yml` |
| A measurement we took | cited like any other source, with the exact command in an appendix so it can be re-run |
| A judgement, rating, forecast, or recommendation | ends with `#assess`, or sits inside `assessment[…]` |
| A verbatim quotation | `srcquote("exact words", source: [@key], locator: …)`. The quote is a Typst *string*, and `check` compares it word for word against `snapshots/<key>.txt` |
| A table or figure | built with `srcfig(…, source: [@key])`; if the content is our judgement, the source reads `none — assessment, not evidence` |
| A number that came from a file | the file goes in `reports/<id>/data/`, registered with `report-maker data add`, and the table reads it with `srctable(path, source: [@data-<name>])` — never retyped into the prose |
| An image | built with `srcimage(path, caption:, source:, alt:)`. Never place a bare `image()` in a report |
| A diagram | authored as mermaid in `diagrams/*.mmd`, rendered by `report-maker diagrams`, placed with `diagram(…)`. Cited like any other figure |

The one exemption is brand chrome: the logo on the cover and in the running
header is page furniture, not evidence, and is not cited.

In practice, when writing or editing a report:

- Start `sources.yml` before the prose. If a claim has no key to point at,
  either go and find the source or downgrade the sentence to an assessment.
  `report-maker cite <report> <url>` does the whole clerical half of that in one
  line — it fetches the page, writes the entry, and prints the key to cite with.
- Quote the audited party verbatim with `srcquote("…", source: [@key],
  locator: …)` when the exact wording carries weight; the words have to be in the
  archived page or `check` fails with E009. `claim(…, attribution:, source:)` is
  for a paraphrase you want set apart. Paraphrase plus citation otherwise.
- Never merge a cited fact and a judgement into one unmarked sentence. Split
  them, or mark the whole sentence `#assess`.
- Severity ratings, scores, likelihood and impact are always assessment. The
  facts they rest on are always cited.
- Absence of evidence is reported as absence ("no pricing on any reviewed page
  @key"), never as a claim about the underlying fact. The `@key` for that is a
  real entry, filed by `report-maker data absence <report> <corpus> <query>`,
  which records what was searched, the exact query and the date so somebody can
  run it again. An absence sentence with no key behind it is an opinion wearing
  the clothes of a finding, and W007 will send you here when a column arrives
  empty.
- Every report passes `sources:` to the template as a project-absolute path:
  `sources: "/reports/<slug>/sources.yml"`. A numbered References section is
  appended automatically, listing every reviewed source whether or not a `@key`
  reached it.

`report-maker check` enforces all of this and exits non-zero on any error. Run it
before calling a report finished; `report-maker all` runs it last. A report may
declare `status: "draft"`, which reports its errors as warnings while it is
unfinished — and `status: "final"`, which is refused (E014) while any error
stands. Never seed a status into a starter, and never move a report to `final` to
make a build green.

**Do not weaken a rule to pass a build.** A finding is a fact about the report,
so the fix is the report. Lowering an error to a warning, deleting a rule's test,
or reaching for `--warn-only` outside a genuine work-in-progress all convert a
true statement about the vault into a false one.

**Evidence lives inside the report folder.** `reports/<id>/snapshots/<key>.html`
is the archived copy of a cited page, `.txt` is its extracted text, and `.json`
carries the sha256 and the moment it was fetched; `reports/<id>/data/` holds the
CSVs its tables read. They sit there rather than in a vault-wide cache so that
moving, zipping or handing over the folder takes the evidence with it. Never
write into `snapshots/` by hand, and never overwrite one: `cite` creates them and
`verify --refresh` rotates the old copy to `<key>.<date>.html` before writing a
new record. An archive you are willing to overwrite is not an archive.

The same holds for the numbers. Never edit a registered CSV and then hand-edit
its `sha256:` in `sources.yml` — that is the one move the checksum exists to
prevent. `report-maker data revise <report> <csv>` is the sanctioned path: it
keeps a dated copy, moves the checksum, and prints what changed so the prose
around the table can be re-read.

## The pad

A report folder also holds two markdown files, and neither is a report:

- `todos.md` — a checklist, seeded by `report-maker new`.
  `report-maker todos [target]` reads them across the vault; `--add`,
  `--check <line>` and `--uncheck <line>` write.
- `notes.md` — free prose, created only when somebody writes one.
  `report-maker notes <target>` prints it.

**Neither is ever compiled into the PDF, and the citation rule does not apply to
either of them.** That is a decision, not an omission: the rule exists so nothing
a *reader* sees can sit between a cited fact and a marked opinion, and a pad has
no reader but the author. A half-formed thought that had to be cited before it
could be written down would not get written down. `check` never opens these
files, and `build` never sees them.

**They are committed with the report**, and `.gitignore` must leave them alone. A
note that does not travel with the folder is exactly the failure this design
avoids: the folder is the unit that gets moved, zipped and handed over, and it
has to arrive complete. If a thought is too private to commit, it does not belong
in the vault at all.

A `// TODO:` or `// FIXME:` in a comment in `main.typ` is harvested into the same
view, read-only — a list that omitted it could not be trusted to be complete, and
a checkbox in a Typst comment is prose, not state.

## Building

`V` names the vault and defaults to `examples/demo-vault`; `R` narrows to one
report or one folder of them.

```bash
make V=<vault>                                 # stage, diagrams, PDFs, pages, manifest, check
make build V=<vault> R=clients/acme            # one folder of reports
make new T="Title" V=<vault> G=clients/acme TPL=brief
make templates V=<vault>                       # the designs available there
make design ID=audits/company FROM=base V=<vault>
make check V=<vault>                           # the citation rule alone (SCORE=1 adds density)
make build V=<vault> KEEP=1                    # build the rest of the vault past a broken report
make test                                      # engine unit tests
make app                                       # the desktop app
make web                                       # the web version: API + Vite dev server
make web-build                                 # build the frontend the API serves
make web-docker                                # the container, via docker compose
make web-test                                  # the web server's own suite
make install                                   # CLI on PATH, app in /Applications (see INSTALL.md)
```

The web suite is a second `unittest` discovery root, not a second runner:
`python3 -m unittest discover -s web/tests`. There is no pytest anywhere in this
repository, and `web/` is standard library only for the same reason `engine/` is.

The evidence commands, and the ones that read a vault back:

```bash
make cite R=<report> URL=https://…             # archive a page, add it to sources.yml
make verify V=<vault>                          # re-fetch the archive, report drift
make verify V=<vault> OFFLINE=1                # …without touching the network
make score V=<vault>                           # cited / assessed / unmarked, per report
make diff R=<report> REV=HEAD~3                # what changed, in claims not lines
make html V=<vault>                            # self-contained bundle (run `make pages` first)
make data V=<vault> R=<report>                 # the CSVs a report registered, and their checksums
make find Q="pricing kind:source" V=<vault>    # search prose, sources, snapshots, diagrams
make todos V=<vault>                           # the pad, across the vault (OPEN=1 for unfinished)
make notes R=<report>                          # that report's notes.md
make brand-preview V=<vault> PACK=mono         # render the brand specimen
make sync V=<vault> M="message" PUSH=1         # commit, and optionally push
make mcp V=<vault>                             # serve the vault to an agent
```

Anything without a `make` target is a direct CLI call: `report-maker sources
<target>`, `report-maker data add|revise|revisions <target> <csv>`,
`report-maker data status <target> <csv>`,
`report-maker data absence <target> <corpus> <query>`, `report-maker index`,
`report-maker brand list|show|new|set`,
`report-maker template install|update|uninstall`, `report-maker diagrams
--prepare <file>`, `report-maker --version`. Every command takes `--json` where
the app needs it.

`RM` is a GNU make built-in (`rm -f`) and must never be used as the variable
holding the CLI — `$(RM) doctor` silently expands to `rm -f doctor`. The Makefile
uses `CLI` and `VAULT`.

## The front ends hold no logic

`app/` (`make app`) and `web/` (`make web`) are front ends over these same
commands. Each shells out to the CLI for everything; the app stores nothing but
the list of vaults you have opened, and the server stores nothing but a session
record and a vault per session. **Never move logic into either one — add it to
`engine/` and let them call it.** That sentence is the whole of it: two front
ends over one engine can never disagree with the CLI or with each other, and the
moment one of them parses a report or evaluates the citation rule itself, there
are two implementations of the rule and one of them is wrong.

Where a front end needs something the engine cannot do, that is a change to
`engine/`, tested in `tests/`, and then a call. Not a workaround in the front
end. The web version writes what it could not close into `web/README.md`'s *What
is not closed* rather than patching around it — that section is a list of
engine-side work, not a list of excuses.

**The engine still has no preview server**, and `web/` is not one. Every engine
command is headless and ends by writing a file; the server serves files the
engine already wrote. To read a built report, open `out/<report-id>.pdf`, or look
at the page PNGs in `out/pages/<report-id>/`, which is also how an agent, a
phone, or an embedded browser that cannot render a PDF should read it.
`report-maker html` writes `out/<report-id>.html` — the same pages plus the
evidence behind every claim, in one self-contained file — but it writes it and
stops. No command in the engine ever launches a browser, and none may start
doing so because a front end would find it convenient.

An agent driving a vault should reach for `report-maker mcp` rather than shelling
out command by command. It exposes the vault over stdio JSON-RPC, and its
`write_report` tool runs `check` on what it just wrote and restores the file byte
for byte if the write introduced a new error — the rule is enforced at the moment
of writing rather than at the next build.

## Conventions

- One folder per report, named `YYYY-MM-DD-kebab-slug`, filed under whatever
  folders make sense, containing `main.typ`, `sources.yml` and `todos.md`.
  Folders starting with `_` or `.` are not built.
- Reports import their design from `/.build/design/<template-id>/report.typ`, and
  reference their own files by project-absolute paths. A relative path in a
  report breaks the moment the folder moves. That import line is also the record
  of which design the report uses — do not rewrite it by hand without moving the
  report to a design that exists.
- A new design is a delta, not a fork: set `extends` in its `template.toml` and
  override only the Typst files that actually differ.
- Design tokens live in a brand pack and nowhere else. Never write a hex code
  into a `.typ` or `.mmd` file: diagrams use the emphasis classes
  `em-accent`, `em-muted`, `em-good`, `em-ghost`, and the engine injects the
  matching `classDef`s from the brand at render time.
- `htmlLabels` must stay `false` in the mermaid config, or Typst renders the
  diagram with no text in it.
- Anything under `.build/` and `out/` is generated. Edit the source, not the
  output.
