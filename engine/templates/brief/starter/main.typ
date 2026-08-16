// {{slug}}
//
// House rule: something is either cited, or it is an opinion. Facts carry a
// @key into sources.yml. Judgements carry #assess, or sit in assessment[…].
// `report-maker check` enforces it.

#import "{{design}}/report.typ": report
#import "{{design}}/components.typ": *

#show: report.with(
  title: "{{title}}",
  subtitle: "One line on what this brief argues.",
  kind: "{{kind}}",
  author: "{{author}}",
  role: "Role",
  date: {{date}},
  subject: "Subject",
  doc-id: "{{doc_id}}",
  classification: "Internal",
  sources: "{{sources}}",
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
