// Report components.
//
// Everything here is built from the brand tokens, so a document assembled from
// these pieces looks like it came from the same house as every other one.
//
// The sourcing components are the load-bearing ones: `srcfig`, `srcimage` and
// `diagram` cannot be written without saying where their content came from, and
// `assess` / `assessment` mark the places where the content is ours. Between
// them there is no third category — see `report-maker check`.

#import "theme.typ": colors, fonts, sizes, space, label, hairline, accent-rule

// ─────────────────────────────────────────────────────────────────────────────
// Callouts

#let _callout-styles = (
  note:       (accent: colors.ink-faint,  wash: colors.surface-alt,   word: "Note"),
  insight:    (accent: colors.accent,     wash: colors.accent-tint,   word: "Insight"),
  risk:       (accent: colors.accent-deep, wash: colors.accent-tint,  word: "Risk"),
  caution:    (accent: colors.accent,     wash: colors.surface-alt,   word: "Caution"),
  strength:   (accent: colors.positive,   wash: colors.positive-tint, word: "Strength"),
  method:     (accent: colors.neutral,    wash: colors.surface-alt,   word: "Method"),
  assessment: (accent: colors.ink,        wash: colors.surface-alt,   word: "Assessment — our judgement, not a cited fact"),
)

#let callout(body, kind: "note", title: none) = {
  let s = _callout-styles.at(kind, default: _callout-styles.note)
  block(
    width: 100%,
    fill: s.wash,
    stroke: (left: 2.5pt + s.accent),
    inset: (left: 11pt, rest: 9pt),
    radius: (top-right: 2pt, bottom-right: 2pt),
    breakable: true,
  )[
    #label(if title != none { title } else { s.word }, fill: s.accent, size: 7.4pt)
    #v(5pt)
    #set text(size: 9.2pt, fill: colors.ink-soft)
    #body
  ]
}

// ─────────────────────────────────────────────────────────────────────────────
// Sourcing

// Sentence-level opinion marker. It mirrors the `[3]` of a numeric citation, so
// a reader scanning a paragraph can see at a glance which clauses are sourced
// and which are ours. Use at the end of the judgement it applies to.
#let assess = super(text(fill: colors.accent, weight: "medium", size: 0.85em)[A])

// Paragraph-level opinion. Anything longer than a sentence or two belongs here
// rather than in a trail of `#assess` markers.
#let assessment(body) = callout(body, kind: "assessment")

// A figure that cannot be created without saying where its content came from.
// `source` is content, so pass citations: `source: [@example-page]`. When the
// content is our own judgement, say so: `source: [none — assessment, not evidence]`.
#let srcfig(body, caption: none, source: none, kind: auto) = figure(
  body,
  kind: kind,
  caption: if source == none {
    caption
  } else {
    [#caption #h(2pt) #text(fill: colors.ink-faint)[Source: #source]]
  },
)

// A branded frame for figure imagery: a thin card with the same left accent rule
// and flat corners that findings and callouts use, so screenshots read as part
// of the document rather than pasted into it. `w` is the frame's width.
#let framed(body, w: 100%) = align(center, block(
  width: w,
  fill: colors.surface,
  stroke: (left: 2.5pt + colors.accent, rest: 0.75pt + colors.rule),
  inset: 6pt,
  radius: (top-right: 2pt, bottom-right: 2pt),
  clip: true,
  body,
))

// An image that cannot be placed without a source. Same contract as `srcfig`,
// with alt text too. Framed in the house style by default; pass `frame: false`
// for line art that already carries its own border.
#let srcimage(path, caption: none, source: none, alt: none, width: 100%, frame: true) = srcfig(
  if frame {
    framed(image(path, width: 100%, alt: alt), w: width)
  } else {
    image(path, width: width, alt: alt)
  },
  caption: caption,
  source: source,
)

