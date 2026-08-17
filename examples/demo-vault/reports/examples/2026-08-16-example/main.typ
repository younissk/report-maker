// 2026-08-16-example
//
// House rule: something is either cited, or it is an opinion. Facts carry a
// @key into sources.yml. Judgements carry #assess, or sit in assessment[…].
// Tables, figures and images go through srcfig / srcimage / diagram, which
// cannot be written without a source. `report-maker check` enforces all of it.

#import "/.build/design/base/report.typ": report
#import "/.build/design/base/components.typ": *

#show: report.with(
  title: "Example report",
  subtitle: "A one-line description of what this document establishes, and for whom.",
  kind: "Report",
  author: "Youniss Kandah",
  role: "Role",
  date: datetime(year: 2026, month: 8, day: 16),
  subject: "Subject of the report",
  doc-id: "RM-2026-001",
  version: "0.1 — Draft",
  classification: "Internal",
  // Project-absolute — it resolves against the workspace root, not this file.
  sources: "/reports/examples/2026-08-16-example/sources.yml",
  abstract: [
    Two or three sentences stating the question, the method, and the answer.
    This paragraph appears on the cover, so keep it self-contained.
  ],
)

= Executive summary

#lede[
  Open with the answer, not the setup. A reader who stops after this section
  should already know what to do.#assess
]

#callout(kind: "method", title: "How to read this report")[
  Every factual statement carries a numbered citation into
  #link(<references>)[References]. Anything not cited is our judgement, marked
  #assess at the end of the sentence or set in an assessment block. There is no
  third category.
]

#verdict(
  [Short, decisive headline.],
  [Supporting sentence or two, with the main caveat.#assess],
  tone: "caution",
)

#kpis(
  ([12], [Findings raised]),
  ([3], [Rated high or above]),
  ([18], [Sources reviewed]),
)

= Scope and method

A cited fact looks like this @example-page. A measurement we took ourselves is
cited the same way @own-measurement, with the exact command in an appendix so it
can be re-run.

#callout(kind: "method")[
  Explain how the evidence was gathered, and what the limits are.
]

An unmarked judgement is a bug. Mark short ones inline#assess, and give longer
ones their own block:

#assessment[
  A paragraph of interpretation, clearly separated from the evidence it rests
  on, so a reader never has to guess which is which.
]

== Findings

#claim(
  [A verbatim quotation from the subject of the report.],
  attribution: "example.com/page, accessed 1 January 2026",
  source: [@example-page],
)

#finding(
  id: "F-01",
  title: "A concise statement of the problem",
  severity: "high",
  area: "Area",
  confidence: "High",
  evidence: [What was observed, and how @example-page.],
  impact: [Why it matters, in business terms.#assess],
  action: [The specific thing to do about it.#assess],
)

#srcfig(
  scorecard((
    ("First domain", 4, 5, "Why this score."),
    ("Second domain", 2, 5, "Why this score."),
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
//   source: [@example-page],
//   alt: "Description for a reader who cannot see the image",
//   width: 90%,
// )

#metadata(none) <references>
