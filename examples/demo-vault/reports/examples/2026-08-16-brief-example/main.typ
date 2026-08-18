// 2026-08-16-brief-example
//
// House rule: something is either cited, or it is an opinion. Facts carry a
// @key into sources.yml. Judgements carry #assess, or sit in assessment[…].
// `report-maker check` enforces it.

#import "/.build/design/brief/report.typ": report
#import "/.build/design/brief/components.typ": *

#show: report.with(
  title: "Say what a green build means",
  subtitle: "One decision: what the pass badge should be allowed to claim.",
  kind: "Brief",
  author: "Youniss Kandah",
  role: "Engine author",
  date: datetime(year: 2026, month: 8, day: 16),
  subject: "Reporting the result of report-maker check",
  doc-id: "RM-2026-002",
  classification: "Public",
  sources: "/reports/examples/2026-08-16-brief-example/sources.yml",
  abstract: [
    A passing check should be reported as "the evidence is wired up", never as
    "the report is sound". The two are different claims and only the first is
    tested.
  ],
)

= The situation

The engine's rule is that a statement is either cited or marked as an opinion
@house-rule, and twenty-four coded rules enforce it — thirteen of which stop a
build @rule-inventory. All twenty-four test the form of the evidence: whether a
figure carries a source, whether a key resolves, whether a quoted sentence is
present in the archived page @rule-inventory — which anyone can confirm against
the modules themselves @repository.

= What we think

#assessment[
  "Passed" is therefore a narrow claim, and the narrowness is a feature: it is
  precisely what makes the check worth running on every build. The risk is not
  the rule, it is the summary — a green tick beside a document invites a reader
  to hear a verdict on the argument, which nothing here has looked at.
]

= What to do

+ Report the pass in the engine's own words — evidence wired, nothing about the
  argument — wherever a build result is surfaced to a reader.#assess
+ Print the density and the source families beside it, so a thin report and a
  well-evidenced one do not both read as simply green.#assess

#metadata(none) <references>
