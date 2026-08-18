// The report template: cover, running chrome, contents, and the appended
// bibliography that makes the citation rule enforceable.
//
//   #import "/.build/design/base/report.typ": report
//   #import "/.build/design/base/components.typ": *
//
//   #show: report.with(
//     title: "Company audit — Example Ltd",
//     subtitle: "Public-evidence assessment of example.com",
//     kind: "Company Audit",
//     author: "Jane Doe",
//     role: "Research & Advisory",
//     date: datetime(year: 2026, month: 8, day: 12),
//     subject: "example.com",
//     doc-id: "RM-AUD-2026-001",
//     sources: "/reports/2026-08-12-example/sources.yml",
//   )
//
// Compile through the engine — `report-maker build` — or by hand with --root
// pointed at the workspace root, since every path above is project-absolute:
//
//   typst compile --root . reports/<slug>/main.typ out/<slug>.pdf

#import "theme.typ": org, colors, fonts, sizes, space, page-margin, defaults, label, hairline, accent-rule, orgmark

// Imported as a module rather than with `*`: the `colophon:` parameter below and
// the `colophon(…)` component share a name, and the module qualifier keeps them
// apart without renaming either. A design that overrides components.typ without
// defining `colophon` simply cannot use that parameter — which fails at compile
// time, in the report that asked for it, naming the missing function.
#import "components.typ"

// ─────────────────────────────────────────────────────────────────────────────
// Cover

#let meta-cell(key, value) = block(width: 100%)[
  #label(key, fill: colors.ink-faint)
  #v(2.5pt, weak: true)
  #text(font: fonts.text, size: 9pt, fill: colors.ink, value)
]

#let cover-page(
  title: none,
  subtitle: none,
  kind: none,
  author: none,
  role: none,
  date: none,
  subject: none,
  doc-id: none,
  version: none,
  classification: none,
  status: none,
  abstract: none,
  org-name: none,
  org-url: none,
) = page(
  margin: 0pt,
  header: none,
  footer: none,
  numbering: none,
)[
  #place(top + left, rect(width: 100%, height: 5mm, fill: colors.accent))
  #place(
    bottom + left,
    rect(width: 100%, height: 15mm, fill: colors.accent, inset: (x: page-margin.x))[
      #set align(horizon)
      #grid(
        columns: (1fr, auto),
        align: (left, right),
        label(org-name, fill: white, size: 7.4pt),
        label(
          if classification != none { classification } else { org-url },
          fill: white.transparentize(15%),
          size: 7.4pt,
        ),
      )
    ],
  )

  #block(width: 100%, height: 100%, inset: (x: page-margin.x, top: 30mm, bottom: 30mm))[
    #orgmark(size: 22pt)

    #v(space.xxl)

    #if kind != none [
      #label(kind, fill: colors.accent, size: 8pt)
      #v(space.sm, weak: true)
    ]

    #block(width: 92%)[
      #text(
        font: fonts.display,
        size: sizes.cover-title,
        weight: "regular",
        fill: colors.ink,
        hyphenate: false,
      )[#par(leading: 0.42em, justify: false, title)]
    ]

    #if subtitle != none [
      #v(space.md)
      #block(width: 82%)[
        #text(
          font: fonts.text,
          size: sizes.cover-sub,
          weight: 300,
          fill: colors.ink-soft,
        )[#par(leading: 0.62em, justify: false, subtitle)]
      ]
    ]

    #v(space.lg)
    #accent-rule(width: 26mm, thickness: 2.5pt)

    #if abstract != none [
      #v(space.lg)
      #block(width: 76%)[
        #text(font: fonts.text, size: 9.2pt, fill: colors.ink-soft)[
          #par(leading: 0.68em, justify: false, abstract)
        ]
      ]
    ]

    #v(1fr)

    #hairline(fill: colors.rule)
    #v(space.md)

    #grid(
      columns: (1fr, 1fr, 1fr),
      column-gutter: 10mm,
      row-gutter: 7mm,
      ..(
        ("Prepared by", if role != none [#author #linebreak() #text(fill: colors.ink-muted, size: 8.2pt, role)] else [#author]),
        ("Date", if type(date) == datetime { date.display("[day] [month repr:long] [year]") } else { date }),
        ("Subject", subject),
        ("Document", doc-id),
        ("Version", version),
        ("Classification", classification),
        // Only present when the report declared one. A document that has not
        // said whether it is finished should not have a cover row asserting it.
        ("Status", if status != none { upper(status) } else { none }),
      )
        .filter(p => p.at(1) != none)
        .map(p => meta-cell(p.at(0), p.at(1)))
    )
  ]
]

// ─────────────────────────────────────────────────────────────────────────────
// Running header / footer

#let running-header(title: none) = context {
  // The header names the section the reader is actually in, not the document.
  let this-page = here().page()
  let sections = query(heading.where(level: 1))
    .filter(h => h.location().page() <= this-page)
  let current = if sections.len() > 0 { sections.last().body } else { none }

  block(width: 100%)[
    #grid(
      columns: (auto, 1fr, auto),
      align: (left + horizon, center, right + horizon),
      column-gutter: 6mm,
      orgmark(width: org.logo-width-header, size: 10pt),
      none,
      text(font: fonts.text, size: sizes.micro, fill: colors.ink-muted, tracking: 0.08em)[
        #upper(if current != none { current } else { title })
      ],
    )
    #v(3.5pt, weak: true)
    #hairline(fill: colors.rule-light)
  ]
}

