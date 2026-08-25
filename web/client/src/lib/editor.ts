/**
 * The citation rule, wired into CodeMirror.
 *
 * Four things live here and not one of them decides anything about a vault:
 *
 *   1. **Findings** — `GET /api/check`'s answer projected onto the buffer as
 *      lint diagnostics. Nothing here knows what E006 *means*, only how to draw
 *      it in a margin.
 *   2. **The evidence rail** — `GET /api/score`'s `lines`, one block per line
 *      down the right-hand edge. Nothing here classifies a line.
 *   3. **`@` completion** — the keys of `GET /api/sources/:id`, so the editor
 *      can only ever offer a key that resolves. That is the point: half of the
 *      rule's error class is a `@key` naming nothing, and the cheapest way not
 *      to write one is to be unable to type one.
 *   4. **The soft keyboard** — the measurements a phone needs and a laptop does
 *      not, kept in one place so the components can stay declarative.
 *
 * Both (1) and (2) describe the file **as last saved**. While the buffer is
 * dirty they can point at a line that has moved; the rail dims rather than
 * pretending otherwise, which is honest instead of wrong.
 */

import { useCallback, useEffect, useSyncExternalStore, type RefObject } from 'react'
import { EditorState, StateEffect, StateField, type Extension } from '@codemirror/state'
import { EditorView, GutterMarker, ViewPlugin, gutter } from '@codemirror/view'
import { linter, type Diagnostic, type LintSource } from '@codemirror/lint'
import { autocompletion } from '@codemirror/autocomplete'
import type {
  Completion,
  CompletionContext,
  CompletionResult,
  CompletionSource,
} from '@codemirror/autocomplete'

import type { Finding, LineClass, SourceRow } from '@/lib/api'

// ── findings ─────────────────────────────────────────────────────────────────

/** What the editor has been told about the open file's findings. */
export type Findings = {
  findings: readonly Finding[]
  /** The open file, as the engine names it: vault-relative POSIX. */
  path: string | null
}

export const setFindingsEffect = StateEffect.define<Findings>()

/**
 * The findings the editor currently holds.
 *
 * Deliberately a state field rather than a prop read through a closure: the
 * view outlives any single render, and a linter that closed over one render's
 * findings would keep reporting them for ever.
 */
export const findingState = StateField.define<Findings>({
  create: () => ({ findings: [], path: null }),
  update(value, tr) {
    for (const effect of tr.effects) if (effect.is(setFindingsEffect)) return effect.value
    return value
  },
})

export function setFindings(
  view: EditorView,
  findings: readonly Finding[],
  path: string | null
): void {
  view.dispatch({ effects: setFindingsEffect.of({ findings, path }) })
}

/**
 * One path test for both shapes a caller might hold.
 *
 * `Finding.path` is vault-relative POSIX. A caller that knows the file by a
 * longer path still matches, because `reports/x/main.typ` is the suffix of
 * anything ending in it on a segment boundary.
 */
function samePath(docPath: string | null, findingPath: string): boolean {
  if (!docPath) return false
  const doc = docPath.replace(/\\/g, '/')
  return doc === findingPath || doc.endsWith('/' + findingPath)
}

/** The findings that belong to the file currently in the editor. */
export function findingsFor(state: EditorState): Finding[] {
  const held = state.field(findingState, false)
  if (!held) return []
  return held.findings.filter((finding) => samePath(held.path, finding.path))
}

/**
 * `E006 · @market-size is not defined in sources.yml` — the code first, because
 * it is the thing you look up, and the sentence after it, because it is the
 * thing you act on. The engine's own words, never a paraphrase.
 */
function label(finding: Finding): string {
  return finding.code ? `${finding.code} · ${finding.message}` : finding.message
}

/** Findings → diagnostics, clamped to a document that may have shrunk since the
 *  engine last read it. */
export const lintSource: LintSource = (view) => {
  const doc = view.state.doc
  const seen = new Set<string>()
  const diagnostics: Diagnostic[] = []

  for (const finding of findingsFor(view.state)) {
    const number = Math.min(Math.max(finding.line || 1, 1), doc.lines)
    // Two findings on one line are two diagnostics; the same finding reported
    // twice is not — the engine can repeat a report-level finding per line.
    const fingerprint = `${number}:${finding.code}:${finding.message}`
    if (seen.has(fingerprint)) continue
    seen.add(fingerprint)

    const line = doc.line(number)
    diagnostics.push({
      from: line.from,
      to: line.to,
      severity: finding.level === 'error' ? 'error' : 'warning',
      source: 'report-maker check',
      message: label(finding),
    })
  }
  return diagnostics
}

