/**
 * The editor's looks, built from Settings.
 *
 * Two things make this more than a stylesheet.
 *
 * **It has to win.** `lib/typst.ts` ships its own `HighlightStyle`, and
 * `styles.css` dresses `.cm-editor` in the app's tokens. CodeMirror concatenates
 * the classes from every highlighter that matches a token, so a second
 * `HighlightStyle` here would be a coin toss decided by stylesheet order.
 * Instead this module tags tokens with stable class names (`rm-cite`,
 * `rm-comment`, …) and colours them from an `EditorView.theme`, whose selectors
 * are prefixed with the theme's own class — `.ͼ7 .rm-cite` beats a bare
 * `.ͼ4`, and `&.cm-editor` beats a bare `.cm-editor`, every time. The Typst mode
 * underneath keeps working on its own, which is why it can stay untouched.
 *
 * **Each theme carries its own surface.** A syntax theme has to stay legible in
 * either app chrome, so it sets background and foreground rather than inheriting
 * them — except `mono`, which is defined in the app's own tokens and therefore
 * follows the chrome by construction.
 *
 * The rail's four colours are part of a theme too, published as `--rm-rail-*`
 * for `rail.ts` to draw with.
 */

import type { Extension } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { syntaxHighlighting } from '@codemirror/language'
import { tagHighlighter, tags } from '@lezer/highlight'
import type { Settings } from '../../../shared/types'

export type SyntaxTheme = Settings['editor']['syntaxTheme']

/** For the Settings screen, so the list of themes is written down once. */
export const SYNTAX_THEMES: { id: SyntaxTheme; label: string; note: string }[] = [
  { id: 'report-light', label: 'Report light', note: 'the writing surface, on paper' },
  { id: 'report-dark', label: 'Report dark', note: 'the writing surface, at night' },
  { id: 'mono', label: 'Monochrome', note: 'no colour but the rail; follows the app theme' },
  { id: 'solarized', label: 'Solarized', note: 'Ethan Schoonover’s palette' },
  { id: 'high-contrast', label: 'High contrast', note: 'maximum separation, heavier weights' }
]

// ── Token classes ────────────────────────────────────────────────────────────
//
// Every tag `lib/typst.ts` can emit, mapped to a class this file styles.
// Modified tags fall back to their base (`function(variableName)` → `rm-name`
// when `rm-fn` is absent), which is why the specific entries come first.

const TOKENS = tagHighlighter([
  { tag: tags.comment, class: 'rm-comment' },
  { tag: tags.keyword, class: 'rm-keyword' },
  { tag: tags.function(tags.variableName), class: 'rm-fn' },
  { tag: tags.variableName, class: 'rm-name' },
  { tag: tags.propertyName, class: 'rm-key' },
  { tag: tags.special(tags.string), class: 'rm-string2' },
  { tag: tags.string, class: 'rm-string' },
  { tag: tags.monospace, class: 'rm-mono' },
  { tag: tags.link, class: 'rm-cite' },
  { tag: tags.url, class: 'rm-url' },
  { tag: tags.labelName, class: 'rm-label' },
  { tag: tags.heading, class: 'rm-heading' },
  { tag: tags.list, class: 'rm-list' },
  { tag: tags.number, class: 'rm-number' },
  { tag: [tags.atom, tags.bool], class: 'rm-atom' },
  { tag: tags.operator, class: 'rm-operator' },
  { tag: tags.meta, class: 'rm-meta' },
  { tag: tags.invalid, class: 'rm-invalid' }
])

// ── Palettes ─────────────────────────────────────────────────────────────────

type Palette = {
  /** Whether CodeMirror should treat this as a dark theme (it keys some of its
   *  own base styles off it). */
  dark: boolean
  bg: string
  fg: string
  caret: string
  selection: string
  selectionMatch: string
  activeLine: string
  gutterBg: string
  gutterFg: string
  gutterActiveFg: string
  gutterBorder: string
  bracket: string
  token: {
    comment: string
    keyword: string
    fn: string
    name: string
    key: string
    string: string
    string2: string
    mono: string
    cite: string
    url: string
    label: string
    heading: string
    list: string
    number: string
    atom: string
    operator: string
    meta: string
    invalid: string
  }
  rail: { cited: string; assessed: string; unmarked: string; neutral: string }
}

