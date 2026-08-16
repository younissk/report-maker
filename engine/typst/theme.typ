// Design tokens plus the primitives built directly on them.
//
// The token values themselves are generated from brand/brand.json into
// /.build/brand/tokens.typ by `report-maker brand` (and by every build), so a
// colour exists in exactly one place across the Typst template, the mermaid
// theme and the mermaid stylesheet.

#import "/.build/brand/tokens.typ": org, colors, fonts, sizes, space, page-margin, defaults

// Letterspaced small caps, used for labels, eyebrows and running heads.
#let label(body, fill: colors.ink-muted, size: sizes.micro, weight: "medium") = text(
  font: fonts.text,
  size: size,
  weight: weight,
  fill: fill,
  tracking: 0.12em,
  upper(body),
)

#let hairline(fill: colors.rule, thickness: 0.4pt) = line(
  length: 100%,
  stroke: thickness + fill,
)

#let accent-rule(width: 100%, thickness: 2pt) = line(
  length: width,
  stroke: thickness + colors.accent,
)

// The organisation mark: the logo when the brand has one, its name set in
// display type when it does not. Brand chrome is the one thing in a report that
// carries no citation — it is page furniture, not evidence.
#let orgmark(width: org.logo-width, size: 20pt, fill: colors.accent) = if org.logo != none {
  image(org.logo, width: width)
} else {
  text(font: fonts.display, size: size, fill: fill, org.name)
}