/**
 * The linter.
 *
 * `delay: 0` because there is nothing to debounce — the expensive part already
 * happened in a subprocess on the server, and this only re-reads a list.
 * `needsRefresh` is what makes a fresh `check` appear without touching the
 * document.
 */
export function reportLinter(): Extension {
  return linter(lintSource, {
    delay: 0,
    needsRefresh: (update) =>
      update.transactions.some((tr) =>
        tr.effects.some((effect) => effect.is(setFindingsEffect))
      ),
  })
}

/**
 * A tap or a click on a gutter marker selects that finding.
 *
 * `pointerdown` on `view.dom`, not `EditorView.domEventHandlers` — those only
 * reach the content, and the gutters are its siblings, so an event on a marker
 * never arrives there. `pointerdown` rather than `mousedown` because a phone
 * has no mouse.
 */
export function findingClicks(onSelect: (finding: Finding) => void): Extension {
  return ViewPlugin.define((view) => {
    const onPointerDown = (event: PointerEvent): void => {
      const target = event.target as HTMLElement | null
      if (!target?.closest('.cm-lint-marker')) return
      const block = view.lineBlockAtHeight(event.clientY - view.documentTop)
      const number = view.state.doc.lineAt(block.from).number
      const hit = findingsFor(view.state).find((finding) => finding.line === number)
      if (hit) onSelect(hit)
    }
    view.dom.addEventListener('pointerdown', onPointerDown)
    return { destroy: () => view.dom.removeEventListener('pointerdown', onPointerDown) }
  })
}

// ── the evidence rail ────────────────────────────────────────────────────────

export type LineKind = LineClass['kind']

/** What the marker says when a pointer rests on it. Naming the class is the
 *  whole point — a colour you cannot name is decoration. The Write pane repeats
 *  these in a legend, because a title attribute is a hover-only affordance and
 *  a phone has no hover. */
export const RAIL_LABEL: Record<LineKind, string> = {
  cited: 'cited — this line carries a @key that resolves to sources.yml',
  assessed: 'assessment — marked #assess, or inside assessment[…]',
  unmarked: 'unmarked — a statement that is neither cited nor assessed',
  neutral: 'markup — nothing here to cite',
}

type Rail = { kinds: ReadonlyMap<number, LineKind>; stale: boolean }

const EMPTY_RAIL: Rail = { kinds: new Map(), stale: false }

export const setLineClassesEffect = StateEffect.define<readonly LineClass[]>()
export const railStaleEffect = StateEffect.define<boolean>()

const railField = StateField.define<Rail>({
  create: () => EMPTY_RAIL,
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
  },
})

/** The rail's state, and the class that dims it. Separate from the gutter so
 *  that hiding the rail cannot lose the classes the server already gave us. */
export const railState: Extension = [
  railField,
  EditorView.editorAttributes.compute([railField], (state): Record<string, string> =>
    state.field(railField).stale ? { class: 'cm-rail-is-stale' } : {}
  ),
]

/** Replace the rail's line classes — the `lines` of one `ReportScore`. */
export function setLineClasses(view: EditorView, lines: readonly LineClass[]): void {
  view.dispatch({ effects: setLineClassesEffect.of(lines) })
}

/** Mark the rail as describing an older version of the buffer. */
export function railStale(view: EditorView, stale: boolean): void {
  if (view.state.field(railField, false)?.stale === stale) return
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
    block.title = RAIL_LABEL[this.kind]
    return block
  }
}

// One marker per class, reused: the gutter redraws on every scroll, and four
// objects are cheaper than one per visible line.
const MARKERS: Record<LineKind, RailMarker> = {
  cited: new RailMarker('cited'),
  assessed: new RailMarker('assessed'),
  unmarked: new RailMarker('unmarked'),
  neutral: new RailMarker('neutral'),
}

