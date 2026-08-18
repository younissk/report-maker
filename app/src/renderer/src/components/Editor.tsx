import { useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Compartment, EditorState, type Extension } from '@codemirror/state'
import {
  EditorView,
  crosshairCursor,
  drawSelection,
  dropCursor,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  rectangularSelection
} from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { bracketMatching, indentOnInput, indentUnit } from '@codemirror/language'
import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap
} from '@codemirror/autocomplete'
import { highlightSelectionMatches, searchKeymap } from '@codemirror/search'
import { lintGutter, lintKeymap } from '@codemirror/lint'
import type { Finding, LineClass, Settings } from '../../../shared/types'
import { languageFor } from '@/lib/typst'
import { editorTheme } from '@/lib/cmtheme'
import { findingClicks, findingState, reportLinter, setFindings } from '@/lib/lint'
import { evidenceRail, railStale, railState, setLineClasses } from '@/lib/rail'

/** What the parent can ask the editor to do. */
export type EditorHandle = {
  /** Put the cursor on a 1-based line and scroll it into view. Called before the
   *  file has finished opening, it is remembered and applied when it does. */
  gotoLine: (line: number) => void
  focus: () => void
  /** The live view, for anything that genuinely needs it. */
  view: () => EditorView | null
}

type Props = {
  /** Absolute path of the open file; `null` shows the empty state. */
  path: string | null
  /** The same file as the engine names it — vault-relative POSIX. Used to match
   *  `Finding.path`; when omitted the absolute path is matched by suffix. */
  rel?: string | null
  text: string
  settings: Settings
  /** The latest `check --json` for the vault. Findings for other files are
   *  ignored here, not filtered out by the caller. */
  findings?: readonly Finding[]
  /** The `lines` of this report's `ReportScore`, for the evidence rail. */
  lineClasses?: readonly LineClass[]
  /** True while the buffer differs from the file the findings and classes were
   *  computed from. Dims the rail rather than lying about it. */
  stale?: boolean
  /** Chrome polarity. Defaults to what `settings.appearance.theme` resolves to. */
  dark?: boolean
  /** A 1-based line to jump to. Survives a file switch — the jump happens once
   *  the new document is open — which the imperative handle cannot. */
  atLine?: number | null
  /** Extra CodeMirror extensions (completion sources, hover tooltips). Memoize
   *  it: a new array identity reconfigures the editor. */
  extra?: Extension
  onChange: (text: string) => void
  onSave: () => void
  onBuild: () => void
  /** The 1-based line the cursor sits on, for PDF sync. Fires only on change. */
  onCursorLine?: (line: number) => void
  /** A lint gutter marker was clicked — the Problems panel should filter to it. */
  onFindingSelect?: (finding: Finding) => void
  ref?: React.Ref<EditorHandle>
}

/**
 * Two compartments, and everything else static.
 *
 * A settings change must not cost the writer their cursor or their undo history,
 * so the editor is never remounted for one. CodeMirror's answer is a
 * `Compartment`: an extension slot that a transaction can swap without building
 * a new `EditorState`. What matters is *what goes in the slot*.
 *
 * Anything holding state we cannot rebuild stays outside: `history()`, because
 * reconfiguring it away discards the undo stack; `findingState` and `railState`,
 * because they hold answers from the engine that toggling a gutter must not
 * throw away; the keymaps and the update listener, because they are wiring, not
 * looks. What goes inside is only presentation — the theme, the gutters, wrap,
 * tab size, bracket matching. Every one of those is a pure function of
 * `Settings.editor` and can be thrown away and rebuilt with no loss.
 *
 * That split is why `lib/rail.ts` publishes its state field and its gutter as
 * two separate exports: the state belongs on the static side of the line, the
 * gutter on the reconfigurable one.
 *
 * The view is still rebuilt when the *file* changes, because a new document
 * genuinely is a new document — sharing an undo history across two files is a
 * bug, not a feature.
 */
const look = new Compartment()
const injected = new Compartment()

/** The presentation half: everything `Settings.editor` decides. */
function looksLike(settings: Settings, dark: boolean): Extension {
  const editor = settings.editor
  return [
    editorTheme(settings, dark),
    editor.lineNumbers ? lineNumbers() : [],
    editor.highlightActiveLine ? [highlightActiveLine(), highlightActiveLineGutter()] : [],
    editor.wordWrap ? EditorView.lineWrapping : [],
    EditorState.tabSize.of(editor.tabSize),
    indentUnit.of(' '.repeat(editor.tabSize)),
    editor.bracketMatching ? bracketMatching() : [],
    editor.evidenceRail ? evidenceRail() : [],
    editor.lintGutter ? lintGutter() : []
  ]
}

/** Cheap identity for the above, so a re-render with equal settings does not
 *  rebuild the gutters for nothing. */
function looksKey(settings: Settings, dark: boolean): string {
  const e = settings.editor
  return [
    e.fontFamily,
    e.fontSize,
    e.lineHeight,
    e.lineNumbers,
    e.wordWrap,
    e.tabSize,
    e.highlightActiveLine,
    e.bracketMatching,
    e.evidenceRail,
    e.lintGutter,
    e.syntaxTheme,
    dark
  ].join('|')
}

/** The chrome's polarity, resolved the way the app resolves it. */
function useDarkChrome(theme: Settings['appearance']['theme'], override?: boolean): boolean {
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  )

  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!query) return
    const onChange = (event: MediaQueryListEvent): void => setSystemDark(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  if (override !== undefined) return override
  return theme === 'system' ? systemDark : theme === 'dark'
}

