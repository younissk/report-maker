import { useEffect, useImperativeHandle, useRef, type Ref } from 'react'
import { Compartment, EditorState, type Extension } from '@codemirror/state'
import {
  EditorView,
  drawSelection,
  dropCursor,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  tooltips,
} from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { bracketMatching, indentOnInput, indentUnit } from '@codemirror/language'
import {
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
  startCompletion,
} from '@codemirror/autocomplete'
import { lintGutter, lintKeymap } from '@codemirror/lint'

import type { Finding, LineClass, SourceRow } from '@/lib/api'
import { editorTheme, languageFor } from '@/lib/typst'
import {
  citationCompletionExtension,
  evidenceRail,
  findingClicks,
  findingState,
  insertAtCursor,
  jumpToLine,
  railStale,
  railState,
  reportLinter,
  setFindings,
  setLineClasses,
  useDarkChrome,
  wrapSelection,
} from '@/lib/editor'

/**
 * CodeMirror, with the citation rule in both margins.
 *
 * The buffer belongs to this component once it is open: `text` seeds the
 * document and is never written back into it, because re-seeding from a prop is
 * how an editor loses somebody's cursor mid-sentence. Edits travel upward
 * through `onChange` and nowhere else.
 *
 * A new *file* rebuilds the view — sharing an undo history across two documents
 * is a bug, not a feature. Everything short of that is a compartment
 * reconfigure, so switching to dark or rotating a phone costs no state.
 *
 * Nothing here interprets a finding, a line class or a source. They arrive from
 * `/api/check`, `/api/score` and `/api/sources/:id` already decided.
 */

export type EditorHandle = {
  /** Put the cursor on a 1-based line and centre it. Remembered if the document
   *  is not open yet. */
  gotoLine: (line: number) => void
  focus: () => void
  /** Type at the cursor without taking focus off the buffer. */
  insert: (text: string, caretOffset?: number) => void
  wrap: (before: string, after?: string) => void
  /** Open the `@` list — the accessory bar's one-tap route to a citation. */
  complete: () => void
  /** Re-measure after the pane comes back from `display: none`, where the
   *  editor had no geometry to measure. */
  measure: () => void
  view: () => EditorView | null
}

export type EditorProps = {
  /**
   * The open file as the engine names it — vault-relative POSIX. Doubles as the
   * key that decides when a new view is built, and as what `Finding.path` is
   * matched against. `null` renders the empty state.
   */
  path: string | null
  /** Initial text. Read once per `path`; see the note above. */
  text: string
  /** The vault's findings, unfiltered. The linter keeps the ones for `path`. */
  findings?: readonly Finding[]
  /** The `lines` of this report's `ReportScore`, for the evidence rail. */
  lineClasses?: readonly LineClass[]
  /** True while the buffer differs from the file the server last read. Dims the
   *  rail rather than lying about where its blocks belong. */
  stale?: boolean
  /** This report's `sources.yml`, for `@` completion. Read through a ref, so a
   *  newly cited page is offered without rebuilding the editor. */
  sources?: readonly SourceRow[]
  /** Line numbers and the active-line highlight: a laptop's affordances, and
   *  28px of a 375px screen that a phone would rather spend on words. */
  desktop?: boolean
  /** A 1-based line to jump to. Survives a file switch, which the imperative
   *  handle cannot: the jump happens once the new document exists. */
  atLine?: number | null
  onChange: (text: string) => void
  /** Cmd/Ctrl-S, and any other explicit save. */
  onSave: () => void
  /** Cmd/Ctrl-B. */
  onBuild: () => void
  /** The buffer lost focus — the last chance to flush a debounced save before
   *  whatever took the focus does something with the file. */
  onBlur?: () => void
  /** A lint marker was tapped. */
  onFindingSelect?: (finding: Finding) => void
  className?: string
  ref?: Ref<EditorHandle>
}

/**
 * One compartment, holding everything that is purely presentation: the theme,
 * the gutters, the tab size. Every one of those is a pure function of two
 * booleans and can be thrown away and rebuilt with nothing lost.
 *
 * What stays outside is anything holding state we could not rebuild —
 * `history()`, whose undo stack a reconfigure would discard, and `findingState`
 * and `railState`, which hold answers from the server that toggling a gutter
 * must not throw away.
 */
const look = new Compartment()

function looksLike(dark: boolean, desktop: boolean): Extension {
  return [
    editorTheme(dark),
    desktop ? [lineNumbers(), highlightActiveLineGutter()] : [],
    highlightActiveLine(),
    bracketMatching(),
    EditorState.tabSize.of(2),
    indentUnit.of('  '),
    // Both gutters are on at every width. They are not decoration — they are
    // the citation rule, and a phone is exactly where a writer most needs to
    // see that this paragraph is still unmarked.
    lintGutter(),
    evidenceRail(),
  ]
}

