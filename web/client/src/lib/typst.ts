/**
 * Just enough Typst for an editor — the desktop app's mode, carried across.
 *
 * A full grammar is not the point here. What a writer needs to see at a glance
 * is the citation rule: which spans are `@key` citations, which are `#assess`
 * markers, and which are comments. Those get colour; everything else is text.
 *
 * This file is a copy of `app/src/renderer/src/lib/typst.ts` plus the two
 * palettes of `app/src/renderer/src/lib/cmtheme.ts` — the tokeniser is byte for
 * byte the same, because a second Typst mode that drifts from the first is two
 * bugs waiting rather than one. What changed is only how the tokens are
 * *coloured*: the app reads a Settings record and offers five syntax themes, and
 * a browser has no settings screen of ours to read, so the web ships the app's
 * two default palettes and picks between them the way the rest of the page does
 * — `prefers-color-scheme`, overridden by `data-theme` on <html>.
 *
 * Tokens are tagged with stable class names (`rm-cite`, `rm-comment`, …) and
 * coloured from an `EditorView.theme` rather than a `HighlightStyle`, for the
 * reason the app documents: CodeMirror concatenates the classes of every
 * highlighter that matches, so two `HighlightStyle`s make the winner a question
 * of stylesheet order. A theme's selectors are prefixed with its own generated
 * class and win outright.
 */

import { StreamLanguage, syntaxHighlighting } from '@codemirror/language'
import { tagHighlighter, tags } from '@lezer/highlight'
import { EditorView } from '@codemirror/view'
import type { Extension } from '@codemirror/state'

const KEYWORDS = new Set([
  'let',
  'set',
  'show',
  'import',
  'include',
  'if',
  'else',
  'for',
  'while',
  'return',
  'none',
  'auto',
  'true',
  'false',
])