const railTheme = EditorView.baseTheme({
  '.cm-gutter-evidence': { minWidth: '10px', paddingLeft: '3px', paddingRight: '4px' },
  '.cm-gutter-evidence .cm-gutterElement': {
    display: 'flex',
    alignItems: 'stretch',
    justifyContent: 'center',
  },
  '.cm-rail-block': {
    width: '3px',
    minHeight: '2px',
    borderRadius: '1px',
    background: 'var(--rm-rail-neutral, var(--border))',
  },
  '.cm-rail-cited': { background: 'var(--rm-rail-cited, var(--primary))' },
  '.cm-rail-assessed': { background: 'var(--rm-rail-assessed, var(--muted-foreground))' },
  '.cm-rail-unmarked': { background: 'var(--rm-rail-unmarked, var(--destructive))' },
  '.cm-rail-neutral': { background: 'var(--rm-rail-neutral, var(--border))' },
  // `&` is the editor root, which is where the stale class lands.
  '&.cm-rail-is-stale .cm-gutter-evidence': { opacity: '0.3' },
})

/** The gutter. Safe to add and remove at will — everything it draws from lives
 *  in `railState`. */
export function evidenceRail(): Extension {
  return [
    railTheme,
    gutter({
      class: 'cm-gutter-evidence',
      side: 'after',
      lineMarker: (view, block) => {
        const kinds = view.state.field(railField, false)?.kinds
        // Nothing drawn until the server has spoken: an all-neutral rail would
        // read as "scored, nothing to say" when it means "not scored yet".
        if (!kinds || kinds.size === 0) return null
        const number = view.state.doc.lineAt(block.from).number
        return MARKERS[kinds.get(number) ?? 'neutral']
      },
      lineMarkerChange: (update) =>
        update.startState.field(railField, false) !== update.state.field(railField, false),
      // Holds the gutter's width open while there is nothing to show, so the
      // text does not jump sideways the first time a score arrives.
      initialSpacer: () => MARKERS.neutral,
    }),
  ]
}

// ── `@` completion over the report's own bibliography ────────────────────────

/** The span a citation may occupy after its `@`. Matches the tokeniser in
 *  `lib/typst.ts`, which colours the same text. */
const KEY_CHARS = /^[\w.:+-]*$/

function option(row: SourceRow): Completion {
  return {
    label: row.key,
    // The title is what a writer recognises; the key is what they are typing.
    detail: row.title || 'untitled',
    info: row.type,
    type: 'constant',
    // A source with nothing citing it yet is usually the one just added, and
    // usually the one being reached for. Orphans sort first, for that one
    // keystroke.
    boost: row.uses === 0 ? 1 : 0,
  }
}

/**
 * The completion list, sized for a thumb.
 *
 * CodeMirror's defaults are a mouse's: 13px rows about 20px tall. On a phone
 * the list is the affordance the whole product leans on, so its rows clear
 * 40px, its text stays at the editor's own size, and the source title sits on
 * the right where a reader's eye finishes rather than pushed off the end of a
 * line. The title is set as text by CodeMirror, so a source called
 * `<script>` is a source called `<script>`.
 */
const completionTheme = EditorView.baseTheme({
  '.cm-tooltip.cm-tooltip-autocomplete': { maxWidth: 'min(92vw, 32rem)' },
  '.cm-tooltip.cm-tooltip-autocomplete > ul': {
    maxHeight: '40vh',
    fontFamily: 'var(--font-mono)',
    fontSize: '15px',
  },
  '.cm-tooltip.cm-tooltip-autocomplete > ul > li': {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    minHeight: '40px',
    padding: '4px 10px',
    lineHeight: '1.25',
  },
  '.cm-tooltip-autocomplete ul li[aria-selected]': {
    background: 'var(--accent)',
    color: 'var(--accent-foreground)',
  },
  '.cm-completionLabel': { flex: '0 0 auto', fontWeight: '600' },
  '.cm-completionMatchedText': { textDecoration: 'none', color: 'var(--rail-cited)' },
  '.cm-completionDetail': {
    marginLeft: 'auto',
    maxWidth: '60%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontStyle: 'normal',
    fontFamily: 'ui-sans-serif, -apple-system, sans-serif',
    fontSize: '13px',
    color: 'var(--muted-foreground)',
  },
})

