# report-maker

A headless engine for evidence-grade reports. Typst sources in; branded PDFs,
page images, rendered diagrams and a JSON manifest out. No server, no viewer, no
prompts — every command reads files and writes files, so the same code runs from
a shell, a Makefile, CI, or an agent.

It enforces one rule: **something is either cited, or it is an opinion.**

```bash
brew install typst          # the only hard dependency
./bin/report-maker doctor   # what is installed, what is missing
make                        # theme, diagrams, PDFs, page images, manifest, check
```

## Layout

```
engine/          the engine — Python, no third-party dependencies
  typst/         the Typst library: report template + components
  brand/         the default brand pack
  templates/     what `new` scaffolds
brand/           this workspace's house style — brand.json + assets
reports/         one folder per report: YYYY-MM-DD-slug/main.typ + sources.yml
out/             PDFs, page PNGs, manifest.json          (generated)
.build/          staged Typst library + generated theme  (generated)
```

`engine/` is self-contained. Point it at any directory holding a
`report-maker.toml` and it works there — `report-maker -C ~/work/audits all`.

## Commands

| command | what it does |
|---|---|
| `report-maker init` | make the current directory a workspace |
| `report-maker new "Title"` | scaffold a report folder (`--with-diagram` for a mermaid example) |
| `report-maker list [--json]` | what reports exist, and whether each is built or stale |
| `report-maker brand` | regenerate the theme, stage the Typst library |
| `report-maker diagrams [slug]` | mermaid `.mmd` → branded `.svg` |
| `report-maker build [slug]` | Typst → PDF |
| `report-maker pages [slug]` | page PNGs plus `pages.json` |
| `report-maker manifest` | `out/manifest.json` |
| `report-maker check [slug]` | enforce the citation rule; non-zero exit on any error |
| `report-maker all` | all of the above, in order |
| `report-maker watch <slug>` | live rebuild while writing |
| `report-maker doctor` | tool availability |
| `report-maker clean` | remove generated output (`--all` also drops mermaid-cli) |

`make` wraps every one of these; `R=<slug>` narrows a target to one report.

## Writing a report

```bash
report-maker new "Company audit — Example Ltd" --kind "Company Audit"
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
| E001 | no `sources:` passed to the template — the report cannot cite anything |
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

## Brand

`brand/brand.json` is the single source of truth for colour, type and rhythm.
From it the engine generates the Typst tokens, the mermaid theme, and the
mermaid stylesheet into `.build/`. Change a colour once and the report, its
diagrams and its figures all move together.

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
`classDef`s from the brand at render time — Typst's SVG renderer honours
mermaid's inline styles over any stylesheet, so the colour has to arrive that
way.

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

They cover theme generation and the citation linter — the two places where a
failure would be silent, and the build would go green with the rule quietly no
longer true.