function jump(view: EditorView, target: number): void {
  const doc = view.state.doc
  const line = doc.line(Math.min(Math.max(Math.round(target), 1), doc.lines))
  view.dispatch({
    selection: { anchor: line.from },
    effects: EditorView.scrollIntoView(line.from, { y: 'center' })
  })
  view.focus()
}

/**
 * CodeMirror, recreated whenever the open file changes and reconfigured
 * otherwise. The parent owns the text; this view reports edits upward and never
 * re-seeds itself from props, which is what keeps the cursor where the writer
 * put it.
 */
export function Editor({
  path,
  rel,
  text,
  settings,
  findings,
  lineClasses,
  stale = false,
  dark,
  atLine,
  extra,
  onChange,
  onSave,
  onBuild,
  onCursorLine,
  onFindingSelect,
  ref
}: Props) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const cursorLine = useRef(0)
  const pendingLine = useRef<number | null>(null)
  const appliedLook = useRef('')
  const appliedExtra = useRef<Extension | undefined>(undefined)

  const isDark = useDarkChrome(settings.appearance.theme, dark)
  const key = looksKey(settings, isDark)

  // One box of everything the view's long-lived callbacks need to read. The view
  // outlives any single render, so it must never close over a render's props.
  const latest = useRef({
    findings,
    lineClasses,
    stale,
    docPath: rel ?? path,
    onChange,
    onSave,
    onBuild,
    onCursorLine,
    onFindingSelect
  })
  latest.current = {
    findings,
    lineClasses,
    stale,
    docPath: rel ?? path,
    onChange,
    onSave,
    onBuild,
    onCursorLine,
    onFindingSelect
  }

  useImperativeHandle(
    ref,
    () => ({
      gotoLine(line: number) {
        const instance = view.current
        if (instance) jump(instance, line)
        else pendingLine.current = line
      },
      focus: () => view.current?.focus(),
      view: () => view.current
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
      autocompletion(),
      rectangularSelection(),
      crosshairCursor(),
      highlightSelectionMatches(),
      ...languageFor(path),
      findingState,
      railState,
      reportLinter(),
      findingClicks((finding) => latest.current.onFindingSelect?.(finding)),
      keymap.of([
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...searchKeymap,
        ...historyKeymap,
        ...completionKeymap,
        ...lintKeymap,
        indentWithTab,
        { key: 'Mod-s', preventDefault: true, run: () => (latest.current.onSave(), true) },
        { key: 'Mod-b', preventDefault: true, run: () => (latest.current.onBuild(), true) }
      ]),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) latest.current.onChange(update.state.doc.toString())
        if (!update.docChanged && !update.selectionSet) return
        const line = update.state.doc.lineAt(update.state.selection.main.head).number
        if (line === cursorLine.current) return
        cursorLine.current = line
        latest.current.onCursorLine?.(line)
      }),

      // ── reconfigurable
      look.of(looksLike(settings, isDark)),
      injected.of(extra ?? [])
    ]

    const instance = new EditorView({
      state: EditorState.create({ doc: text, extensions }),
      parent: host.current
    })
    view.current = instance
    appliedLook.current = key
    appliedExtra.current = extra
    cursorLine.current = 0

    // The state fields start empty, so whatever the app already knows has to be
    // pushed in once the view exists.
    setFindings(instance, latest.current.findings ?? [], latest.current.docPath)
    setLineClasses(instance, latest.current.lineClasses ?? [])
    railStale(instance, latest.current.stale)

    const requested = pendingLine.current ?? atLine ?? null
    pendingLine.current = null
    if (requested !== null) jump(instance, requested)
    else instance.focus()

    return () => {
      instance.destroy()
      view.current = null
    }
    // Deliberately keyed on the path alone: a new document means a new view, an
    // edit or a settings change does not. Everything else this effect reads is
    // either seeded above or reconfigured by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  // Settings, live. Skipped on the render that created the view, which already
  // built the same configuration.
  useEffect(() => {
    const instance = view.current
    if (!instance || appliedLook.current === key) return
    appliedLook.current = key
    instance.dispatch({ effects: look.reconfigure(looksLike(settings, isDark)) })
    // `key` is the settings fingerprint; `settings` itself is read through it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    const instance = view.current
    if (!instance || appliedExtra.current === extra) return
    appliedExtra.current = extra
    instance.dispatch({ effects: injected.reconfigure(extra ?? []) })
  }, [extra])

  useEffect(() => {
    const instance = view.current
    if (instance) setFindings(instance, findings ?? [], rel ?? path)
  }, [findings, rel, path])

  useEffect(() => {
    const instance = view.current
    if (instance) setLineClasses(instance, lineClasses ?? [])
  }, [lineClasses])

  useEffect(() => {
    const instance = view.current
    if (instance) railStale(instance, stale)
  }, [stale])

  // Declarative jump, for a click in the Problems panel that also changed file:
  // it fires again once the new document's view exists.
  useEffect(() => {
    const instance = view.current
    if (instance && atLine != null) jump(instance, atLine)
  }, [atLine, path])

  if (path === null) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center text-muted-foreground">
        <div>
          <p className="text-sm">Nothing open.</p>
          <p className="mt-1 text-xs">
            Pick a <span className="font-mono">main.typ</span> or{' '}
            <span className="font-mono">sources.yml</span> from the tree.
          </p>
        </div>
      </div>
    )
  }

  return <div ref={host} className="h-full overflow-hidden" />
}