/** The dark surface `lib/typst.ts` was originally coloured for. */
const REPORT_DARK: Palette = {
  dark: true,
  bg: 'oklch(0.16 0.005 285.8)',
  fg: 'oklch(0.93 0.004 286)',
  caret: 'oklch(0.96 0 0)',
  selection: 'oklch(0.52 0.05 258 / 45%)',
  selectionMatch: 'oklch(0.52 0.05 258 / 22%)',
  activeLine: 'oklch(1 0 0 / 4%)',
  gutterBg: 'oklch(0.16 0.005 285.8)',
  gutterFg: 'oklch(0.48 0.010 286)',
  gutterActiveFg: 'oklch(0.86 0.006 286)',
  gutterBorder: 'oklch(1 0 0 / 10%)',
  bracket: 'oklch(0.78 0.11 230 / 35%)',
  token: {
    comment: 'oklch(0.58 0.012 286)',
    keyword: 'oklch(0.72 0.13 300)',
    fn: 'oklch(0.78 0.11 230)',
    name: 'oklch(0.93 0.004 286)',
    key: 'oklch(0.78 0.11 230)',
    string: 'oklch(0.80 0.11 140)',
    string2: 'oklch(0.78 0.10 170)',
    mono: 'oklch(0.80 0.09 90)',
    cite: 'oklch(0.75 0.16 25)',
    url: 'oklch(0.74 0.10 230)',
    label: 'oklch(0.75 0.16 25)',
    heading: 'oklch(0.98 0 0)',
    list: 'oklch(0.75 0.16 25)',
    number: 'oklch(0.82 0.10 60)',
    atom: 'oklch(0.78 0.12 320)',
    operator: 'oklch(0.72 0.05 286)',
    meta: 'oklch(0.66 0.06 286)',
    invalid: 'oklch(0.70 0.19 22)'
  },
  rail: {
    cited: 'oklch(0.72 0.15 150)',
    assessed: 'oklch(0.78 0.14 78)',
    unmarked: 'oklch(0.63 0.20 25)',
    neutral: 'oklch(0.34 0.008 286)'
  }
}

/** The same reading, on paper. */
const REPORT_LIGHT: Palette = {
  dark: false,
  bg: 'oklch(0.995 0 0)',
  fg: 'oklch(0.20 0.006 285.8)',
  caret: 'oklch(0.18 0 0)',
  selection: 'oklch(0.72 0.07 250 / 42%)',
  selectionMatch: 'oklch(0.72 0.07 250 / 20%)',
  activeLine: 'oklch(0 0 0 / 4%)',
  gutterBg: 'oklch(0.995 0 0)',
  gutterFg: 'oklch(0.70 0.010 286)',
  gutterActiveFg: 'oklch(0.30 0.008 286)',
  gutterBorder: 'oklch(0.90 0.004 286)',
  bracket: 'oklch(0.48 0.13 245 / 25%)',
  token: {
    comment: 'oklch(0.60 0.012 286)',
    keyword: 'oklch(0.45 0.17 300)',
    fn: 'oklch(0.47 0.13 245)',
    name: 'oklch(0.20 0.006 285.8)',
    key: 'oklch(0.47 0.13 245)',
    string: 'oklch(0.45 0.12 145)',
    string2: 'oklch(0.45 0.10 175)',
    mono: 'oklch(0.48 0.10 85)',
    cite: 'oklch(0.50 0.19 25)',
    url: 'oklch(0.48 0.12 240)',
    label: 'oklch(0.50 0.19 25)',
    heading: 'oklch(0.14 0.005 286)',
    list: 'oklch(0.50 0.19 25)',
    number: 'oklch(0.48 0.13 62)',
    atom: 'oklch(0.48 0.15 320)',
    operator: 'oklch(0.45 0.03 286)',
    meta: 'oklch(0.52 0.05 286)',
    invalid: 'oklch(0.52 0.22 27)'
  },
  rail: {
    cited: 'oklch(0.58 0.14 150)',
    assessed: 'oklch(0.70 0.14 78)',
    unmarked: 'oklch(0.57 0.21 25)',
    neutral: 'oklch(0.90 0.004 286)'
  }
}

/**
 * Monochrome: the app's own tokens, no syntax colour at all. Citations still
 * stand out, by weight and an underline rather than by hue — which is also the
 * one theme that reads correctly for a colour-blind writer.
 *
 * It is written entirely in CSS variables, so it follows the chrome rather than
 * choosing a side.
 */
