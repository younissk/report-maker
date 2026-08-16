// 2026-08-16-brief-example
//
// House rule: something is either cited, or it is an opinion. Facts carry a
// @key into sources.yml. Judgements carry #assess, or sit in assessment[…].
// `report-maker check` enforces it.

#import "/.build/design/brief/report.typ": report
#import "/.build/design/brief/components.typ": *

#show: report.with(
  title: "Brief example",
  subtitle: "One line on what this brief argues.",
  kind: "Brief",
  author: "Youniss Kandah",
  role: "Role",
  date: datetime(year: 2026, month: 8, day: 16),
  subject: "Subject",
  doc-id: "RM-2026-002",
  classification: "Internal",
  sources: "/reports/examples/2026-08-16-brief-example/sources.yml",
  abstract: [
    The answer in two sentences. A brief has no cover and no contents, so this
    is the first thing anyone reads — make it the conclusion, not the setup.
  ],
)

= The situation

What is true, and how we know @example-page. Keep it to what the decision needs.

= What we think

#assessment[
  The judgement, marked as judgement, kept apart from the evidence above.
]

= What to do

+ The first action.#assess
+ The second.#assess

#metadata(none) <references>
