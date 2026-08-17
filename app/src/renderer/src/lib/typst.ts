/**
 * Just enough Typst for an editor.
 *
 * A full grammar is not the point here — what a writer needs to see at a glance
 * is the citation rule: which spans are `@key` citations, which are `#assess`
 * markers, and which are comments. Those get colour; everything else is text.
 */

import { HighlightStyle, StreamLanguage, syntaxHighlighting } from '@codemirror/language'
import { tags } from '@lezer/highlight'
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
  'false'
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
  languageData: { commentTokens: { line: '//', block: { open: '/*', close: '*/' } } }
})

const highlight = HighlightStyle.define([
  { tag: tags.comment, color: 'var(--muted-foreground)', fontStyle: 'italic' },
  { tag: tags.keyword, color: 'oklch(0.72 0.13 300)' },
  { tag: tags.function(tags.variableName), color: 'oklch(0.78 0.11 230)' },
  { tag: tags.string, color: 'oklch(0.80 0.11 140)' },
  { tag: tags.special(tags.string), color: 'oklch(0.80 0.11 140)' },
  { tag: tags.monospace, color: 'oklch(0.80 0.09 90)' },
  // Citations and labels share the accent, because they are two halves of one
  // mechanism: the marker and the thing it resolves to.
  { tag: tags.link, color: 'oklch(0.75 0.16 25)', fontWeight: '600' },
  { tag: tags.labelName, color: 'oklch(0.75 0.16 25)' },
  { tag: tags.heading, color: 'var(--foreground)', fontWeight: '700' },
  { tag: tags.list, color: 'oklch(0.75 0.16 25)' },
  { tag: tags.number, color: 'oklch(0.82 0.10 60)' }
])

export function typstLanguage(): Extension {
  return [typst, syntaxHighlighting(highlight)]
}

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
  }
})

export function languageFor(path: string): Extension[] {
  if (/\.(yml|yaml)$/i.test(path)) return [yaml, syntaxHighlighting(highlight)]
  if (/\.(typ|mmd)$/i.test(path)) return [typstLanguage()]
  return [syntaxHighlighting(highlight)]
}