// A mermaid diagram, rendered to SVG by `report-maker diagrams` and embedded here.
//
//   #diagram(
//     "/reports/<slug>/diagrams/pipeline.svg",
//     caption: [What the reader should take from it.],
//     source: [@some-key],
//     alt: "Description for a reader who cannot see the diagram",
//   )
//
// Write the `.mmd`, not the `.svg` — the SVG is build output that happens to be
// committed. Colour comes from the generated theme, never from the `.mmd` file.
#let diagram(path, caption: none, source: none, alt: none, width: 100%) = srcimage(
  path,
  caption: caption,
  source: source,
  alt: alt,
  width: width,
  frame: false,
)

// ─────────────────────────────────────────────────────────────────────────────
// Severity

#let _severity-styles = (
  critical: (fill: colors.accent-deep,  text: white,            word: "Critical"),
  high:     (fill: colors.accent,       text: white,            word: "High"),
  medium:   (fill: colors.ink-soft,     text: white,            word: "Medium"),
  low:      (fill: colors.rule,         text: colors.ink,       word: "Low"),
  info:     (fill: colors.surface-alt,  text: colors.ink-muted, word: "Info"),
  positive: (fill: colors.positive,     text: white,            word: "Strength"),
)

#let chip(level) = {
  let s = _severity-styles.at(lower(level), default: _severity-styles.info)
  box(
    fill: s.fill,
    inset: (x: 5pt, y: 2.5pt),
    outset: (y: 0pt),
    radius: 1.5pt,
    text(font: fonts.text, size: 6.8pt, weight: "medium", fill: s.text, tracking: 0.1em)[
      #upper(s.word)
    ],
  )
}

// A single audit finding. `evidence`, `impact` and `action` are content blocks.
// Evidence is cited; impact and action are almost always assessment.
#let finding(
  id: none,
  title: "",
  severity: "medium",
  area: none,
  confidence: none,
  evidence: none,
  impact: none,
  action: none,
) = {
  let s = _severity-styles.at(lower(severity), default: _severity-styles.info)
  block(
    width: 100%,
    stroke: (left: 2.5pt + s.fill, rest: 0.4pt + colors.rule-light),
    inset: (left: 11pt, rest: 10pt),
    radius: (top-right: 2pt, bottom-right: 2pt),
    breakable: false,
    above: 1.1em,
    below: 1.1em,
  )[
    #grid(
      columns: (1fr, auto),
      align: (left + horizon, right + horizon),
      column-gutter: 8pt,
      [
        #if id != none [#text(font: fonts.mono, size: 7.6pt, fill: colors.ink-faint)[#id#h(6pt)]]
        #text(font: fonts.display, size: 11pt, fill: colors.ink, title)
      ],
      chip(severity),
    )
    #if area != none or confidence != none [
      #v(4pt, weak: true)
      #label(
        (area, if confidence != none { "confidence: " + confidence }).filter(x => x != none).join("  ·  "),
        fill: colors.ink-faint,
        size: 6.8pt,
      )
    ]
    #v(7pt, weak: true)
    #set text(size: 9.2pt)
    #let row(key, val) = if val != none {
      grid(
        columns: (17mm, 1fr),
        column-gutter: 6pt,
        row-gutter: 0pt,
        label(key, fill: colors.ink-faint, size: 6.8pt),
        block(inset: (top: -1.5pt), val),
      )
      v(5pt, weak: true)
    }
    #row("Evidence", evidence)
    #row("Impact", impact)
    #row("Action", action)
  ]
}

// ─────────────────────────────────────────────────────────────────────────────
// Numbers

// A row of headline figures. Pass pairs: (value, caption).
#let kpis(..items) = {
  let cells = items.pos()
  block(width: 100%, above: 1.2em, below: 1.2em)[
    #grid(
      columns: cells.len() * (1fr,),
      column-gutter: 7mm,
      ..cells.map(c => block(
        width: 100%,
        inset: (top: 7pt, bottom: 7pt),
        stroke: (top: 1.6pt + colors.accent),
      )[
        #text(font: fonts.display, size: 21pt, fill: colors.ink, c.at(0))
        #v(2.5pt, weak: true)
        #text(font: fonts.text, size: 7.6pt, fill: colors.ink-muted)[#c.at(1)]
      ])
    )
  ]
}

