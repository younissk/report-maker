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
//
// `locator` says *where* in the source the words sit — a page, a section, a
// timestamp — so a reader can go and find them without reading the whole thing.
// It sets after the citation in the same faint micro type, because it is part
// of the same provenance note and not a second one.
#let claim(body, attribution: none, source: none, locator: none) = block(
  width: 100%,
  inset: (left: 10pt, y: 5pt),
  stroke: (left: 2pt + colors.accent),
  above: 1.1em,
  below: 1.1em,
)[
  #text(font: fonts.display, size: 10.5pt, style: "italic", fill: colors.ink)[#body]
  #if attribution != none or source != none or locator != none [
    #v(4pt, weak: true)
    #if attribution != none { label(attribution, fill: colors.ink-faint, size: 6.8pt) }
    #if source != none [
      #h(3pt)#text(size: 7.2pt, fill: colors.ink-faint)[#source]
    ]
    #if locator != none [
      #h(3pt)#text(size: 7.2pt, fill: colors.ink-faint)[#locator]
    ]
  ]
]

// A quotation that is checked, word for word, against the archived page.
//
// `quote` is a *string*, not content, and that restriction is the whole point.
// A verified quote is verbatim by definition, so there is no markup to apply
// inside it — and a plain string literal is the one form `report-maker check`
// can lift back out of this file and compare against `snapshots/<key>.txt`, the
// copy of the page taken when it was cited. Allow content, and emphasis, line
// breaks and nested calls come with it, until there is nothing left to compare:
// the quotation would look verified without being it. The assertion below says
// the same thing at compile time, so the mistake is caught by whichever of the
// two runs first.
//
//   #srcquote(
//     "The exact words, copied from the page.",
//     source: [@example-page],
//     locator: "Pricing section, paragraph 3",
//     attribution: "Example Ltd",
//   )
//
// Quotation marks are added here rather than typed into the string, so what the
// checker compares is only ever the words the page actually carried.
#let srcquote(quote, source: none, locator: none, attribution: none) = {
  assert(
    type(quote) == str,
    message: "srcquote() takes a string, not content — a verified quote is verbatim, "
      + "so it cannot carry markup. Use claim(…) for a paraphrase.",
  )
  claim(["#quote"], attribution: attribution, source: source, locator: locator)
}

#let pagebreak-section() = pagebreak(weak: true)

// ─────────────────────────────────────────────────────────────────────────────
// Colophon
//
// The facts of the build that produced this file: which typst compiled it, which
// revision of the vault it came from, how much of the evidence behind it was
// actually there when it ran, and whether every data file it declared carried
// any rows. `report-maker build` writes them to .build/facts/<report-id>.json
// immediately before typst runs, and this block reads that file — so it is
// generated by the run rather than typed by a person. A method statement written
// by hand starts drifting from the truth the moment the run changes; this cannot,
// which is the whole reason it exists. An incomplete run says so on its own face.
//
// It is exempt from the citation rule, and a reader will ask why, so: like the
// logo on the cover and the running head, a colophon is page furniture
// *describing this document*, not a claim about the world the document is about.
// Nothing in it is evidence and nothing in it is an opinion — every line is
// mechanically derived from the build, and the build is the thing being
// described. The moment a sentence in here made a claim about the subject of the
// report, it would need a @key like anything else.
//
// A fact that could not be gathered arrives as "unknown" rather than as a
// missing key, and the groups that failed outright are named in `gaps`, which is
// printed too: a colophon that is quiet because there was nothing to say and one
// that is quiet because something broke must not read the same.

#let _colophon-unknown = "unknown"

// A count with the right noun beside it. Colophons are read as prose, and
// "1 sources" is the tell that a document was assembled rather than written.
#let _colophon-count(n, one, many) = str(n) + " " + if n == 1 { one } else { many }

// An ISO instant, trimmed to what a reader wants: the day and the minute.
#let _colophon-stamp(value) = if type(value) == str and value.len() >= 16 {
  value.slice(0, 16).replace("T", " ")
} else if type(value) == str and value != "" {
  value
} else {
  _colophon-unknown
}

#let _colophon-row(key, value) = (
  label(key, fill: colors.ink-faint, size: 6.6pt),
  text(font: fonts.text, size: 7.6pt, fill: colors.ink-muted, value),
)