#let running-footer(classification: none, doc-id: none, org-name: none) = block(width: 100%)[
  #hairline(fill: colors.rule-light)
  #v(3.5pt, weak: true)
  #grid(
    columns: (1fr, auto, 1fr),
    align: (left + horizon, center + horizon, right + horizon),
    text(font: fonts.text, size: sizes.micro, fill: colors.ink-faint, tracking: 0.08em)[
      #upper((classification, doc-id).filter(x => x != none).join(" · "))
    ],
    text(font: fonts.text, size: sizes.micro, fill: colors.ink-faint, tracking: 0.08em)[
      #upper(org-name)
    ],
    context text(font: fonts.text, size: sizes.small, fill: colors.ink-soft)[
      #counter(page).display("1")#text(fill: colors.ink-faint)[ \/ #counter(page).final().first()]
    ],
  )
]

// ─────────────────────────────────────────────────────────────────────────────
// Contents

#let contents-page(depth: 2, title: "Contents") = {
  set page(header: none)

  block(width: 100%)[
    #label("Table of contents", fill: colors.accent, size: 8pt)
    #v(space.sm, weak: true)
    #text(font: fonts.display, size: 26pt, fill: colors.ink, title)
    #v(space.sm)
    #accent-rule(width: 26mm, thickness: 2.5pt)
  ]

  v(space.lg)

  // The global `show link` rule paints links in the accent colour and still
  // wraps the output of any inner rule, so each entry sets its own fill — the
  // innermost `text(fill: …)` is the one that wins.
  let entry(it, indent: 0pt, tone: colors.ink, num-fill: colors.accent, dots: true) = link(
    it.element.location(),
    box(width: 100%, text(fill: tone)[
      #h(indent)
      #if it.prefix() != none [
        #box(width: if indent == 0pt { 9mm } else { 11mm })[
          #text(fill: num-fill, it.prefix())
        ]
      ]
      #it.body()
      #if dots [
        #box(width: 1fr, inset: (x: 5pt), text(fill: colors.rule)[#repeat[.]])
      ] else [
        #box(width: 1fr, inset: (x: 5pt), line(length: 100%, stroke: 0.3pt + colors.rule-light))
      ]
      #text(fill: colors.ink-muted, size: 8.6pt, it.page())
    ]),
  )

  show outline.entry.where(level: 1): it => {
    v(11pt, weak: true)
    set text(font: fonts.display, size: 12pt, weight: "regular")
    entry(it, tone: colors.ink)
  }

  show outline.entry.where(level: 2): it => {
    v(4pt, weak: true)
    set text(font: fonts.text, size: 8.8pt)
    entry(it, indent: 9mm, tone: colors.ink-soft, num-fill: colors.ink-faint)
  }

  show outline.entry.where(level: 3): it => {
    v(2pt, weak: true)
    set text(font: fonts.text, size: 8.2pt)
    entry(it, indent: 18mm, tone: colors.ink-muted, num-fill: colors.ink-faint)
  }

  outline(title: none, depth: depth)

  pagebreak(weak: true)
}

// ─────────────────────────────────────────────────────────────────────────────
// Template

