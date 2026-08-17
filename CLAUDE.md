# report-maker — repository instructions

A headless report engine over a folder-based vault. Reports are Typst, built by
`engine/`, which is pure Python and has no third-party dependencies. See
[README.md](README.md) for the commands and [engine/README.md](engine/README.md)
for the internals.

Folders are the data model, and there is no index to keep in sync:

- `reports/<any/nesting>/<YYYY-MM-DD-slug>/` — the path is the report id, the
  folders above it are its group, and `out/` mirrors the same shape.
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
| A table or figure | built with `srcfig(…, source: [@key])`; if the content is our judgement, the source reads `none — assessment, not evidence` |
| An image | built with `srcimage(path, caption:, source:, alt:)`. Never place a bare `image()` in a report |
| A diagram | authored as mermaid in `diagrams/*.mmd`, rendered by `report-maker diagrams`, placed with `diagram(…)`. Cited like any other figure |

The one exemption is brand chrome: the logo on the cover and in the running
header is page furniture, not evidence, and is not cited.

In practice, when writing or editing a report:

- Start `sources.yml` before the prose. If a claim has no key to point at,
  either go and find the source or downgrade the sentence to an assessment.
- Quote the audited party verbatim with `claim(…, attribution:, source: [@key])`
  when the exact wording carries weight. Paraphrase plus citation otherwise.
- Never merge a cited fact and a judgement into one unmarked sentence. Split
  them, or mark the whole sentence `#assess`.
- Severity ratings, scores, likelihood and impact are always assessment. The
  facts they rest on are always cited.
- Absence of evidence is reported as absence ("no pricing on any reviewed page
  @key"), never as a claim about the underlying fact.
- Every report passes `sources:` to the template as a project-absolute path:
  `sources: "/reports/<slug>/sources.yml"`. A numbered References section is
  appended automatically, listing every reviewed source whether or not a `@key`
  reached it.

`report-maker check` enforces this and exits non-zero on any error. Run it
before calling a report finished; `report-maker all` runs it last.

## Building

```bash
make                       # stage, diagrams, PDFs, page images, manifest, check
make R=<target>            # narrow to one report, or to a folder of them
make new T="Title" G=clients/acme TPL=brief    # scaffold a report
make templates             # the designs available
make design ID=audits/company FROM=base        # an editable design
make check                 # the citation rule alone
make test                  # engine unit tests
```

The desktop shell in `app/` (`make app`) is a front end over these same commands
— it shells out to the CLI for everything and stores nothing but the list of
vaults you have opened. Never move logic into it; add it to `engine/` and let the
app call it.

The engine itself has no preview server and no HTML viewer, by design — every
command is headless. To read a built report, open `out/<report-id>.pdf`, or look at the
page PNGs in `out/pages/<report-id>/`, which is also how an agent or an embedded
browser that cannot render a PDF should read it.

## Conventions

- One folder per report, named `YYYY-MM-DD-kebab-slug`, filed under whatever
  folders make sense, containing `main.typ` and `sources.yml`. Folders starting
  with `_` or `.` are not built.
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