// `facts` is the project-absolute path to the JSON file, as passed by the report
// through its design's `colophon:` parameter. A dictionary is accepted too, so
// the block can be exercised without a build behind it.
#let colophon(facts, title: "About this build") = {
  let f = if type(facts) == str { json(facts) } else { facts }
  let tool = f.at("toolchain", default: (:))
  let prov = f.at("provenance", default: (:))
  let ev = f.at("evidence", default: (:))
  let inp = f.at("inputs", default: (:))
  let gaps = f.at("gaps", default: ())

  let typst-version = tool.at("typst", default: _colophon-unknown)
  let engine-version = tool.at("engine", default: _colophon-unknown)
  let python-version = tool.at("python", default: _colophon-unknown)

  // Three cases, and they are not two: a repository with no uncommitted changes,
  // one with them, and a folder git has never heard of. Collapsing the third
  // into "clean" would claim something nobody checked.
  let vault = if not prov.at("repo", default: false) {
    "not under version control"
  } else {
    let rev = prov.at("revision", default: _colophon-unknown)
    let branch = prov.at("branch", default: none)
    let dirty = prov.at("dirty", default: none)
    let at = if branch != none { rev + " on " + branch } else { rev }
    let state = if dirty == true {
      ("with uncommitted changes at build time",)
    } else if dirty == false {
      ("clean",)
    } else { () }
    ((at,) + state).join(", ")
  }

  let archived = ev.at("archived", default: 0)
  let window = if ev.at("archived_from", default: "") != "" {
    let from = ev.at("archived_from", default: "")
    let to = ev.at("archived_to", default: "")
    " (" + (if from == to { from } else { from + " – " + to }) + ")"
  } else { "" }

  let sourcing = (
    _colophon-count(ev.at("sources", default: 0), "source", "sources"),
    str(archived) + " archived" + window,
  )
  let unarchived = ev.at("unarchived", default: 0)
  // Absence reported as absence: a web source nobody kept a copy of is the one
  // thing in this list a reader should be able to see without asking.
  let sourcing = if unarchived > 0 {
    sourcing + (str(unarchived) + " with a URL and no archived copy",)
  } else { sourcing }

  let quoted = ev.at("quotations", default: 0)
  let verified = ev.at("quotations_verified", default: 0)

  let rows = (
    _colophon-row("Built", _colophon-stamp(prov.at("built", default: _colophon-unknown))),
    _colophon-row(
      "Typeset with",
      typst-version + " · report-maker " + engine-version + " · python " + python-version,
    ),
    _colophon-row("Vault", vault),
    _colophon-row("Evidence", sourcing.join(" · ")),
  )

  let rows = if quoted > 0 {
    rows + (_colophon-row(
      "Quotations",
      str(verified)
        + " of "
        + _colophon-count(quoted, "quotation", "quotations")
        + " found word for word in the archived copy of the page cited",
    ),)
  } else { rows }

  let rows = rows + (_colophon-row(
    "Density",
    str(int(calc.round(ev.at("density", default: 0.0) * 100)))
      + "% of statements carry a citation or are marked as assessment ("
      + str(ev.at("cited", default: 0))
      + " cited, "
      + str(ev.at("assessed", default: 0))
      + " assessed, "
      + str(ev.at("unmarked", default: 0))
      + " unmarked)",
  ),)

  // The line the whole component is for. A run that declared a data file and got
  // nothing out of it has to say so *here*, in the document built on it, rather
  // than in a log or a sibling file the reader will never open.
  let declared = inp.at("declared", default: 0)
  let empty = inp.at("empty", default: ())
  let rows = if declared > 0 {
    rows + (_colophon-row(
      "Data",
      str(inp.at("with_rows", default: 0))
        + " of "
        + _colophon-count(declared, "declared file", "declared files")
        + " produced rows"
        + (if empty.len() > 0 { " — no rows in " + empty.join(", ") } else { "" }),
    ),)
  } else { rows }

  let rows = if gaps.len() > 0 {
    rows + (_colophon-row(
      "Not determined",
      gaps.join(", ") + " — this account of the build is itself incomplete",
    ),)
  } else { rows }

  block(width: 100%, breakable: true, above: 2em)[
    #hairline(fill: colors.rule)
    #v(space.sm)
    #label(title, fill: colors.ink-faint, size: 7pt)
    #v(space.sm, weak: true)
    #grid(
      columns: (26mm, 1fr),
      column-gutter: 7pt,
      row-gutter: 5pt,
      ..rows.flatten()
    )
    #v(space.sm)
    #text(font: fonts.text, size: sizes.micro, fill: colors.ink-faint)[
      Generated by the build that produced this document, not written by hand.
      It describes the document; it makes no claim about the subject of it, and
      so carries no citation.
    ]
  ]
}