export function Editor({
  path,
  text,
  findings,
  lineClasses,
  stale = false,
  sources,
  desktop = false,
  atLine,
  onChange,
  onSave,
  onBuild,
  onBlur,
  onFindingSelect,
  className,
  ref,
}: EditorProps) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const pendingLine = useRef<number | null>(null)
  const appliedLook = useRef('')

  const dark = useDarkChrome()
  const key = `${dark}|${desktop}`

  // One box of everything the view's long-lived callbacks read. The view
  // outlives any single render, so it must never close over a render's props.
  const latest = useRef({ findings, lineClasses, stale, sources, path, onChange, onSave, onBuild, onBlur, onFindingSelect })
  latest.current = { findings, lineClasses, stale, sources, path, onChange, onSave, onBuild, onBlur, onFindingSelect }

  useImperativeHandle(
    ref,
    () => ({
      gotoLine(line: number) {
        const instance = view.current
        if (instance) jumpToLine(instance, line)
        else pendingLine.current = line
      },
      focus: () => view.current?.focus(),
      insert(value: string, caretOffset?: number) {
        const instance = view.current
        if (instance) insertAtCursor(instance, value, caretOffset)
      },
      wrap(before: string, after?: string) {
        const instance = view.current
        if (instance) wrapSelection(instance, before, after)
      },
      complete() {
        const instance = view.current
        if (!instance) return
        instance.focus()
        startCompletion(instance)
      },
      measure: () => view.current?.requestMeasure(),
      view: () => view.current,
    }),
    []
  )

  useEffect(() => {
    if (!host.current || path === null) return

    const extensions: Extension[] = [
      // ── static: state we could not rebuild, and wiring
      history(),
      highlightSpecialChars(),
      drawSelection(),
      dropCursor(),
      EditorState.allowMultipleSelections.of(true),
      indentOnInput(),
      closeBrackets(),
      // Always on. A report is prose, and prose that scrolls sideways on a
      // 375px screen is prose nobody reads back.
      EditorView.lineWrapping,
      // Keep the caret clear of both edges — on a phone the bottom edge is
      // where the accessory bar and then the keyboard begin.
      EditorView.scrollMargins.of(() => ({ top: 24, bottom: 64 })),
      // A completion list is positioned against the window by default, and the
      // window is 812px tall while a keyboard is open — so the list a phone
      // most needs would be placed in the 320px the keys are covering. Telling
      // CodeMirror that the space it may use is the editor's own box fixes it
      // in one line: the editor already ends where the accessory bar begins,
      // and the accessory bar already ends where the keyboard begins.
      tooltips({
        position: 'fixed',
        tooltipSpace: (view) => {
          const box = view.dom.getBoundingClientRect()
          return {
            top: box.top + 4,
            bottom: box.bottom - 4,
            left: 4,
            right: window.innerWidth - 4,
          }
        },
      }),
      ...languageFor(path),
      citationCompletionExtension(() => latest.current.sources ?? []),
      findingState,
      railState,
      reportLinter(),
      findingClicks((finding) => latest.current.onFindingSelect?.(finding)),
      keymap.of([
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...historyKeymap,
        ...completionKeymap,
        ...lintKeymap,
        indentWithTab,
        { key: 'Mod-s', preventDefault: true, run: () => (latest.current.onSave(), true) },
        { key: 'Mod-b', preventDefault: true, run: () => (latest.current.onBuild(), true) },
      ]),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) latest.current.onChange(update.state.doc.toString())
      }),
      // The last chance to flush a debounced write before the thing that took
      // the focus — the Build button, most often — asks the server about a file
      // it is about to read.
      EditorView.domEventHandlers({
        blur: () => {
          latest.current.onBlur?.()
          return false
        },
      }),

      // ── reconfigurable
      look.of(looksLike(dark, desktop)),
    ]

    const instance = new EditorView({
      state: EditorState.create({ doc: text, extensions }),
      parent: host.current,
    })
    view.current = instance
    appliedLook.current = key

    // The state fields start empty, so whatever we already know has to be
    // pushed in once the view exists.
    setFindings(instance, latest.current.findings ?? [], latest.current.path)
    setLineClasses(instance, latest.current.lineClasses ?? [])
    railStale(instance, latest.current.stale)

    const requested = pendingLine.current ?? atLine ?? null
    pendingLine.current = null
    if (requested !== null) jumpToLine(instance, requested)
    // Deliberately no `focus()` on open. On a desktop it is a courtesy; on a
    // phone it throws the keyboard up over two thirds of the screen before
    // anybody has said they want to type, and the first thing the writer has to
    // do is dismiss it.

    // A pane that was `display: none` has no measurable geometry, and the shell
    // hides three of its four panes that way. Re-measure whenever the box we
    // live in changes size, which covers coming back from hidden, the keyboard
    // opening, and a rotation.
    const observer = new ResizeObserver(() => instance.requestMeasure())
    if (host.current) observer.observe(host.current)

    return () => {
      observer.disconnect()
      instance.destroy()
      view.current = null
    }
    // Keyed on the path alone: a new document is a new view, an edit or a theme
    // change is not. Everything else this effect reads is seeded above or
    // reconfigured below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  useEffect(() => {
    const instance = view.current
    if (!instance || appliedLook.current === key) return
    appliedLook.current = key
    instance.dispatch({ effects: look.reconfigure(looksLike(dark, desktop)) })
  }, [key, dark, desktop])

  useEffect(() => {
    const instance = view.current
    if (instance) setFindings(instance, findings ?? [], path)
  }, [findings, path])

  useEffect(() => {
    const instance = view.current
    if (instance) setLineClasses(instance, lineClasses ?? [])
  }, [lineClasses])

  useEffect(() => {
    const instance = view.current
    if (instance) railStale(instance, stale)
  }, [stale])

  // Declarative jump, for a tap on a finding that also changed file: it fires
  // again once the new document's view exists.
  useEffect(() => {
    const instance = view.current
    if (instance && atLine != null) jumpToLine(instance, atLine)
  }, [atLine, path])

  if (path === null) {
    return (
      <div
        className={className}
        // The empty state is the pane's to render; this component only refuses
        // to draw a CodeMirror with nothing in it.
      />
    )
  }

  return <div ref={host} className={className} />
}
