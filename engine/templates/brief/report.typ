// The brief: a short document that opens on the argument.
//
// Same components, same tokens, same citation rule as the base design — only the
// front matter differs. A cover page and a table of contents cost two pages, and
// under about six pages they cost more than they give back, so this design
// replaces both with a letterhead at the top of page one.
//
//   #import "/.build/design/brief/report.typ": report
//   #import "/.build/design/brief/components.typ": *

#import "theme.typ": org, colors, fonts, sizes, space, page-margin, defaults, label, hairline, accent-rule, orgmark

// The base design is staged alongside this one, so its running chrome can be
// borrowed rather than duplicated.
#import "/.build/design/base/report.typ": running-header, running-footer

// The colophon is inherited from base rather than copied: a brief that printed a
// different account of its own build than a report would is two answers to one
// question.
#import "components.typ"

#let letterhead(
  title: none,
  subtitle: none,
  kind: none,
  author: none,
  role: none,
  date: none,
  subject: none,
  doc-id: none,
  classification: none,
  status: none,
  abstract: none,
) = {
  block(width: 100%, below: space.lg)[
    #grid(
      columns: (1fr, auto),
      align: (left + horizon, right + horizon),
      orgmark(size: 15pt),
      // A brief has no cover to carry a metadata table, so the status — when the
      // report declares one — rides the eyebrow beside kind and classification.
      label(
        (kind, classification, if status != none { upper(status) })
          .filter(x => x != none)
          .join("  ·  "),
        fill: colors.accent,
        size: 7.4pt,
      ),
    )
    #v(space.sm, weak: true)
    #accent-rule(thickness: 2pt)
  ]

  block(width: 100%)[
    #text(font: fonts.display, size: 22pt, fill: colors.ink, hyphenate: false)[
      #par(leading: 0.45em, justify: false, title)
    ]
    #if subtitle != none [
      #v(space.sm)
      #text(font: fonts.text, size: 10.5pt, weight: 300, fill: colors.ink-soft)[
        #par(leading: 0.6em, justify: false, subtitle)
      ]
    ]
  ]

  v(space.md)

  // The metadata a cover page would have carried, folded into one line.
  let bits = (
    if author != none { if role != none { author + ", " + role } else { author } },
    if type(date) == datetime { date.display("[day] [month repr:long] [year]") } else { date },
    subject,
    doc-id,
  ).filter(x => x != none)

  block(width: 100%)[
    #hairline(fill: colors.rule-light)
    #v(4pt, weak: true)
    #label(bits.join("  ·  "), fill: colors.ink-faint, size: 6.8pt)
    #v(4pt, weak: true)
    #hairline(fill: colors.rule-light)
  ]

  if abstract != none {
    v(space.md)
    block(width: 100%, inset: (left: 10pt), stroke: (left: 2pt + colors.accent))[
      #text(font: fonts.text, size: 9.6pt, fill: colors.ink-soft)[
        #par(leading: 0.66em, justify: false, abstract)
      ]
    ]
  }

  v(space.lg)
}

