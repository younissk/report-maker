# report-maker

A headless engine for evidence-grade reports. A folder-based vault in; branded
PDFs, page images, rendered diagrams and a JSON manifest out. No database, no
server, no viewer, no prompts — every command reads files and writes files, so
the same code runs from a shell, a Makefile, CI, or an agent.

It enforces one rule: **something is either cited, or it is an opinion.**

```bash
brew install typst          # the only hard dependency
./bin/report-maker doctor   # what is installed, what is missing
make                        # stage, diagrams, PDFs, page images, manifest, check
```

## The vault

Everything is a folder, and the folder structure *is* the data model. There is
nothing else to keep in sync.

```
report-maker.toml            marks the vault root
reports/                     nests as deep as you like — the path is the id
  clients/acme/2026-08-12-audit/   main.typ · sources.yml · diagrams/*.mmd
  internal/q3/2026-08-01-review/
templates/                   designs — nesting groups them
  audits/company/            template.toml · report.typ · starter/
  audits/quick/
brand/                       brand packs
  brand.json                 the default pack
  assets/
  mono/brand.json            a second pack, named "mono"
out/                         PDFs, page PNGs, manifest.json     (generated)
.build/                      staged designs + themes            (generated)
engine/                      the engine itself — no vault state
```

`out/` mirrors the report tree: `reports/clients/acme/2026-08-12-audit/` builds
to `out/clients/acme/2026-08-12-audit.pdf` and
`out/pages/clients/acme/2026-08-12-audit/`.

The engine holds no state. Point it at any vault: `report-maker -C ~/work/vault all`.

## Designs

A design is a folder under `templates/`. Its id is its path, so nesting is
grouping — `templates/audits/company/` is id `audits/company`, group `audits`.

```bash
report-maker templates                                  # what exists, by group
report-maker template new audits/company --from base    # an editable copy
report-maker template show audits/company               # what it inherits
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

## Commands

| command | what it does |
|---|---|
| `report-maker init` | make the current directory a vault |
| `report-maker new "Title"` | scaffold a report (`--into <folder>`, `--template <id>`, `--with-diagram`) |
| `report-maker list [target]` | reports, grouped by folder; `--json` for the machine-readable form |
| `report-maker templates` | designs, grouped by folder |
| `report-maker template new <id>` | create an editable design (`--from <id>`, `--thin`) |
| `report-maker template show <id>` | what a design is, and what it inherits |
| `report-maker stage` | regenerate every design into `.build/design/` |
| `report-maker diagrams [target]` | mermaid `.mmd` → branded `.svg` |
| `report-maker build [target]` | Typst → PDF |
| `report-maker pages [target]` | page PNGs plus `pages.json` |
| `report-maker manifest` | `out/manifest.json` |
| `report-maker check [target]` | enforce the citation rule; non-zero exit on any error |
| `report-maker all [target]` | all of the above, in order |
| `report-maker watch <target>` | live rebuild while writing |
| `report-maker doctor` | tool availability |
| `report-maker clean` | remove generated output (`--all` also drops mermaid-cli) |

A **target** is a report id (`clients/acme/2026-08-12-audit`), a bare slug when
it is unambiguous, or a folder — `build clients/acme` builds everything filed
under it. `make` wraps every command; `R=<target>` narrows it.

## Writing a report

```bash
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
| A table or figure | built with `srcfig(…, source: [@key])` |
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
```

| code | meaning |
|---|---|
| E001 | no `sources:` passed to the design — the report cannot cite anything |
| E002 / E003 | bare `image(…)` / `figure(…)` instead of `srcimage` / `srcfig` |
| E004 | a figure helper with no `source:` |
| E006 | a `@key` with no entry in `sources.yml` |
| E007 | a `.mmd` with no rendered `.svg` — it would compile to nothing |
| W001 | a source that is never cited (still listed in References) |
| W002 | a bare `table(…)` outside a `srcfig` |
| W003 | an image or diagram with no `alt:` |

Every source in `sources.yml` reaches the References section whether or not a
`@key` points at it, so that section doubles as the inventory of what was
reviewed.

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

Diagrams never name a colour. They reference the emphasis classes `em-accent`,
`em-muted`, `em-good` and `em-ghost`, and the engine injects the matching
`classDef`s from the design's brand pack at render time — Typst's SVG renderer
honours mermaid's inline styles over any stylesheet, so the colour has to arrive
that way.

## The manifest

`out/manifest.json` is how anything downstream reads the vault without walking
it: every report with its id, group, design, brand pack, metadata, artefacts and
staleness, plus the template registry and the list of groups.

## Requirements

- **Typst** — required. `brew install typst`.
- **Node + a system Chrome** — only for mermaid diagrams. mermaid-cli installs
  itself into `.build/mermaid/` on first use and drives the system browser
  headlessly; it never downloads its own.
- **Python 3.11+** — standard library only.

## Tests

```bash
make test
```

They cover vault discovery, theme generation and the citation linter — the
places where a failure would be silent, and the build would go green with the
rule quietly no longer true.