function mono(dark: boolean): Palette {
  return {
    dark,
    bg: 'var(--background)',
    fg: 'var(--foreground)',
    caret: 'var(--foreground)',
    selection: 'color-mix(in oklab, var(--ring) 40%, transparent)',
    selectionMatch: 'color-mix(in oklab, var(--ring) 20%, transparent)',
    activeLine: 'color-mix(in oklab, var(--muted) 55%, transparent)',
    gutterBg: 'var(--background)',
    gutterFg: 'var(--muted-foreground)',
    gutterActiveFg: 'var(--foreground)',
    gutterBorder: 'var(--border)',
    bracket: 'color-mix(in oklab, var(--ring) 35%, transparent)',
    token: {
      comment: 'var(--muted-foreground)',
      keyword: 'var(--foreground)',
      fn: 'var(--foreground)',
      name: 'var(--foreground)',
      key: 'var(--foreground)',
      string: 'var(--muted-foreground)',
      string2: 'var(--muted-foreground)',
      mono: 'var(--muted-foreground)',
      cite: 'var(--foreground)',
      url: 'var(--muted-foreground)',
      label: 'var(--foreground)',
      heading: 'var(--foreground)',
      list: 'var(--muted-foreground)',
      number: 'var(--muted-foreground)',
      atom: 'var(--foreground)',
      operator: 'var(--muted-foreground)',
      meta: 'var(--muted-foreground)',
      invalid: 'var(--destructive)'
    },
    // The rail keeps its meaning. It is not syntax — it is the report's
    // evidence, and three indistinguishable greys would be a worse answer than
    // three restrained hues.
    rail: dark
      ? {
          cited: 'oklch(0.70 0.11 150)',
          assessed: 'oklch(0.76 0.10 78)',
          unmarked: 'oklch(0.62 0.15 25)',
          neutral: 'var(--border)'
        }
      : {
          cited: 'oklch(0.58 0.11 150)',
          assessed: 'oklch(0.70 0.11 78)',
          unmarked: 'oklch(0.57 0.16 25)',
          neutral: 'var(--border)'
        }
  }
}

// Solarized is a fixed set of sixteen values with published relative
// luminances; expressing it in anything but its own hexes would stop being
// Solarized. This is the one place in the app where a literal palette is the
// point rather than a shortcut.
const SOL = {
  base03: '#002b36',
  base02: '#073642',
  base01: '#586e75',
  base00: '#657b83',
  base0: '#839496',
  base1: '#93a1a1',
  base2: '#eee8d5',
  base3: '#fdf6e3',
  yellow: '#b58900',
  orange: '#cb4b16',
  red: '#dc322f',
  magenta: '#d33682',
  violet: '#6c71c4',
  blue: '#268bd2',
  cyan: '#2aa198',
  green: '#859900'
}

function solarized(dark: boolean): Palette {
  return {
    dark,
    bg: dark ? SOL.base03 : SOL.base3,
    fg: dark ? SOL.base0 : SOL.base00,
    caret: dark ? SOL.base1 : SOL.base01,
    selection: dark ? 'rgb(7 54 66 / 90%)' : 'rgb(238 232 213 / 90%)',
    selectionMatch: dark ? 'rgb(7 54 66 / 60%)' : 'rgb(238 232 213 / 60%)',
    activeLine: dark ? 'rgb(7 54 66 / 55%)' : 'rgb(238 232 213 / 55%)',
    gutterBg: dark ? SOL.base03 : SOL.base3,
    gutterFg: dark ? SOL.base01 : SOL.base1,
    gutterActiveFg: dark ? SOL.base1 : SOL.base01,
    gutterBorder: dark ? SOL.base02 : SOL.base2,
    bracket: dark ? 'rgb(38 139 210 / 35%)' : 'rgb(38 139 210 / 25%)',
    token: {
      comment: dark ? SOL.base01 : SOL.base1,
      keyword: SOL.green,
      fn: SOL.blue,
      name: dark ? SOL.base0 : SOL.base00,
      key: SOL.blue,
      string: SOL.cyan,
      string2: SOL.violet,
      mono: SOL.yellow,
      cite: SOL.orange,
      url: SOL.blue,
      label: SOL.magenta,
      heading: dark ? SOL.base1 : SOL.base01,
      list: SOL.green,
      number: SOL.magenta,
      atom: SOL.violet,
      operator: SOL.green,
      meta: SOL.violet,
      invalid: SOL.red
    },
    rail: {
      cited: SOL.green,
      assessed: SOL.yellow,
      unmarked: SOL.red,
      neutral: dark ? SOL.base02 : SOL.base2
    }
  }
}

