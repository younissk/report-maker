// Tables that read their numbers from a file.
//
// A number in a report is a fact about the world, and a fact about the world is
// either cited or it is an opinion. A number typed into the prose is neither: it
// was true of some spreadsheet, once. The spreadsheet moves on, the sentence does
// not, and no reader — and no author, six months later — can tell by looking.
//
// So a table reads its numbers rather than carrying them. `srctable` hands the
// path to Typst's own `csv()`, which opens the file while the document compiles.
// There is no second copy of the figures to drift, because there is no second
// copy. On the engine side that file is registered in `sources.yml` with its
// sha256 and its shape, so it is inventoried in References like any other source
// and `report-maker check` fails the build when the bytes move underneath a
// report that already quotes them.
//
//   #srctable(
//     "/reports/<id>/data/prices.csv",
//     caption: [What the reader should take from these numbers.],
//     source: [@data-prices],
//   )
//
// The path is project-absolute, like every other path in a report: `csv()`
// resolves a leading "/" against the vault root, so the call survives the report
// folder being moved or renamed. The table goes through `srcfig`, so it cannot be
// written without saying where the numbers came from — and for a CSV, the thing
// it came from is the file.
//
// ── Absent is not zero ───────────────────────────────────────────────────────
//
// An empty cell is the most dangerous thing a data file can contain, because on
// a branded page it is indistinguishable from a measurement. Set in a column of
// right-aligned figures, blank space reads as "nothing there" — which the eye
// completes as zero, and a zero is a fact about the world. It is not one. It is
// the absence of a fact, and the two must never look alike.
//
// This is not hypothetical. A report in a sibling repository derived its headline
// from `sig.get("ams_course_count", 0) or 0` — a collector that returned 0 when
// its database was missing — and labelled four of the most crowded categories in
// the corpus "white space, absent". A missing source became a measured zero, the
// zero became a derived label, and the label became the finding. The provenance
// line listed five sources and silently omitted the one that had failed.
//
// So an empty cell renders as an explicit mark: a figure dash (U+2012, exactly
// one digit wide, so it lines up under the numbers it stands among) in the
// brand's faintest ink, plus a legend in the caption saying what it means. Never
// `0`, and never blank. `missing:` lets an author choose a different mark —
// "n/a", "withheld", "not applicable" all say different things — and
// `missing-label:` is the wording the legend explains it with, because a mark
// nobody defines is a convention, not a statement.
//
// This lives in its own file rather than in components.typ because a design is
// staged by globbing "*.typ": dropping a file into a template folder is all it
// takes to add a component, and nothing has to be edited to make it arrive.

#import "theme.typ": colors, fonts, sizes
#import "components.typ": srcfig

// ─────────────────────────────────────────────────────────────────────────────
// Reading the shape of a column

#let _DIGITS = "0123456789"

// Ornament a number can wear without ceasing to be one. A column of "1,240",
// "€18.50" and "(3.1%)" is a column of numbers to a reader, and right-aligning
// it is what lines the decimal points up.
#let _ORNAMENT = (",", " ", "\u{00a0}", "%", "$", "€", "£", "(", ")", "'", "+", "_")

#let _numeric(value) = {
  let s = str(value).trim()
  for token in _ORNAMENT {
    s = s.replace(token, "")
  }
  if s == "" {
    return false
  }
  let seen-digit = false
  for c in s.clusters() {
    if _DIGITS.contains(c) {
      seen-digit = true
    } else if c != "." and c != "-" {
      return false
    }
  }
  seen-digit
}

#let _blank(value) = str(value).trim() == ""

// A column is numeric when every cell that says anything says a number. An empty
// cell is not evidence either way — a half-filled column of figures still reads
// as figures. Note that this runs on the *raw* rows, before any cell is replaced
// by the not-measured mark: the mark is not a number, and a column that lost its
// right alignment because one measurement was missing would be a column whose
// typography depends on how complete the export happened to be.
#let _column-numeric(rows, index) = {
  let seen = false
  for row in rows {
    let value = row.at(index, default: "")
    if _blank(value) {
      continue
    }
    if not _numeric(value) {
      return false
    }
    seen = true
  }
  seen
}

// CSV rows are as long as whatever was written on the line, so a file with a
// trailing comma missing would otherwise shear the table.
#let _pad(row, width) = range(width).map(i => row.at(i, default: ""))

// ─────────────────────────────────────────────────────────────────────────────
// The not-measured mark
//
// A figure dash, which is a digit wide by definition, so it occupies the column
// exactly as a number would rather than shrinking away from it. The colour comes
// from the brand pack like every other colour in the system: faint enough to read
// as "nothing was recorded here", present enough that nobody mistakes the cell
// for one that was simply left blank in the layout.
#let missing-mark = text(fill: colors.ink-faint)[#sym.dash.fig]

// What the legend calls it. "Not measured" is the honest default: it says a
// measurement did not happen, and says nothing at all about what the value would
// have been — which is exactly the distinction an empty cell destroys.
#let _MISSING_LABEL = "not measured"