/** Fires on `@` and replaces only what follows it, so the marker the syntax
 *  highlighter keys on survives the completion. */
export function citationCompletion(getSources: () => readonly SourceRow[]): CompletionSource {
  return (context: CompletionContext): CompletionResult | null => {
    const token = context.matchBefore(/@[\w.:+-]*/)
    if (!token) return null

    const options = getSources().map(option)
    if (options.length === 0) return null

    return { from: token.from + 1, options, validFor: KEY_CHARS }
  }
}

/**
 * The same thing, ready to drop into an extension list.
 *
 * `getSources` is a getter rather than an array because the bibliography changes
 * under a document that stays open: citing a page must not cost the writer their
 * cursor.
 *
 * Registered as language data rather than as an `override`, which would displace
 * every other completion source in the document. On a phone the list is opened
 * explicitly (`activateOnTyping: false` is *not* set — typing `@` should offer
 * it, and the accessory bar's `@` does the same thing with one tap).
 */
export function citationCompletionExtension(
  getSources: () => readonly SourceRow[]
): Extension {
  const source = citationCompletion(getSources)
  return [
    completionTheme,
    autocompletion({
      // A phone's completion list must be reachable by thumb and readable at
      // arm's length; the defaults are tuned for a mouse.
      maxRenderedOptions: 24,
      icons: false,
      closeOnBlur: true,
    }),
    EditorState.languageData.of(() => [{ autocomplete: source }]),
  ]
}

// ── writing into the buffer ──────────────────────────────────────────────────

/**
 * Insert text at the cursor and keep the keyboard up.
 *
 * Every accessory-bar button ends here. It focuses the view first, because a
 * button that steals focus to type a `#` has closed the keyboard to insert one
 * character — which is the failure mode that makes people stop using an
 * accessory bar at all.
 */
export function insertAtCursor(view: EditorView, text: string, caretOffset?: number): void {
  const { from, to } = view.state.selection.main
  const at = from + (caretOffset ?? text.length)
  view.dispatch({
    changes: { from, to, insert: text },
    selection: { anchor: at },
    scrollIntoView: true,
    userEvent: 'input.type',
  })
  view.focus()
}

/** Wrap the selection, or drop an empty pair with the caret between — `*` and
 *  `_` are the two Typst emphases and both behave this way in every editor a
 *  writer has used. */
export function wrapSelection(view: EditorView, before: string, after = before): void {
  const { from, to } = view.state.selection.main
  const selected = view.state.sliceDoc(from, to)
  view.dispatch({
    changes: { from, to, insert: `${before}${selected}${after}` },
    selection: selected
      ? { anchor: from, head: from + before.length + selected.length + after.length }
      : { anchor: from + before.length },
    scrollIntoView: true,
    userEvent: 'input.type',
  })
  view.focus()
}

/** Put the cursor on a 1-based line and centre it. */
export function jumpToLine(view: EditorView, target: number): void {
  const doc = view.state.doc
  const line = doc.line(Math.min(Math.max(Math.round(target), 1), doc.lines))
  view.dispatch({
    selection: { anchor: line.from },
    effects: EditorView.scrollIntoView(line.from, { y: 'center' }),
  })
  view.focus()
}

// ── the soft keyboard, measured ──────────────────────────────────────────────

/**
 * How many pixels of the layout viewport the keyboard (and anything else) is
 * covering at the bottom.
 *
 * This is the number every mobile editor gets wrong. When the soft keyboard
 * opens, iOS Safari does **not** shrink the layout viewport — `100dvh` still
 * reports the full 812px and a bar pinned to the bottom of it ends up behind
 * the keys. `visualViewport` is the only thing that tells the truth, and
 * `offsetTop` matters as well as `height`, because iOS scrolls the visual
 * viewport up when the caret would otherwise be hidden.
 */
export function useKeyboardInset(): number {
  const subscribe = useCallback((onChange: () => void) => {
    const vv = window.visualViewport
    if (!vv) return () => {}
    vv.addEventListener('resize', onChange)
    vv.addEventListener('scroll', onChange)
    window.addEventListener('orientationchange', onChange)
    return () => {
      vv.removeEventListener('resize', onChange)
      vv.removeEventListener('scroll', onChange)
      window.removeEventListener('orientationchange', onChange)
    }
  }, [])

  const get = useCallback(() => {
    const vv = window.visualViewport
    if (!vv) return 0
    // Rounded, because iOS reports fractional heights and a value that flickers
    // between 291.5 and 292 would re-render on every scroll event.
    return Math.max(0, Math.round(window.innerHeight - (vv.height + vv.offsetTop)))
  }, [])

  return useSyncExternalStore(subscribe, get, () => 0)
}

