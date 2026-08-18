/**
 * The evidence rail.
 *
 * A second gutter, drawn *after* the content, one 3px block per line: green
 * where the line is cited, amber where it is an assessment, red where it is
 * neither, and a hairline where there is nothing to cite. It is the citation
 * rule made continuously visible — you can see the shape of a report's evidence
 * without reading a word of it, and a red run down the right-hand side is the
 * thing this whole application exists to make impossible to ignore.
 *
 * The classes come from `score --json`; nothing here classifies anything. They
 * describe the file **as last saved**, so `railStale` drops the rail to 30%
 * while the buffer is dirty rather than pretending it still lines up.
 *
 * Colours are CSS variables (`--rm-rail-*`) set by `cmtheme.ts`, so a syntax
 * theme owns them the way it owns every other colour in the editor.
 */

import { StateEffect, StateField, type Extension } from '@codemirror/state'
import { EditorView, GutterMarker, gutter } from '@codemirror/view'
import type { LineClass } from '../../../shared/types'

export type LineKind = LineClass['kind']

/** What the tooltip says. Naming the class is the whole point of the hover —
 *  a colour you cannot name is decoration. */
const LABEL: Record<LineKind, string> = {
  cited: 'cited — this line carries a @key that resolves to sources.yml',
  assessed: 'assessment — marked #assess, or inside assessment[…]',
  unmarked: 'unmarked — a statement that is neither cited nor assessed',
  neutral: 'markup — nothing here to cite'
}

type Rail = { kinds: ReadonlyMap<number, LineKind>; stale: boolean }

const EMPTY: Rail = { kinds: new Map(), stale: false }

export const setLineClassesEffect = StateEffect.define<readonly LineClass[]>()
export const railStaleEffect = StateEffect.define<boolean>()

const railField = StateField.define<Rail>({
  create: () => EMPTY,
  update(value, tr) {
    let next = value
    for (const effect of tr.effects) {
      if (effect.is(setLineClassesEffect)) {
        const kinds = new Map<number, LineKind>()
        for (const line of effect.value) kinds.set(line.line, line.kind)
        next = { ...next, kinds }
      }
      if (effect.is(railStaleEffect)) next = { ...next, stale: effect.value }
    }
    return next
  }
})

/**
 * The rail's state, and the class that dims it.
 *
 * Kept separate from `evidenceRail()` on purpose: the Editor puts this in its
 * static base and the gutter in the compartment it reconfigures, so turning the
 * rail off in Settings and on again does not lose the classes we were given.
 */
export const railState: Extension = [
  railField,
  EditorView.editorAttributes.compute(
    [railField],
    (state): Record<string, string> =>
      state.field(railField).stale ? { class: 'cm-rail-is-stale' } : {}
  )
]

/** Replace the rail's line classes — the `lines` of one `ReportScore`. */
export function setLineClasses(view: EditorView, lines: readonly LineClass[]): void {
  view.dispatch({ effects: setLineClassesEffect.of(lines) })
}

/** Mark the rail as describing an older version of the buffer. */
export function railStale(view: EditorView, stale: boolean): void {
  if (view.state.field(railField).stale === stale) return
  view.dispatch({ effects: railStaleEffect.of(stale) })
}

class RailMarker extends GutterMarker {
  constructor(readonly kind: LineKind) {
    super()
  }

  eq(other: GutterMarker): boolean {
    return other instanceof RailMarker && other.kind === this.kind
  }

  toDOM(): HTMLElement {
    const block = document.createElement('div')
    block.className = `cm-rail-block cm-rail-${this.kind}`
    block.title = LABEL[this.kind]
    return block
  }
}

// One marker per class, reused: the gutter redraws on every scroll, and four
// objects are cheaper than one per visible line.
const MARKERS: Record<LineKind, RailMarker> = {
  cited: new RailMarker('cited'),
  assessed: new RailMarker('assessed'),
  unmarked: new RailMarker('unmarked'),
  neutral: new RailMarker('neutral')
}

const railTheme = EditorView.baseTheme({
  '.cm-gutter-evidence': {
    minWidth: '9px',
    paddingLeft: '3px',
    paddingRight: '3px'
  },
  '.cm-gutter-evidence .cm-gutterElement': {
    display: 'flex',
    alignItems: 'stretch',
    justifyContent: 'center'
  },
  '.cm-rail-block': {
    width: '3px',
    minHeight: '2px',
    borderRadius: '1px',
    background: 'var(--rm-rail-neutral, var(--border))'
  },
  '.cm-rail-cited': { background: 'var(--rm-rail-cited, var(--primary))' },
  '.cm-rail-assessed': { background: 'var(--rm-rail-assessed, var(--muted-foreground))' },
  '.cm-rail-unmarked': { background: 'var(--rm-rail-unmarked, var(--destructive))' },
  '.cm-rail-neutral': { background: 'var(--rm-rail-neutral, var(--border))' },
  // `&` is the editor root, which is where the stale class lands.
  '&.cm-rail-is-stale .cm-gutter-evidence': { opacity: '0.3' }
})

/**
 * The gutter. Safe to add and remove at will — everything it draws from lives in
 * `railState`.
 */
export function evidenceRail(): Extension {
  return [
    railTheme,
    gutter({
      class: 'cm-gutter-evidence',
      side: 'after',
      lineMarker: (view, block) => {
        const kinds = view.state.field(railField, false)?.kinds
        // Nothing drawn until the engine has spoken: an all-neutral rail would
        // read as "checked, nothing to say" when it means "not scored yet".
        if (!kinds || kinds.size === 0) return null
        const number = view.state.doc.lineAt(block.from).number
        return MARKERS[kinds.get(number) ?? 'neutral']
      },
      lineMarkerChange: (update) =>
        update.startState.field(railField, false) !== update.state.field(railField, false),
      // Holds the gutter's width open while there is nothing to show, so the
      // text does not jump sideways the first time a score arrives.
      initialSpacer: () => MARKERS.neutral
    })
  ]
}