// ─────────────────────────────────────────────────────────────────────────────
// The table

// `transform` is the escape hatch for the cases where the raw file is not what
// belongs on the page — summing, filtering, reordering, unit conversion. It takes
// the parsed array of rows and returns another one, so whatever it does happens
// in the document, in front of the reader, rather than in a spreadsheet nobody
// can re-run. `max-rows` truncates and says so in the caption; it never truncates
// silently, because a table that quietly drops its tail is a table that lies.
#let srctable(
  path,
  source: none,
  caption: none,
  columns: auto,
  header: true,
  align: auto,
  transform: none,
  max-rows: none,
  delimiter: auto,
  missing: auto,
  missing-label: _MISSING_LABEL,
) = {
  // `.tsv` means tabs — the engine sniffs the dialect when it registers the file,
  // and a table that silently read a tab-separated file as one wide column would
  // be the exact failure this component exists to prevent.
  let sep = if delimiter != auto {
    delimiter
  } else if path.ends-with(".tsv") or path.ends-with(".tab") {
    "\t"
  } else {
    ","
  }
  let rows = csv(path, delimiter: sep)
  if transform != none {
    rows = transform(rows)
  }
  assert(
    rows.len() > 0,
    message: "srctable: " + path + " has no rows — an empty data file cannot carry a table",
  )

  let width = calc.max(..rows.map(row => row.len()))
  let head = if header { _pad(rows.first(), width) } else { () }
  let body = rows.slice(if header { 1 } else { 0 }).map(row => _pad(row, width))

  let total = body.len()
  if max-rows != none and total > max-rows {
    body = body.slice(0, max-rows)
  }

  let numeric = range(width).map(i => _column-numeric(body, i))

  // Numeric columns are as wide as their digits; the words take the slack, so
  // the table fills the measure instead of huddling in the middle of it.
  let widths = if columns != auto {
    columns
  } else {
    range(width).map(i => if numeric.at(i) { auto } else { 1fr })
  }
  let alignment = if align != auto {
    align
  } else {
    range(width).map(i => if numeric.at(i) { right } else { left })
  }

  // The mark stands in for the cell, not beside it: whatever an author passes to
  // `missing:` is what occupies the position, so absence is as visible as the
  // figures around it and can never be read as one of them.
  let mark = if missing == auto { missing-mark } else { missing }

  let cell(value, index) = if _blank(value) {
    text(size: sizes.small)[#mark]
  } else if numeric.at(index) {
    text(font: fonts.mono, size: sizes.small)[#value]
  } else if index == 0 {
    text(size: sizes.small, weight: "medium")[#value]
  } else {
    text(size: sizes.small, fill: colors.ink-soft)[#value]
  }

  // Counted over the padded rows, so a line that ran out of commas early counts
  // its phantom cells too — a row of four values in a five-column file is a fifth
  // measurement that was never taken, whatever the file's punctuation suggests.
  // Counted after `max-rows`, because the legend describes the table on the page
  // rather than the file behind it.
  let absent = body.map(row => row.filter(_blank).len()).sum(default: 0)

  // Header cells stay unstyled: the design already dresses the top row, and two
  // places deciding what a column label looks like is one too many. They are also
  // the one row the not-measured mark does not touch — a blank column label is a
  // column nobody named, which is a different problem from a measurement nobody
  // took, and marking it would claim a reading is missing where none was due.
  //
  // A file with no header row still gets a `table.header`, made of zero-inset
  // empty cells. It sounds like a trick and it is one, but the alternative is
  // worse: the design styles the top row as a column heading by position — small,
  // grey, upper-cased — and a cell cannot opt out of that from the inside. A
  // collapsed header row takes no height, keeps the heavy rule the design draws
  // above it, and leaves the first fact reading as a fact.
  let head-cells = if header {
    head.map(v => [#v])
  } else {
    range(width).map(_ => table.cell(inset: 0pt)[])
  }
  let cells = body.map(row => range(width).map(i => cell(row.at(i), i))).flatten()

  let dropped = total - body.len()
  let note = if dropped > 0 {
    text(fill: colors.ink-faint)[ Showing the first #body.len() of #total rows.]
  }

  // The legend is the accessible half of the mark. A glyph with no stated meaning
  // is a convention, and a convention is something the reader has to already
  // know; spelled out in the caption it becomes a statement the report makes, in
  // the place a reader looks to find out what a table is claiming. It appears
  // only when something is actually absent, so a complete table carries no
  // apology for a problem it does not have.
  let legend = if absent > 0 {
    text(fill: colors.ink-faint)[ #mark = #missing-label: #absent cell#if absent > 1 [s]
      carr#if absent > 1 [y] else [ies] no value in the source file, which is an
      absence and not a zero.]
  }

  srcfig(
    table(
      columns: widths,
      align: alignment,
      table.header(..head-cells),
      ..cells,
    ),
    caption: if caption == none and note == none and legend == none {
      none
    } else {
      [#caption#note#legend]
    },
    source: source,
  )
}