const typst = StreamLanguage.define<{ inBlockComment: boolean }>({
  name: 'typst',
  startState: () => ({ inBlockComment: false }),
  token(stream, state) {
    if (state.inBlockComment) {
      while (!stream.eol()) {
        if (stream.match('*/')) {
          state.inBlockComment = false
          break
        }
        stream.next()
      }
      return 'comment'
    }
    if (stream.eatSpace()) return null

    if (stream.match('/*')) {
      state.inBlockComment = true
      return 'comment'
    }
    if (stream.match('//')) {
      stream.skipToEnd()
      return 'comment'
    }
    if (stream.match('%%')) {
      stream.skipToEnd()
      return 'comment'
    }

    // A citation. The one span a reader of this file most wants to find.
    if (stream.match(/^@[A-Za-z][\w.:+-]*/)) return 'link'

    // `#assess`, `#srcfig(`, `#show:` — a hash starts code in markup.
    if (stream.match(/^#[A-Za-z][\w-]*/)) {
      const word = stream.current().slice(1)
      return KEYWORDS.has(word) ? 'keyword' : 'variableName.function'
    }

    if (stream.match(/^"(?:[^"\\]|\\.)*"?/)) return 'string'
    if (stream.match(/^\$[^$]*\$?/)) return 'string.special'
    if (stream.match(/^`[^`]*`?/)) return 'monospace'
    if (stream.match(/^<[A-Za-z][\w.:-]*>/)) return 'labelName'
    if (stream.match(/^=+\s/)) return 'heading'
    if (stream.match(/^[-+*]\s/)) return 'list'
    if (stream.match(/^\d+(\.\d+)?/)) return 'number'
    if (stream.match(/^[A-Za-z][\w-]*/)) {
      return KEYWORDS.has(stream.current()) ? 'keyword' : null
    }
    stream.next()
    return null
  },
  languageData: { commentTokens: { line: '//', block: { open: '/*', close: '*/' } } },
})

/** YAML is close enough to Typst's comment style to share the stream tokeniser
 *  for the little that sources.yml needs — keys, strings, comments. */
export const yaml = StreamLanguage.define({
  name: 'yaml',
  token(stream) {
    if (stream.sol() && stream.match(/^\s*#.*/)) return 'comment'
    if (stream.eatSpace()) return null
    if (stream.match(/^#.*/)) return 'comment'
    if (stream.match(/^[A-Za-z][\w.:+-]*(?=\s*:)/)) return 'propertyName'
    if (stream.match(/^"(?:[^"\\]|\\.)*"?/)) return 'string'
    if (stream.match(/^https?:\/\/\S+/)) return 'link'
    if (stream.match(/^\d[\d-]*/)) return 'number'
    stream.next()
    return null
  },
})

/** The language for a path. Nothing here decides what a file *is* beyond its
 *  extension; a path the engine never hands us simply gets no mode. */
export function languageFor(path: string): Extension[] {
  if (/\.(yml|yaml)$/i.test(path)) return [yaml]
  if (/\.(typ|mmd)$/i.test(path)) return [typst]
  return []
}

// ── token classes ────────────────────────────────────────────────────────────
//
// Every tag the modes above can emit, mapped to a class the theme styles.
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
  { tag: tags.invalid, class: 'rm-invalid' },
])

// ── palettes ─────────────────────────────────────────────────────────────────

type Palette = {
  dark: boolean
  caret: string
  selection: string
  selectionMatch: string
  activeLine: string
  gutterFg: string
  gutterActiveFg: string
  bracket: string
  token: Record<
    | 'comment'
    | 'keyword'
    | 'fn'
    | 'name'
    | 'key'
    | 'string'
    | 'string2'
    | 'mono'
    | 'cite'
    | 'url'
    | 'label'
    | 'heading'
    | 'list'
    | 'number'
    | 'atom'
    | 'operator'
    | 'meta'
    | 'invalid',
    string
  >
}

/**
 * The app's `report-dark`, minus the surface.
 *
 * The one deliberate difference from the desktop: background and foreground are
 * left to the page's own `--background` / `--foreground`. On a phone the editor
 * is the whole screen, and a syntax theme that painted its own near-black over
 * the app's near-black would show a one-pixel seam at every edge.
 */
const DARK: Palette = {
  dark: true,
  caret: 'oklch(0.96 0 0)',
  selection: 'oklch(0.52 0.05 258 / 45%)',
  selectionMatch: 'oklch(0.52 0.05 258 / 22%)',
  activeLine: 'oklch(1 0 0 / 4%)',
  gutterFg: 'oklch(0.48 0.010 286)',
  gutterActiveFg: 'oklch(0.86 0.006 286)',
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
    invalid: 'oklch(0.70 0.19 22)',
  },
}

/** The same reading, on paper. */
const LIGHT: Palette = {
  dark: false,
  caret: 'oklch(0.18 0 0)',
  selection: 'oklch(0.72 0.07 250 / 42%)',
  selectionMatch: 'oklch(0.72 0.07 250 / 20%)',
  activeLine: 'oklch(0 0 0 / 4%)',
  gutterFg: 'oklch(0.70 0.010 286)',
  gutterActiveFg: 'oklch(0.30 0.008 286)',
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
    invalid: 'oklch(0.52 0.22 27)',
  },
}

/**
 * The editor's looks.
 *
 * Font size is deliberately absent: `styles.css` sets `.cm-editor .cm-content`
 * to 16px below the desktop breakpoint and 13px above it, and it is 16px for a
 * reason that is not taste — under 16px, iOS zooms the viewport the instant the
 * caret lands. Setting a size here would override that in JavaScript, where the
 * media query cannot reach.
 */
export function editorTheme(dark: boolean): Extension {
  const p = dark ? DARK : LIGHT

  const theme = EditorView.theme(
    {
      // `&.cm-editor` rather than `&`: styles.css already styles `.cm-editor`,
      // and at equal specificity that sheet wins, because CodeMirror inserts
      // its own styles at the top of <head>.
      '&.cm-editor': {
        backgroundColor: 'transparent',
        color: 'var(--foreground)',
        height: '100%',
        // Published for the evidence rail in `lib/editor.ts`, which draws with
        // them and owns none of them.
        '--rm-rail-cited': 'var(--rail-cited)',
        '--rm-rail-assessed': 'var(--rail-assessed)',
        '--rm-rail-unmarked': 'var(--rail-unmarked)',
        '--rm-rail-neutral': 'var(--rail-neutral)',
      },
      '&.cm-editor.cm-focused': { outline: 'none' },
      '.cm-scroller': {
        fontFamily: 'var(--font-mono)',
        lineHeight: '1.6',
        // Momentum scrolling, and a scroll that stops at its own edges rather
        // than dragging the page behind it.
        overscrollBehavior: 'contain',
      },
      '.cm-content': {
        fontFamily: 'var(--font-mono)',
        caretColor: p.caret,
        // Room for a thumb to land past the last line without the caret ending
        // up under the accessory bar.
        paddingBottom: '40vh',
      },
      '.cm-cursor, .cm-dropCursor': { borderLeftColor: p.caret, borderLeftWidth: '2px' },
      '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, .cm-content ::selection':
        { backgroundColor: p.selection },
      '.cm-selectionMatch': { backgroundColor: p.selectionMatch },
      '.cm-activeLine': { backgroundColor: p.activeLine },
      '.cm-gutters': {
        backgroundColor: 'transparent',
        color: p.gutterFg,
        border: 'none',
      },
      '.cm-gutters.cm-gutters-before': { borderRight: '1px solid var(--border)' },
      // The rail sits flush against the text; a border there would read as an
      // edge of the document rather than a margin note about it.
      '.cm-gutters.cm-gutters-after': { border: 'none' },
      '.cm-activeLineGutter': { backgroundColor: p.activeLine, color: p.gutterActiveFg },
      '.cm-matchingBracket, .cm-nonmatchingBracket': {
        backgroundColor: p.bracket,
        outline: 'none',
      },

      // Chrome — tooltips, the completion list, lint messages — wears the app's
      // own tokens whatever the syntax colours are, because it is the
      // application talking rather than the document.
      '.cm-tooltip': {
        backgroundColor: 'var(--popover)',
        color: 'var(--popover-foreground)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        boxShadow: '0 8px 24px oklch(0 0 0 / 18%)',
        fontFamily: 'inherit',
        fontSize: '13px',
      },
      '.cm-tooltip .cm-tooltip-arrow:before': { borderTopColor: 'var(--border)' },
      '.cm-tooltip .cm-tooltip-arrow:after': { borderTopColor: 'var(--popover)' },
      '.cm-diagnostic': { padding: '6px 10px', fontFamily: 'inherit' },
      '.cm-diagnostic-error': { borderLeft: `3px solid var(--destructive)` },
      '.cm-diagnostic-warning': { borderLeft: `3px solid var(--rail-assessed)` },

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
      '.rm-cite': { color: p.token.cite, fontWeight: '600' },
      '.rm-label': { color: p.token.label },
      '.rm-url': { color: p.token.url, textDecoration: 'underline' },
      '.rm-heading': { color: p.token.heading, fontWeight: '700' },
      '.rm-list': { color: p.token.list },
      '.rm-number': { color: p.token.number },
      '.rm-atom': { color: p.token.atom },
      '.rm-operator': { color: p.token.operator },
      '.rm-meta': { color: p.token.meta },
      '.rm-invalid': { color: p.token.invalid, textDecoration: 'underline wavy' },
    },
    { dark: p.dark }
  )

  return [theme, syntaxHighlighting(TOKENS)]
}
