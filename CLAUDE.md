# report-maker — repository instructions

A headless report engine. Reports are Typst, built by `engine/`, which is pure
Python and has no third-party dependencies. See [README.md](README.md) for the
commands and [engine/README.md](engine/README.md) for the internals.

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
make                       # theme, diagrams, PDFs, page images, manifest, check
make R=<slug>              # narrow to one report
make new T="Title"         # scaffold a report
make check                 # the citation rule alone
make test                  # engine unit tests
```

There is no preview server and no HTML viewer, by design — every command is
headless. To read a built report, open `out/<slug>.pdf`, or look at the page
PNGs in `out/pages/<slug>/`, which is also how an agent or an embedded browser
that cannot render a PDF should read it.

## Conventions

- One folder per report, named `YYYY-MM-DD-kebab-slug`, containing `main.typ`
  and `sources.yml`. Folders starting with `_` are not built.
- Reports import the staged library — `/.build/typst/report.typ` — and reference
  their own files by project-absolute paths. A relative path in a report breaks
  the moment the folder moves.
- Design tokens live in `brand/brand.json` and nowhere else. Never write a hex
  code into a `.typ` or `.mmd` file: diagrams use the emphasis classes
  `em-accent`, `em-muted`, `em-good`, `em-ghost`, and the engine injects the
  matching `classDef`s from the brand at render time.
- `htmlLabels` must stay `false` in the mermaid config, or Typst renders the
  diagram with no text in it.
- Anything under `.build/` and `out/` is generated. Edit the source, not the
  output.