/**
 * Pin an element's bottom edge to the bottom of the *visual* viewport.
 *
 * The element is given an explicit height — the distance from where it starts
 * to where the visible area ends — and `flex: none`, because a `flex-1` child
 * has a zero basis and would ignore the height entirely.
 *
 * This is what makes the accessory bar sit on top of the keyboard rather than
 * under it: the bar is the last thing in a normal flow inside this box, so when
 * the box ends where the keys begin, so does the bar. Nothing is
 * `position: fixed`, which is the other half of why it works — a fixed element
 * is positioned against the layout viewport, and the layout viewport is exactly
 * the thing that lies while a keyboard is open.
 */
export function useVisualViewportFit<T extends HTMLElement>(
  ref: RefObject<T | null>,
  enabled: boolean
): void {
  useEffect(() => {
    const node = ref.current
    if (!node) return

    if (!enabled) {
      node.style.removeProperty('height')
      node.style.removeProperty('flex')
      return
    }

    const apply = (): void => {
      const el = ref.current
      if (!el) return
      const vv = window.visualViewport
      const visual = vv ? vv.offsetTop + vv.height : window.innerHeight
      // Two floors, and the lower one wins. The visual viewport is where the
      // keyboard begins; the parent box is where the tab bar begins. Fitting to
      // the first alone slides the last two lines of the document under the tab
      // bar whenever the keyboard is *shut*, which is most of the time.
      const parent = el.parentElement?.getBoundingClientRect()
      const bottom = parent ? Math.min(parent.bottom, visual) : visual
      // The element's own top never moves when we change its height — it is a
      // flex child laid out after its siblings — so measuring it after the
      // write is safe and the next frame's number is not stale.
      const top = el.getBoundingClientRect().top
      const height = Math.max(120, Math.round(bottom - top))
      const next = `${height}px`
      if (el.style.height !== next) el.style.height = next
      if (el.style.flex !== 'none') el.style.flex = 'none'
    }

    // Applied synchronously rather than on the next animation frame. A frame
    // callback is the tidier way to coalesce layout work, and it is the wrong
    // tool here: the keyboard-open resize is the one moment the geometry has to
    // be right *now*, and a frame that is throttled — a backgrounded tab, a
    // browser mid-gesture — leaves the accessory bar behind the keys. The work
    // is one rect read and one style write that is skipped when nothing moved.
    const schedule = apply

    apply()

    const vv = window.visualViewport
    vv?.addEventListener('resize', schedule)
    vv?.addEventListener('scroll', schedule)
    window.addEventListener('resize', schedule)
    window.addEventListener('orientationchange', schedule)

    // The shell removes the bottom tab bar when the keyboard opens, which
    // changes where this element's top sits. Watching the parent catches that
    // and anything else that reflows above us.
    const parent = node.parentElement
    const observer = parent ? new ResizeObserver(schedule) : null
    if (parent && observer) observer.observe(parent)

    return () => {
      vv?.removeEventListener('resize', schedule)
      vv?.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
      window.removeEventListener('orientationchange', schedule)
      observer?.disconnect()
      const el = ref.current
      if (el) {
        el.style.removeProperty('height')
        el.style.removeProperty('flex')
      }
    }
  }, [ref, enabled])
}

/**
 * The page's polarity, resolved the way `styles.css` resolves it: the system
 * preference, overridden in both directions by `data-theme` on <html>.
 */
export function useDarkChrome(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    query.addEventListener('change', onChange)
    const observer = new MutationObserver(onChange)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    })
    return () => {
      query.removeEventListener('change', onChange)
      observer.disconnect()
    }
  }, [])

  const get = useCallback(() => {
    const override = document.documentElement.dataset.theme
    if (override === 'dark') return true
    if (override === 'light') return false
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  }, [])

  return useSyncExternalStore(subscribe, get, () => false)
}