#let report(
  title: "Untitled brief",
  subtitle: none,
  kind: defaults.kind,
  author: none,
  role: none,
  date: none,
  subject: none,
  doc-id: none,
  version: defaults.version,
  classification: defaults.classification,
  // See base/report.typ: `report-maker check` gates on this field, so every
  // design has to accept it whether or not it prints it.
  status: none,
  abstract: none,
  org-name: org.name,
  org-url: org.url,
  paper: defaults.paper,
  sources: none,
  bib-style: defaults.bib-style,
  bib-title: defaults.bib-title,
  // The facts of the build that made this file — see base/report.typ. A brief
  // overrides report.typ wholesale, so it has to opt in explicitly; the
  // component itself is the inherited one.
  colophon: none,
  body,
) = {
  set document(
    title: title,
    author: if author != none { author } else { org-name },
    date: if type(date) == datetime { date } else { auto },
  )

  set text(
    font: fonts.text,
    size: sizes.body,
    fill: colors.ink,
    lang: "en",
    hyphenate: false,
  )
  set par(justify: true, leading: 0.66em, spacing: 0.95em, first-line-indent: 0pt)

  set page(
    paper: paper,
    margin: (
      top: page-margin.top,
      bottom: page-margin.bottom,
      left: page-margin.x,
      right: page-margin.x,
    ),
    // No running header on page one: the letterhead already says whose it is.
    header: context if here().page() > 1 { running-header(title: title) },
    header-ascent: 32%,
    footer: running-footer(
      classification: classification,
      doc-id: doc-id,
      org-name: org-name,
    ),
    footer-descent: 28%,
  )

  // Sections are numbered but do not take a page each — a brief that page-breaks
  // on every heading is a report wearing a brief's clothes.
  set heading(numbering: (..n) => {
    if n.pos().len() <= 2 { numbering("1.1", ..n.pos()) }
  })

  show heading: set text(font: fonts.display, weight: "regular", fill: colors.ink)
  show heading: set block(above: 1.3em, below: 0.7em)

  show heading.where(level: 1): it => block(width: 100%)[
    #text(size: 13.5pt)[
      #if it.numbering != none [
        #text(fill: colors.accent)[#counter(heading).display(it.numbering)#h(6pt)]
      ]
      #it.body
    ]
    #v(3pt, weak: true)
    #hairline(fill: colors.rule-light)
  ]

  show heading.where(level: 2): it => block(width: 100%)[
    #text(font: fonts.text, size: sizes.h3, weight: "medium", fill: colors.ink, it.body)
  ]

  show link: it => text(fill: colors.accent-bright, it)
  show raw.where(block: false): it => box(
    fill: colors.surface-alt,
    inset: (x: 3pt, y: 1.5pt),
    outset: (y: 2.5pt),
    radius: 1.5pt,
    text(font: fonts.mono, size: 0.88em, fill: colors.ink, it),
  )
  show raw.where(block: true): it => block(
    width: 100%,
    fill: colors.surface-alt,
    inset: 9pt,
    radius: 2pt,
    stroke: (left: 2pt + colors.rule-mid),
    text(font: fonts.mono, size: 7.8pt, it),
  )

  set list(marker: (
    text(fill: colors.accent)[▪],
    text(fill: colors.ink-faint)[–],
  ), indent: 6pt, spacing: 0.66em)
  set enum(indent: 6pt, spacing: 0.66em, numbering: n => text(fill: colors.accent, weight: "medium")[#n.])

  set table(
    stroke: (x, y) => (
      top: if y == 0 { 0.9pt + colors.ink } else if y == 1 { 0.5pt + colors.rule } else { 0.3pt + colors.rule-light },
      bottom: 0pt,
      left: 0pt,
      right: 0pt,
    ),
    inset: (x: 6pt, y: 5pt),
    fill: none,
  )
  show table.cell.where(y: 0): set text(
    font: fonts.text,
    size: sizes.micro,
    weight: "medium",
    fill: colors.ink-muted,
    tracking: 0.08em,
  )
  show table.cell.where(y: 0): it => upper(it)
  show table: set par(justify: false)

  set figure(gap: 8pt)
  show figure.caption: it => text(font: fonts.text, size: sizes.micro, fill: colors.ink-muted)[
    #text(fill: colors.accent, weight: "medium")[#it.supplement #context it.counter.display(it.numbering)]
    #h(4pt) #it.body
  ]

  show cite: it => text(fill: colors.accent, it)
  set bibliography(style: bib-style)

  letterhead(
    title: title,
    subtitle: subtitle,
    kind: kind,
    author: author,
    role: role,
    date: date,
    subject: subject,
    doc-id: doc-id,
    classification: classification,
    status: status,
    abstract: abstract,
  )

  body

  if sources != none {
    heading(level: 1, bib-title)
    bibliography(sources, title: none, style: bib-style, full: true)
  }

  // Last, and after the references: the evidence comes before the account of the
  // machine that assembled it.
  if colophon != none {
    components.colophon(colophon)
  }
}