#let report(
  title: "Untitled report",
  subtitle: none,
  kind: defaults.kind,
  author: none,
  role: none,
  date: none,
  subject: none,
  doc-id: none,
  version: defaults.version,
  classification: defaults.classification,
  // How finished the report says it is: "draft", "review" or "final", or none.
  // It is not decoration — `report-maker check` reads this same field out of the
  // source and gates on it, reporting a draft's errors as warnings and refusing
  // (E014) a report that calls itself final while an error stands. The parameter
  // has to exist whether or not a design chooses to print it, because a report
  // that declares one and cannot compile is worse than one that never says.
  status: none,
  abstract: none,
  org-name: org.name,
  org-url: org.url,
  toc: true,
  toc-depth: 2,
  paper: defaults.paper,
  // House rule: everything is cited or marked as opinion. Point this at the
  // report's Hayagriva source file and a numbered References section is
  // appended automatically. `full: true` lists every reviewed source, not only
  // the ones a `@key` happens to reach, so the section doubles as the evidence
  // inventory.
  //
  // Must be a project-absolute path — "/reports/<slug>/sources.yml" — because it
  // resolves relative to this template file, not to the report that passed it.
  sources: none,
  bib-style: defaults.bib-style,
  bib-title: defaults.bib-title,
  // The facts of the build that made this file, printed after References as
  // quiet back matter. Pass the project-absolute path to the JSON `report-maker
  // build` writes for this report:
  //
  //   colophon: "/.build/facts/clients/acme/2026-08-12-audit.json",
  //
  // Default off, so no report that does not ask for one changes at all. What it
  // states — toolchain, vault revision, how much of the evidence was archived,
  // whether every declared data file carried rows — is generated by the run, so
  // unlike a method statement it cannot drift from what the run actually did.
  // Like the brand chrome, it describes the document rather than the world, and
  // is the second and last thing in a report that carries no citation.
  colophon: none,
  body,
) = {
  set document(
    title: title,
    author: if author != none { author } else { org-name },
    date: if type(date) == datetime { date } else { auto },
  )

  // ── Base type
  set text(
    font: fonts.text,
    size: sizes.body,
    fill: colors.ink,
    lang: "en",
    hyphenate: false,
  )
  set par(justify: true, leading: 0.66em, spacing: 0.95em, first-line-indent: 0pt)

  // ── Page
  set page(
    paper: paper,
    margin: (
      top: page-margin.top,
      bottom: page-margin.bottom,
      left: page-margin.x,
      right: page-margin.x,
    ),
    header: running-header(title: title),
    header-ascent: 32%,
    footer: running-footer(
      classification: classification,
      doc-id: doc-id,
      org-name: org-name,
    ),
    footer-descent: 28%,
  )

  // ── Headings
  set heading(numbering: (..n) => {
    if n.pos().len() <= 3 { numbering("1.1", ..n.pos()) }
  })

  show heading: set text(font: fonts.display, weight: "regular", fill: colors.ink)
  show heading: set block(above: 1.5em, below: 0.85em)

  show heading.where(level: 1): it => {
    pagebreak(weak: true)
    block(width: 100%, below: 1.1em)[
      #text(size: sizes.h1)[
        #if it.numbering != none [
          #text(fill: colors.accent)[#counter(heading).display(it.numbering)#h(7pt)]
        ]
        #it.body
      ]
      #v(space.sm, weak: true)
      #hairline(fill: colors.rule)
    ]
  }

  show heading.where(level: 2): it => block(width: 100%)[
    #text(size: sizes.h2)[
      #if it.numbering != none [
        #text(fill: colors.accent)[#counter(heading).display(it.numbering)#h(6pt)]
      ]
      #it.body
    ]
  ]

  show heading.where(level: 3): it => block(width: 100%)[
    #text(font: fonts.text, size: sizes.h3, weight: "medium", fill: colors.ink, it.body)
  ]

  show heading.where(level: 4): it => block(width: 100%)[
    #label(it.body, fill: colors.ink-soft, size: 8pt)
  ]

  // ── Inline elements
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

  set quote(block: true)
  show quote: it => block(
    width: 100%,
    inset: (left: 10pt, y: 4pt),
    stroke: (left: 2pt + colors.accent),
    text(font: fonts.display, size: 10.5pt, style: "italic", fill: colors.ink-soft, it.body),
  )

  set list(marker: (
    text(fill: colors.accent)[▪],
    text(fill: colors.ink-faint)[–],
    text(fill: colors.ink-faint)[·],
  ), indent: 6pt, spacing: 0.72em)
  set enum(indent: 6pt, spacing: 0.72em, numbering: n => text(fill: colors.accent, weight: "medium")[#n.])

  // ── Tables & figures
  set table(
    stroke: (x, y) => (
      top: if y == 0 { 0.9pt + colors.ink } else if y == 1 { 0.5pt + colors.rule } else { 0.3pt + colors.rule-light },
      bottom: 0pt,
      left: 0pt,
      right: 0pt,
    ),
    inset: (x: 6pt, y: 6pt),
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
  set table.hline(stroke: 0.3pt + colors.rule-light)
  // Justification in narrow cells opens rivers; ragged right reads better.
  show table: set par(justify: false)

  set figure(gap: 9pt)
  show figure.caption: it => text(
    font: fonts.text,
    size: sizes.micro,
    fill: colors.ink-muted,
  )[
    #text(fill: colors.accent, weight: "medium")[#it.supplement #context it.counter.display(it.numbering)]
    #h(4pt) #it.body
  ]
  show figure: set block(above: 1.4em, below: 1.4em)

  set footnote.entry(separator: line(length: 30%, stroke: 0.4pt + colors.rule))
  show footnote.entry: set text(size: sizes.micro, fill: colors.ink-soft)

  // ── Citations
  // Citation markers take the accent colour so a reader can see, at a glance,
  // how much of a paragraph is sourced. They pair with the `#assess` marker that
  // labels the opinions.
  show cite: it => text(fill: colors.accent, it)
  set bibliography(style: bib-style)

  // ── Front matter
  cover-page(
    title: title,
    subtitle: subtitle,
    kind: kind,
    author: author,
    role: role,
    date: date,
    subject: subject,
    doc-id: doc-id,
    version: version,
    classification: classification,
    status: status,
    abstract: abstract,
    org-name: org-name,
    org-url: org-url,
  )

  counter(page).update(1)

  if toc { contents-page(depth: toc-depth) }

  body

  if sources != none {
    heading(level: 1, bib-title)
    bibliography(sources, title: none, style: bib-style, full: true)
  }

  // Last, and after the references: the evidence the report rests on comes
  // before the account of the machine that assembled it.
  if colophon != none {
    components.colophon(colophon)
  }
}
