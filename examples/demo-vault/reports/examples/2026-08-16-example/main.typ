// 2026-08-16-example
//
// House rule: something is either cited, or it is an opinion. Facts carry a
// @key into sources.yml. Judgements carry #assess, or sit in assessment[…].
// Tables, figures and images go through srcfig / srcimage / diagram, which
// cannot be written without a source. `report-maker check` enforces all of it.
//
// This report is a worked example rather than a skeleton: every number in it was
// counted, every count is cited, and the commands that produce them are in
// Appendix A. That is deliberate — a sample report carrying invented figures
// teaches the habit this engine exists to break.

#import "/.build/design/base/report.typ": report
#import "/.build/design/base/components.typ": *
// srctable lives in its own staged file, so a design that wants cited numbers
// asks for them explicitly.
#import "/.build/design/base/data.typ": srctable

#show: report.with(
  title: "What the linter can see",
  subtitle: "The rules report-maker enforces, what each one can establish, and where enforcement stops.",
  kind: "Report",
  author: "Youniss Kandah",
  role: "Engine author",
  date: datetime(year: 2026, month: 8, day: 16),
  subject: "report-maker — the checking layer",
  doc-id: "RM-2026-001",
  version: "1.0",
  classification: "Public",
  // Project-absolute — it resolves against the workspace root, not this file.
  sources: "/reports/examples/2026-08-16-example/sources.yml",
  abstract: [
    report-maker refuses to build a report whose figures, quotations and
    citations are not wired to a source. This document counts what that
    enforcement actually covers, and marks the boundary past which a green build
    stops being an argument about the writing.
  ],
)

= Executive summary

#lede[
  A passing build says the report's evidence is *wired up*. It does not say the
  report is right, and the distance between those two claims is the thing a
  reader has to be told rather than left to assume.#assess
]

#callout(kind: "method", title: "How to read this report")[
  Every factual statement carries a numbered citation into
  #link(<references>)[References]. Anything not cited is our judgement, marked
  #assess at the end of the sentence or set in an assessment block. There is no
  third category.
]

#verdict(
  [Enforced, and worth trusting exactly that far.],
  [Every rule below fails or flags a real defect. None of them reads the
   argument, so a fully-cited report can still reach the wrong conclusion.#assess],
  tone: "caution",
)

#kpis(
  ([24], [Rules enforced]),
  ([13], [Fail the build]),
  ([11], [Reported, not fatal]),
)

= Scope and method

The engine states its own rule in one sentence, and the checking layer exists to
make that sentence true of every document it builds @house-rule. We counted the
codes that layer emits, by reading the modules that emit them: twenty-four in
all, thirteen of which stop a build and eleven of which report and let it
through @rule-inventory. The engine and this vault are one repository, so the
count can be re-taken by anyone holding it @repository.

#callout(kind: "method")[
  The count is of *codes reachable in the source*, not of findings observed in
  any vault. A rule that has never fired is still a rule, and a rule that fires
  twice in one report is still one code. Appendix A gives the command.
]

An unmarked judgement is a bug. Mark short ones inline#assess, and give longer
ones their own block:

#assessment[
  Splitting the codes by subsystem is the more useful cut than splitting them by
  severity. Severity says what happens to the build; subsystem says what kind of
  mistake was being guarded against, and it is the second question a reader of a
  failing build actually has.
]

== Findings

#claim(
  [Something is either cited, or it is an opinion.],
  attribution: "report-maker, CLAUDE.md",
  locator: "The citation rule, opening line",
  source: [@house-rule],
)

#finding(
  id: "F-01",
  title: "Form is checkable; truth is not, and a green build does not distinguish them",
  severity: "medium",
  area: "Checking layer",
  confidence: "High",
  evidence: [Of the twenty-four codes, every one tests the *shape* of the
    evidence — that a figure carries a source, that a key resolves, that a
    quotation appears in the archived page @rule-inventory. The modules the
    count was taken from are public, so the reading can be checked
    independently of us @repository.],
  impact: [A reader who takes a passing build as a verdict on the argument is
    reading it for more than it says.#assess],
  action: [Report the density and the source families alongside the pass, so
    "checked" and "well-evidenced" stay visibly separate claims.#assess],
)

#srctable(
  "/reports/examples/2026-08-16-example/data/rule-coverage.csv",
  caption: [Every code the engine can emit, grouped by what it guards. The
    column totals are the three counted figures on the cover.],
  source: [@data-rule-coverage],
)

#srcfig(
  scorecard((
    ("Form of the evidence", 5, 5, "Figures, images and quotations cannot be written without a source."),
    ("Freshness of the evidence", 4, 5, "Archived pages are re-fetched and diffed; nothing forces the re-check."),
    ("Depth of the evidence", 2, 5, "Single-sourcing is reported, never refused."),
    ("Soundness of the argument", 0, 5, "Out of scope for any linter."),
  )),
  caption: [Assessment scorecard. Scores are our judgement#assess; the facts behind them are cited above.],
  source: [none — assessment, not evidence],
)

#diagram(
  "/reports/examples/2026-08-16-example/diagrams/example-flow.svg",
  caption: [Every statement in a report takes one of these two exits.],
  source: [none — assessment, not evidence],
  alt: "Source material is read, then either cited as fact or marked as opinion, and both reach the report",
  width: 90%,
)

// An image needs a source and alt text, the same as everything else:
//
// #srcimage(
//   "/reports/examples/2026-08-16-example/figure.png",
//   caption: [What the reader should take from it.],
//   source: [@repository],
//   alt: "Description for a reader who cannot see the image",
//   width: 90%,
// )

= Appendix A — how the count was taken

A measurement is cited like any other source, which is only worth something if
the reader can run it again. From the root of the repository:

```bash
# Every code the engine can emit, deduplicated.
grep -rhoE '"(E[0-9]{3}|W[0-9]{3})"' engine/*.py | tr -d '"' | sort -u

# The same, split by the module that owns it.
grep -rn '"E0\|"E1\|"W0\|"W1' engine/check.py engine/data.py engine/score.py
```

The grouping in the table is ours: the codes carry no subsystem field, so each
was filed under the module that emits it and the rule it guards.#assess

#metadata(none) <references>