/** Maximum separation: pure black or pure white ground, saturated tokens,
 *  heavier weights everywhere the colour alone might not carry. */
function highContrast(dark: boolean): Palette {
  return dark
    ? {
        dark: true,
        bg: '#000000',
        fg: '#ffffff',
        caret: '#ffffff',
        selection: 'rgb(255 255 255 / 32%)',
        selectionMatch: 'rgb(255 255 255 / 18%)',
        activeLine: 'rgb(255 255 255 / 10%)',
        gutterBg: '#000000',
        gutterFg: '#b8b8b8',
        gutterActiveFg: '#ffffff',
        gutterBorder: '#ffffff',
        bracket: 'rgb(0 224 255 / 45%)',
        token: {
          comment: '#a6a6a6',
          keyword: '#ff8cff',
          fn: '#5ad6ff',
          string: '#63f963',
          string2: '#63f9c8',
          name: '#ffffff',
          key: '#5ad6ff',
          mono: '#ffd23f',
          cite: '#ff6d6d',
          url: '#5ad6ff',
          label: '#ff6d6d',
          heading: '#ffffff',
          list: '#ffd23f',
          number: '#ffd23f',
          atom: '#ff8cff',
          operator: '#ffffff',
          meta: '#a6a6a6',
          invalid: '#ff3b3b'
        },
        rail: {
          cited: '#00e05a',
          assessed: '#ffc400',
          unmarked: '#ff4d4d',
          neutral: '#4a4a4a'
        }
      }
    : {
        dark: false,
        bg: '#ffffff',
        fg: '#000000',
        caret: '#000000',
        selection: 'rgb(0 0 0 / 22%)',
        selectionMatch: 'rgb(0 0 0 / 12%)',
        activeLine: 'rgb(0 0 0 / 7%)',
        gutterBg: '#ffffff',
        gutterFg: '#4a4a4a',
        gutterActiveFg: '#000000',
        gutterBorder: '#000000',
        bracket: 'rgb(0 64 139 / 30%)',
        token: {
          comment: '#4a4a4a',
          keyword: '#8b008b',
          fn: '#00408b',
          string: '#005f00',
          string2: '#005f5f',
          name: '#000000',
          key: '#00408b',
          mono: '#7a4b00',
          cite: '#b00020',
          url: '#00408b',
          label: '#b00020',
          heading: '#000000',
          list: '#7a4b00',
          number: '#7a4b00',
          atom: '#8b008b',
          operator: '#000000',
          meta: '#4a4a4a',
          invalid: '#c00000'
        },
        rail: {
          cited: '#007a2f',
          assessed: '#8a5a00',
          unmarked: '#c00000',
          neutral: '#c8c8c8'
        }
      }
}

/**
 * `dark` is the app chrome's polarity. `report-light` and `report-dark` name
 * their own, so they ignore it; the other three follow it, which is what keeps
 * every theme legible in either chrome.
 */
function paletteFor(theme: SyntaxTheme, dark: boolean): Palette {
  switch (theme) {
    case 'report-light':
      return REPORT_LIGHT
    case 'report-dark':
      return REPORT_DARK
    case 'mono':
      return mono(dark)
    case 'solarized':
      return solarized(dark)
    case 'high-contrast':
      return highContrast(dark)
    default:
      return dark ? REPORT_DARK : REPORT_LIGHT
  }
}

// ── The extension ────────────────────────────────────────────────────────────

/** An empty setting means "whatever the stylesheet already uses" — never a font
 *  this machine may not have. */
function fontStack(family: string): string {
  const chosen = family.trim()
  return chosen ? `${chosen}, var(--font-mono)` : 'var(--font-mono)'
}

/**
 * A theme built from `Settings.editor`, ready to sit in the Editor's
 * reconfigurable compartment.
 *
 * `dark` is the app chrome's current polarity, not a theme choice — see
 * `paletteFor`.
 */