// Horizontal 0–max bar, used in the scorecard.
#let scorebar(value, max: 5, width: 26mm) = {
  let frac = calc.max(0.0, calc.min(1.0, value / max))
  let tone = if frac >= 0.7 { colors.positive } else if frac >= 0.45 { colors.ink-soft } else { colors.accent }
  box(baseline: 1.5pt)[
    #stack(
      dir: ltr,
      rect(width: width * frac, height: 4.5pt, fill: tone, radius: 1pt),
      rect(width: width * (1.0 - frac), height: 4.5pt, fill: colors.rule-light, radius: 1pt),
    )
  ]
}

// rows: array of (domain, score, max, basis). Scores are always assessment —
// wrap the scorecard in srcfig and cite the facts they rest on.
#let scorecard(rows, max: 5) = table(
  columns: (auto, 30mm, 12mm, 1fr),
  align: (left + horizon, left + horizon, right + horizon, left + horizon),
  table.header([Domain], [Rating], [Score], [Basis]),
  ..rows
    .map(r => (
      text(weight: "medium", r.at(0)),
      scorebar(r.at(1), max: max),
      text(font: fonts.mono, size: 8.4pt)[#r.at(1)/#max],
      text(size: 8.8pt, fill: colors.ink-soft, r.at(3)),
    ))
    .flatten()
)

// ─────────────────────────────────────────────────────────────────────────────
// Structure

#let verdict(headline, body, tone: "caution") = {
  let accent = if tone == "positive" { colors.positive } else if tone == "risk" { colors.accent-deep } else { colors.accent }
  block(
    width: 100%,
    fill: colors.surface-alt,
    inset: 13pt,
    radius: 2pt,
    stroke: (left: 3pt + accent),
    breakable: false,
  )[
    #label("Verdict", fill: accent, size: 7.4pt)
    #v(5pt, weak: true)
    #text(font: fonts.display, size: 14pt, fill: colors.ink)[#headline]
    #v(7pt)
    #set text(size: 9.2pt, fill: colors.ink-soft)
    #body
  ]
}

// Definition-style key/value list. rows: array of (key, value)
#let keyvalues(rows, key-width: 34mm) = block(width: 100%)[
  #grid(
    columns: (key-width, 1fr),
    column-gutter: 8pt,
    row-gutter: 7pt,
    ..rows
      .map(r => (
        label(r.at(0), fill: colors.ink-faint, size: 7pt),
        block(inset: (top: -2pt), text(size: 9.2pt, r.at(1))),
      ))
      .flatten()
  )
]

// Section lead-in paragraph, slightly larger and lighter than body text.
#let lede(body) = block(width: 100%, below: 1.2em)[
  #text(size: 10.6pt, weight: 300, fill: colors.ink-soft)[
    #par(leading: 0.72em, body)
  ]
]

// A small grey provenance note — where a figure or quote came from.
#let source(body) = block(width: 100%, above: 0.7em)[
  #text(font: fonts.text, size: sizes.micro, fill: colors.ink-faint)[Source: #body]
]

// Verbatim claim pulled from the subject of the report, kept visually distinct
// from our own analysis so the two are never confused.
#let claim(body, attribution: none, source: none) = block(
  width: 100%,
  inset: (left: 10pt, y: 5pt),
  stroke: (left: 2pt + colors.accent),
  above: 1.1em,
  below: 1.1em,
)[
  #text(font: fonts.display, size: 10.5pt, style: "italic", fill: colors.ink)[#body]
  #if attribution != none or source != none [
    #v(4pt, weak: true)
    #if attribution != none { label(attribution, fill: colors.ink-faint, size: 6.8pt) }
    #if source != none [
      #h(3pt)#text(size: 7.2pt, fill: colors.ink-faint)[#source]
    ]
  ]
]

#let pagebreak-section() = pagebreak(weak: true)
