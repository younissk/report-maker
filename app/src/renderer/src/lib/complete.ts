/**
 * `@` completion over the open report's own bibliography.
 *
 * The keys come from `sources --json` and nowhere else, so the editor can only
 * ever offer a key that resolves — which is the point. Half the citation rule's
 * error class is a `@key` that names nothing, and the cheapest way not to write
 * one is not to be able to type one.
 *
 * `getSources` is a getter rather than an array because a CodeMirror extension
 * is built once per document while the bibliography changes under it: adding a
 * source must not require tearing the editor down.
 */

import { autocompletion } from '@codemirror/autocomplete'
import type {
  Completion,
  CompletionContext,
  CompletionResult,
  CompletionSource
} from '@codemirror/autocomplete'
import { EditorState, type Extension } from '@codemirror/state'
import type { SourceRow } from '../../../shared/types'

/** The span a citation may occupy after its `@`. Matches the tokeniser in
 *  `lib/typst.ts`, which is what colours the same text. */
const KEY_CHARS = /^[\w.:+-]*$/

function option(row: SourceRow): Completion {
  return {
    label: row.key,
    // The title is what a writer recognises; the key is what they are typing.
    detail: row.title || 'untitled',
    info: row.type,
    type: 'constant',
    // A source with nothing citing it yet is usually the one just added, and
    // usually the one being reached for. Orphans sort first for that one keystroke.
    boost: row.uses === 0 ? 1 : 0
  }
}

/**
 * The completion source itself, for callers that want to place it among others.
 * Fires on `@` and replaces only what follows it, so the marker the syntax
 * highlighter keys on survives the completion.
 */
export function citationCompletion(getSources: () => SourceRow[]): CompletionSource {
  return (context: CompletionContext): CompletionResult | null => {
    const token = context.matchBefore(/@[\w.:+-]*/)
    if (!token) return null

    const options = getSources().map(option)
    if (options.length === 0) return null

    return { from: token.from + 1, options, validFor: KEY_CHARS }
  }
}

/**
 * The same thing, ready to drop into an editor's extension list.
 *
 * Registered as language data rather than as an `override`, because an override
 * would displace every other completion source in the document; this one is an
 * addition. `autocompletion()` is included so the extension stands on its own —
 * `basicSetup` already supplies it, and a second copy merges rather than
 * conflicting.
 *
 * Install it for `.typ` documents only: `sources.yml` is where keys are defined,
 * not where they are cited, and offering completions there would suggest a key
 * on the line that declares it.
 */
export function citationCompletionExtension(getSources: () => SourceRow[]): Extension {
  const source = citationCompletion(getSources)
  return [autocompletion(), EditorState.languageData.of(() => [{ autocomplete: source }])]
}