export function editorTheme(settings: Settings, dark: boolean): Extension {
  const editor = settings.editor
  const p = paletteFor(editor.syntaxTheme, dark)
  const font = fontStack(editor.fontFamily)
  const size = `${editor.fontSize}px`
  const height = String(editor.lineHeight)

  const theme = EditorView.theme(
    {
      // `&.cm-editor` rather than `&`: styles.css already styles `.cm-editor`,
      // and at equal specificity the app's own sheet would win, because
      // CodeMirror inserts its styles at the top of <head>.
      '&.cm-editor': {
        backgroundColor: p.bg,
        color: p.fg,
        fontSize: size,
        height: '100%',
        // Published for rail.ts, which draws with them and owns none of them.
        '--rm-rail-cited': p.rail.cited,
        '--rm-rail-assessed': p.rail.assessed,
        '--rm-rail-unmarked': p.rail.unmarked,
        '--rm-rail-neutral': p.rail.neutral
      },
      '&.cm-editor.cm-focused': { outline: 'none' },
      '.cm-scroller': { fontFamily: font, lineHeight: height },
      '.cm-content': {
        fontFamily: font,
        lineHeight: height,
        caretColor: p.caret
      },
      '.cm-cursor, .cm-dropCursor': { borderLeftColor: p.caret, borderLeftWidth: '2px' },
      // styles.css sets the selection with !important, so this has to as well.
      '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, .cm-content ::selection':
        { backgroundColor: `${p.selection} !important` },
      '.cm-selectionMatch': { backgroundColor: p.selectionMatch },
      '.cm-activeLine': { backgroundColor: p.activeLine },
      '.cm-gutters': {
        backgroundColor: p.gutterBg,
        color: p.gutterFg,
        border: 'none',
        fontSize: size
      },
      '.cm-gutters.cm-gutters-before': { borderRight: `1px solid ${p.gutterBorder}` },
      // The rail sits flush against the text; a second rule would read as a
      // border on the document rather than a margin note about it.
      '.cm-gutters.cm-gutters-after': { border: 'none' },
      '.cm-activeLineGutter': { backgroundColor: p.activeLine, color: p.gutterActiveFg },
      '.cm-foldPlaceholder': {
        backgroundColor: p.activeLine,
        color: p.token.comment,
        border: 'none'
      },
      '.cm-matchingBracket, .cm-nonmatchingBracket': {
        backgroundColor: p.bracket,
        outline: 'none'
      },
      '.cm-searchMatch': { backgroundColor: p.selectionMatch },
      '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: p.selection },
      // Lint tooltips and the search panel are app chrome, not document, so they
      // wear the app's tokens whatever the syntax theme is.
      '.cm-tooltip': {
        backgroundColor: 'var(--popover)',
        color: 'var(--popover-foreground)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        fontFamily: 'inherit',
        fontSize: '12px'
      },
      '.cm-tooltip .cm-tooltip-arrow:before': { borderTopColor: 'var(--border)' },
      '.cm-tooltip .cm-tooltip-arrow:after': { borderTopColor: 'var(--popover)' },
      '.cm-panels': {
        backgroundColor: 'var(--popover)',
        color: 'var(--popover-foreground)',
        fontFamily: 'inherit',
        fontSize: '12px'
      },
      '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--border)' },
      '.cm-diagnostic': { padding: '4px 8px', fontFamily: 'inherit' },
      '.cm-diagnostic-error': { borderLeft: `3px solid ${p.token.invalid}` },
      '.cm-diagnostic-warning': { borderLeft: `3px solid ${p.rail.assessed}` },

      // ── tokens ──
      '.rm-comment': { color: p.token.comment, fontStyle: 'italic' },
      '.rm-keyword': { color: p.token.keyword, fontWeight: p.dark ? '500' : '600' },
      '.rm-fn': { color: p.token.fn },
      '.rm-name': { color: p.token.name },
      '.rm-key': { color: p.token.key },
      '.rm-string': { color: p.token.string },
      '.rm-string2': { color: p.token.string2 },
      '.rm-mono': { color: p.token.mono },
      // The one span a reader of a report most wants to find.
      '.rm-cite': {
        color: p.token.cite,
        fontWeight: '600',
        textDecoration: editor.syntaxTheme === 'mono' ? 'underline' : 'none'
      },
      '.rm-label': { color: p.token.label },
      '.rm-url': { color: p.token.url, textDecoration: 'underline' },
      '.rm-heading': { color: p.token.heading, fontWeight: '700' },
      '.rm-list': { color: p.token.list },
      '.rm-number': { color: p.token.number },
      '.rm-atom': { color: p.token.atom },
      '.rm-operator': { color: p.token.operator },
      '.rm-meta': { color: p.token.meta },
      '.rm-invalid': { color: p.token.invalid, textDecoration: 'underline wavy' }
    },
    { dark: p.dark }
  )

  return [theme, syntaxHighlighting(TOKENS)]
}
