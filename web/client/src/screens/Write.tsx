import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from 'react'
import {
  AlertCircle,
  ChevronDown,
  Cloud,
  CloudOff,
  Loader2,
  Quote,
  RefreshCw,
} from 'lucide-react'

import { useApp } from '@/App'
import { Editor, type EditorHandle } from '@/components/Editor'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  api,
  errorText,
  isAbort,
  ApiError,
  type FileNode,
  type Finding,
  type ReportDetail,
  type ReportScore,
  type SourceRow,
} from '@/lib/api'
import { RAIL_LABEL, useKeyboardInset, useVisualViewportFit } from '@/lib/editor'
import {
  onInsert as onInsertRequest,
  onReveal as onRevealRequest,
} from '@/lib/evidence'
import { guard } from '@/lib/session'
import { cn, useIsDesktop } from '@/lib/utils'

/**
 * The Write pane.
 *
 * One report's `main.typ` in CodeMirror, with the citation rule visible in both
 * margins while you type: `check`'s findings in the left gutter, `score`'s line
 * classes down the right. Neither is computed here. This file asks four
 * questions of the server — what files does this report have, what is in this
 * one, what does the rule say about it, what sources may I cite — and draws the
 * answers.
 *
 * Three timings, and the reasoning behind each:
 *
 *   **900ms → save.** Nobody should hunt for a save button on a phone, and
 *   nobody should lose a paragraph to a backgrounded tab either. It also
 *   flushes the moment the buffer loses focus, which is what makes the Build
 *   button in the top bar act on the words that are on the screen.
 *
 *   **1200ms → ask the rule.** Longer than the save on purpose: by the time the
 *   idle fires, the file the engine is about to read is the file you can see.
 *
 *   **While unsaved → dim the rail.** The classes describe the saved file. A
 *   rail that kept its full weight over a buffer that has moved under it would
 *   be confidently pointing at the wrong lines.
 */

// ── which files a report offers ──────────────────────────────────────────────

/** The text files inside a report folder, flattened out of the tree the server
 *  returned. Nothing is inferred about where they live: every path here came
 *  from `GET /api/reports/:id`. */
function textFiles(nodes: readonly FileNode[] | undefined): FileNode[] {
  const out: FileNode[] = []
  const walk = (list: readonly FileNode[]): void => {
    for (const node of list) {
      if (node.kind === 'dir') walk(node.children ?? [])
      else if (/\.(typ|ya?ml|mmd)$/i.test(node.name)) out.push(node)
    }
  }
  walk(nodes ?? [])
  // `main.typ` first, then `sources.yml`, then the rest alphabetically — the
  // order a writer moves between them in.
  const rank = (name: string): number =>
    name === 'main.typ' ? 0 : /^sources\.ya?ml$/i.test(name) ? 1 : 2
  return out.sort(
    (a, b) => rank(a.name) - rank(b.name) || a.path.localeCompare(b.path)
  )
}

type SaveState = 'clean' | 'dirty' | 'saving' | 'saved' | 'error'

const SAVE_DEBOUNCE = 900
const IDLE_REFRESH = 1200

export function Write() {
  const {
    reportId,
    tab,
    setTab,
    check,
    refreshCheck,
    build,
    building,
    buildResult,
    revision,
    invalidate,
  } = useApp()

  const desktop = useIsDesktop()
  const keyboardInset = useKeyboardInset()
  const keyboardOpen = !desktop && keyboardInset > 120

  const editor = useRef<EditorHandle>(null)
  const frame = useRef<HTMLDivElement>(null)

  // The editor's box is pinned to the bottom of the *visual* viewport on a
  // phone, so the accessory bar lands on top of the keyboard instead of behind
  // it. See `useVisualViewportFit`.
  //
  // `reportId` is in the condition and not decoration: with no report open this
  // pane renders an empty state and `frame` is not in the tree at all, so the
  // hook's effect would find a null ref, bail, and — its dependencies never
  // having changed — never run again once the report arrived a moment later.
  // Flipping `enabled` when the frame appears is what makes it attach. Measured
  // before the fix: with a 320px keyboard the accessory bar sat at y 764 while
  // the visual viewport ended at 492, i.e. entirely behind the keys.
  useVisualViewportFit(frame, !desktop && Boolean(reportId))

  // ── what is open ───────────────────────────────────────────────────────────

  const [detail, setDetail] = useState<ReportDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [path, setPath] = useState<string | null>(null)
  const [doc, setDoc] = useState<{ path: string; text: string } | null>(null)
  const [docError, setDocError] = useState<ApiError | Error | null>(null)
  const [reload, setReload] = useState(0)

  const files = useMemo(() => textFiles(detail?.files), [detail])
  const mainPath = useMemo(
    () => files.find((file) => file.name === 'main.typ')?.path ?? files[0]?.path ?? null,
    [files]
  )
  const sourcesPath = useMemo(
    () => files.find((file) => /^sources\.ya?ml$/i.test(file.name))?.path ?? null,
    [files]
  )

  // ── the rule's answers ─────────────────────────────────────────────────────

  const [score, setScore] = useState<ReportScore | null>(null)
  const [sources, setSources] = useState<SourceRow[]>([])

  // ── saving ─────────────────────────────────────────────────────────────────

  const [saveState, setSaveState] = useState<SaveState>('clean')
  const [saveError, setSaveError] = useState<string | null>(null)
  const pending = useRef<{ report: string; path: string; text: string } | null>(null)
  const saveTimer = useRef(0)
  const idleTimer = useRef(0)
  const queue = useRef<Promise<void>>(Promise.resolve())

  // ── overlays ───────────────────────────────────────────────────────────────

  const [findingsOpen, setFindingsOpen] = useState(false)
  const [citeOpen, setCiteOpen] = useState(false)
  const [legendOpen, setLegendOpen] = useState(false)
  const [atLine, setAtLine] = useState<number | null>(null)

  // ── the report's files ─────────────────────────────────────────────────────

  useEffect(() => {
    if (!reportId) {
      setDetail(null)
      setPath(null)
      setDoc(null)
      return
    }
    const controller = new AbortController()
    setDetailError(null)
    guard((signal) => api.getReport(reportId, signal), controller.signal)
      .then((result) => {
        setDetail(result)
        setPath((current) => {
          const list = textFiles(result.files)
          if (current && list.some((file) => file.path === current)) return current
          return list.find((file) => file.name === 'main.typ')?.path ?? list[0]?.path ?? null
        })
      })
      .catch((error) => {
        if (isAbort(error)) return
        setDetailError(errorText(error))
      })
    return () => controller.abort()
  }, [reportId, revision])

  // ── the open file ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (!reportId || !path) {
      setDoc(null)
      return
    }
    const controller = new AbortController()
    setDocError(null)
    guard((signal) => api.readFile(reportId, path, signal), controller.signal)
      .then((text) => {
        setDoc({ path, text })
        pending.current = null
        setSaveState('clean')
        setSaveError(null)
      })
      .catch((error) => {
        if (isAbort(error)) return
        setDoc(null)
        setDocError(error instanceof Error ? error : new Error(String(error)))
      })
    return () => controller.abort()
  }, [reportId, path, reload])

  // ── sources, for the `@` list ──────────────────────────────────────────────

  const loadSources = useCallback(
    (signal?: AbortSignal) => {
      if (!reportId) {
        setSources([])
        return Promise.resolve()
      }
      return guard((inner) => api.sources(reportId, inner), signal)
        .then(setSources)
        .catch((error) => {
          // A report with no `sources.yml` yet is not an error worth a banner:
          // the list is simply empty, and the writer is about to fix that.
          if (!isAbort(error)) setSources([])
        })
    },
    [reportId]
  )

  useEffect(() => {
    const controller = new AbortController()
    void loadSources(controller.signal)
    return () => controller.abort()
  }, [loadSources, revision])

  // ── the score, for the rail ────────────────────────────────────────────────

  const scoreRun = useRef<AbortController | null>(null)

  const loadScore = useCallback(() => {
    if (!reportId) {
      setScore(null)
      return
    }
    scoreRun.current?.abort()
    const controller = new AbortController()
    scoreRun.current = controller
    guard((signal) => api.score(reportId, signal), controller.signal)
      .then((result) => {
        // Selecting the row for this report is not a computation — the engine
        // summed every number on it.
        setScore(result.reports.find((row) => row.id === reportId) ?? null)
      })
      .catch((error) => {
        if (!isAbort(error)) setScore(null)
      })
  }, [reportId])

  useEffect(() => {
    loadScore()
    return () => scoreRun.current?.abort()
  }, [loadScore, revision])

  // ── autosave ───────────────────────────────────────────────────────────────

  const flush = useCallback((): Promise<void> => {
    window.clearTimeout(saveTimer.current)
    saveTimer.current = 0
    const job = pending.current
    if (!job) return queue.current
    pending.current = null
    setSaveState('saving')
    queue.current = queue.current.then(async () => {
      try {
        await guard((signal) => api.writeFile(job.report, job.path, job.text, signal))
        setSaveError(null)
        // Only claim "saved" if nothing was typed while the write was in the
        // air; otherwise the indicator would go green over a dirty buffer.
        setSaveState((current) => (pending.current ? current : 'saved'))
      } catch (error) {
        if (isAbort(error)) return
        // The text is put back so the next attempt — a keystroke, a blur, a
        // build — carries it. A failed save must never silently drop words.
        pending.current = job
        setSaveState('error')
        setSaveError(
          error instanceof ApiError && error.detail
            ? `${error.message}\n${error.detail}`
            : errorText(error)
        )
      }
    })
    return queue.current
  }, [])

  const onChange = useCallback(
    (text: string) => {
      if (!reportId || !path) return
      pending.current = { report: reportId, path, text }
      setSaveState('dirty')

      window.clearTimeout(saveTimer.current)
      saveTimer.current = window.setTimeout(() => void flush(), SAVE_DEBOUNCE)

      // The rule is asked once the typing stops, not once per keystroke. It is
      // a subprocess on the far end of a socket, and asking it mid-word would
      // answer about a file nobody has written yet.
      window.clearTimeout(idleTimer.current)
      idleTimer.current = window.setTimeout(() => {
        void flush().then(() => {
          refreshCheck()
          loadScore()
        })
      }, IDLE_REFRESH)
    },
    [reportId, path, flush, refreshCheck, loadScore]
  )

  // A tab away, a backgrounded browser, or a closed laptop lid are all "the
  // writing stopped" — and on a phone a backgrounded tab may never come back.
  useEffect(() => {
    const onHide = (): void => {
      if (document.visibilityState === 'hidden') void flush()
    }
    document.addEventListener('visibilitychange', onHide)
    window.addEventListener('pagehide', onHide)
    return () => {
      document.removeEventListener('visibilitychange', onHide)
      window.removeEventListener('pagehide', onHide)
    }
  }, [flush])

  useEffect(
    () => () => {
      window.clearTimeout(saveTimer.current)
      window.clearTimeout(idleTimer.current)
    },
    []
  )

  // ── coming back to this pane ───────────────────────────────────────────────

  // The shell keeps all four panes mounted and hides three, and an element that
  // was `display: none` has no geometry CodeMirror can measure.
  useEffect(() => {
    if (tab === 'write') editor.current?.measure()
    else void flush()
  }, [tab, flush])

  // ── what the Evidence tab asks of the cursor ───────────────────────────────

  // Two buses, both owned by `@/lib/evidence`, both no-ops until somebody
  // subscribes. This pane is the somebody: it is the only thing in the product
  // holding a cursor. Without these, tapping a finding switches to Write and
  // then does nothing, and the cite sheet hides its "insert at the cursor"
  // button because `hasEditor()` is false.

  const pendingReveal = useRef<{ path: string; line: number } | null>(null)

  useEffect(
    () =>
      onRevealRequest((target) => {
        if (!target.path) return
        if (target.path === path) {
          setAtLine(target.line)
          editor.current?.gotoLine(target.line)
          return
        }
        // A different file in the same folder: remember where to land, because
        // the buffer for it has not been read yet.
        pendingReveal.current = { path: target.path, line: target.line }
        setPath(target.path)
      }),
    [path]
  )

  // The jump itself, once the file the finding lives in is actually open.
  useEffect(() => {
    const wanted = pendingReveal.current
    if (!wanted || !doc || doc.path !== wanted.path) return
    pendingReveal.current = null
    setAtLine(wanted.line)
    editor.current?.gotoLine(wanted.line)
  }, [doc])

  useEffect(
    () =>
      onInsertRequest((request) => {
        if (request.report && request.report !== reportId) return
        editor.current?.insert(request.text)
      }),
    [reportId]
  )

  // ── building ───────────────────────────────────────────────────────────────

  const tabRef = useRef(tab)
  tabRef.current = tab
  const ours = useRef(false)
  const lastBuild = useRef(buildResult)

  useEffect(() => {
    if (building && tabRef.current === 'write') ours.current = true
  }, [building])

  useEffect(() => {
    if (buildResult === lastBuild.current) return
    lastBuild.current = buildResult
    if (!buildResult || !ours.current) return
    ours.current = false
    // The build's own verdict decides where you land. A report that built is
    // something to read; a report that did not is a list of findings to work
    // through, and hiding them behind a toast would be hiding the product.
    if (buildResult.ok) setTab('read')
    else setFindingsOpen(true)
  }, [buildResult, setTab])

  const startBuild = useCallback(() => {
    ours.current = true
    void flush().then(() => build())
  }, [flush, build])

  // ── citing ────────────────────────────────────────────────────────────────

  const onCited = useCallback(
    (key: string) => {
      setCiteOpen(false)
      void loadSources()
      // `cite` wrote `sources.yml` and an archived snapshot; anything cached
      // about this vault is now one command out of date.
      invalidate()
      // If the writer is looking at `sources.yml` itself, the file on disk has
      // moved under the buffer — re-read it rather than let a later autosave
      // overwrite the entry that was just archived. Anywhere else, the key goes
      // in at the cursor, which is the whole point of having asked.
      if (path && path === sourcesPath) setReload((n) => n + 1)
      else editor.current?.insert(`@${key}`)
    },
    [loadSources, invalidate, path, sourcesPath]
  )

  // ── what the rule says about this report ───────────────────────────────────

  const reportFindings = useMemo<Finding[]>(
    () => (check?.findings ?? []).filter((finding) => finding.report === reportId),
    [check, reportId]
  )
  const errors = reportFindings.filter((finding) => finding.level === 'error').length

  const lineClasses = path && path === mainPath ? (score?.lines ?? []) : []
  const dirty = saveState === 'dirty' || saveState === 'saving' || saveState === 'error'

  // ── render ────────────────────────────────────────────────────────────────

  if (!reportId) {
    return (
      <Empty
        title="No report open."
        body="Pick one from Reports, or make a new one. Everything here is about a single report's main.typ."
        action={<Button size="lg" onClick={() => setTab('reports')}>Go to Reports</Button>}
      />
    )
  }

  return (
    <div ref={frame} className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {/* ── the strip that says where you are and whether it is saved ─────── */}
      <div className="flex min-h-11 shrink-0 items-center gap-1 border-b px-1.5 lg:min-h-9">
        {files.length > 1 ? (
          <div className="scroll-x -mx-0.5 flex min-w-0 flex-1 items-center gap-1 px-0.5">
            {files.map((file) => (
              <button
                key={file.path}
                type="button"
                onClick={() => {
                  void flush()
                  setPath(file.path)
                }}
                aria-current={file.path === path ? 'true' : undefined}
                className={cn(
                  'tap inline-flex shrink-0 items-center rounded-md px-2.5 font-mono text-[12px] whitespace-nowrap transition-colors lg:min-h-7',
                  file.path === path
                    ? 'bg-secondary text-secondary-foreground font-semibold'
                    : 'text-muted-foreground active:bg-accent'
                )}
              >
                {file.name}
              </button>
            ))}
          </div>
        ) : (
          <span className="min-w-0 flex-1 truncate px-2 font-mono text-[12px] text-muted-foreground">
            {files[0]?.name ?? '…'}
          </span>
        )}

        <SaveIndicator state={saveState} onRetry={() => void flush()} />

        <button
          type="button"
          onClick={() => setFindingsOpen(true)}
          disabled={reportFindings.length === 0}
          className="tap inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium disabled:opacity-40 lg:min-h-8"
          aria-label={`${reportFindings.length} findings on this report`}
        >
          <Quote className="size-4" aria-hidden />
          <span
            className={cn('tabular-nums', errors > 0 && 'text-destructive')}
          >
            {reportFindings.length}
          </span>
        </button>
      </div>

      {/* ── the evidence legend, which is also the report's density ───────── */}
      {score && !keyboardOpen && (
        <button
          type="button"
          onClick={() => setLegendOpen(true)}
          // 44px on a phone, because this is the only way to the legend and a
          // 30px strip is not a target a thumb can hit. It drops back to a
          // strip on a desktop, where the pointer is exact.
          className="flex min-h-[var(--tap)] shrink-0 items-center gap-2 border-b px-3 py-1.5 text-left text-[11px] text-muted-foreground active:bg-accent lg:min-h-0"
        >
          <Meter score={score} />
          <span className="tabular-nums">
            {Math.round((score.density ?? 0) * 100)}% cited or assessed
          </span>
          <span className="ml-auto shrink-0 underline underline-offset-2">
            what the rail means
          </span>
        </button>
      )}

      {/* ── the buffer ───────────────────────────────────────────────────── */}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        {docError ? (
          <Failure
            title="That file could not be read."
            error={docError}
            onRetry={() => setReload((n) => n + 1)}
          />
        ) : detailError ? (
          <Failure
            title="This report could not be opened."
            error={new Error(detailError)}
            onRetry={() => invalidate()}
          />
        ) : doc && doc.path === path ? (
          <Editor
            ref={editor}
            key={`${doc.path}:${reload}`}
            path={doc.path}
            text={doc.text}
            findings={check?.findings ?? []}
            lineClasses={lineClasses}
            stale={dirty}
            sources={sources}
            desktop={desktop}
            atLine={atLine}
            onChange={onChange}
            onSave={() => void flush()}
            onBuild={startBuild}
            onBlur={() => void flush()}
            onFindingSelect={() => setFindingsOpen(true)}
            className="h-full"
          />
        ) : (
          <div className="flex flex-col gap-2 p-4">
            {[0, 1, 2, 3, 4, 5].map((n) => (
              <Skeleton key={n} className="h-4" style={{ width: `${90 - n * 9}%` }} />
            ))}
          </div>
        )}
      </div>

      {/* ── the accessory bar ────────────────────────────────────────────────
          In the flow, at the bottom of a box whose bottom edge is the top of the
          keyboard. Not `position: fixed` — a fixed element is placed against the
          layout viewport, and the layout viewport is exactly what lies while a
          keyboard is open. */}
      {keyboardOpen && (
        <AccessoryBar
          onInsert={(text, caret) => editor.current?.insert(text, caret)}
          onWrap={(before, after) => editor.current?.wrap(before, after)}
          onComplete={() => editor.current?.complete()}
          onCite={() => setCiteOpen(true)}
          onDismiss={() => {
            void flush()
            editor.current?.view()?.contentDOM.blur()
          }}
        />
      )}

      {/* ── overlays ─────────────────────────────────────────────────────── */}
      <FindingsSheet
        open={findingsOpen}
        onOpenChange={setFindingsOpen}
        findings={reportFindings}
        stderr={buildResult && !buildResult.ok ? buildResult.stderr : null}
        desktop={desktop}
        onGoto={(finding) => {
          setFindingsOpen(false)
          const file = files.find((entry) => finding.path.endsWith(entry.name))
          if (file && file.path !== path) setPath(file.path)
          // Re-set even when it is the same number, so a second tap on the same
          // finding still scrolls back to it.
          setAtLine(null)
          window.setTimeout(() => setAtLine(finding.line), 0)
        }}
      />

      <CiteSheet
        open={citeOpen}
        onOpenChange={setCiteOpen}
        reportId={reportId}
        keyboardInset={keyboardInset}
        beforeCite={flush}
        onCited={onCited}
      />

      <LegendSheet open={legendOpen} onOpenChange={setLegendOpen} score={score} />

      {saveError && (
        <div className="shrink-0 border-t bg-destructive/10 px-3 py-2">
          <p className="text-[12px] font-medium text-destructive">Not saved.</p>
          <pre className="scroll-x mt-1 max-h-24 overflow-y-auto font-mono text-[11px] whitespace-pre text-destructive">
            {saveError}
          </pre>
        </div>
      )}
    </div>
  )
}

// ── the saved indicator ──────────────────────────────────────────────────────

/**
 * Four words and a dot.
 *
 * It never says "Saved" over a buffer that has moved, and it never disappears
 * into nothing — a writer on a phone with an intermittent connection needs to
 * know which of their words are on the server, and an indicator that only shows
 * failure teaches them to assume the worst.
 */
function SaveIndicator({ state, onRetry }: { state: SaveState; onRetry: () => void }) {
  if (state === 'error') {
    return (
      <button
        type="button"
        onClick={onRetry}
        className="tap inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-destructive lg:min-h-8"
      >
        <CloudOff className="size-4" aria-hidden />
        <span>Retry</span>
      </button>
    )
  }

  const label =
    state === 'saving' ? 'Saving…' : state === 'dirty' ? 'Unsaved' : 'Saved'

  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 px-2 text-[11px] text-muted-foreground"
      role="status"
      aria-live="polite"
    >
      {state === 'saving' ? (
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
      ) : (
        <Cloud className="size-3.5" aria-hidden />
      )}
      <span>{label}</span>
    </span>
  )
}

// ── the density meter ────────────────────────────────────────────────────────

function Meter({ score }: { score: ReportScore }) {
  const total = score.cited + score.assessed + score.unmarked
  if (total <= 0) {
    return <span className="h-1.5 w-20 shrink-0 rounded-full bg-rail-neutral" aria-hidden />
  }
  const pct = (n: number): string => `${(n / total) * 100}%`
  return (
    <span
      className="flex h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-rail-neutral"
      aria-hidden
    >
      <span className="rail-cited" style={{ width: pct(score.cited) }} />
      <span className="rail-assessed" style={{ width: pct(score.assessed) }} />
      <span className="rail-unmarked" style={{ width: pct(score.unmarked) }} />
    </span>
  )
}

// ── the accessory bar ────────────────────────────────────────────────────────

type KeyDef = {
  label: string
  hint: string
  run: (api: AccessoryApi) => void
}

type AccessoryApi = {
  onInsert: (text: string, caret?: number) => void
  onWrap: (before: string, after?: string) => void
  onComplete: () => void
}

/**
 * The six characters Typst needs and a phone keyboard buries, plus the one
 * action that matters.
 *
 * `@` does not type an at-sign and stop — it types one and opens the list of
 * this report's sources, which is the whole argument of the product compressed
 * into a single tap: citing has to be cheaper than not citing.
 */
const KEYS: KeyDef[] = [
  {
    label: '@',
    hint: 'cite a source already in this report',
    run: ({ onInsert, onComplete }) => {
      onInsert('@')
      onComplete()
    },
  },
  { label: '#', hint: 'start Typst code', run: ({ onInsert }) => onInsert('#') },
  {
    label: '#assess',
    hint: 'mark this sentence as a judgement',
    run: ({ onInsert }) => onInsert('#assess'),
  },
  { label: '*', hint: 'strong', run: ({ onWrap }) => onWrap('*') },
  { label: '_', hint: 'emphasis', run: ({ onWrap }) => onWrap('_') },
  { label: '[', hint: 'open a content block', run: ({ onInsert }) => onInsert('[') },
  { label: ']', hint: 'close a content block', run: ({ onInsert }) => onInsert(']') },
  { label: '"', hint: 'a string', run: ({ onWrap }) => onWrap('"') },
]

function AccessoryBar({
  onInsert,
  onWrap,
  onComplete,
  onCite,
  onDismiss,
}: AccessoryApi & {
  onCite: () => void
  onDismiss: () => void
}) {
  const helpers: AccessoryApi = { onInsert, onWrap, onComplete }

  // `mousedown` is where focus moves, on a phone as much as on a laptop — iOS
  // synthesises it from the touch. Preventing its default is what keeps the
  // keyboard up; without it every tap here closes the keyboard to type one
  // character, which is the reason people abandon accessory bars.
  const hold = (event: MouseEvent): void => event.preventDefault()

  return (
    <div className="flex shrink-0 items-stretch gap-1 border-t bg-background px-1 py-1">
      <button
        type="button"
        onMouseDown={hold}
        onClick={onCite}
        className="tap inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 text-[13px] font-semibold text-primary-foreground active:bg-primary/80"
      >
        <Quote className="size-4" aria-hidden />
        Cite
      </button>

      <div className="scroll-x flex min-w-0 flex-1 items-stretch gap-1">
        {KEYS.map((entry) => (
          <button
            key={entry.label}
            type="button"
            onMouseDown={hold}
            onClick={() => entry.run(helpers)}
            aria-label={`${entry.label} — ${entry.hint}`}
            className="tap inline-flex shrink-0 items-center justify-center rounded-md border px-3 font-mono text-[15px] leading-none active:bg-accent"
          >
            {entry.label}
          </button>
        ))}
      </div>

      <button
        type="button"
        onMouseDown={hold}
        onClick={onDismiss}
        aria-label="Hide the keyboard"
        className="tap inline-flex shrink-0 items-center justify-center rounded-md border px-2 active:bg-accent"
      >
        <ChevronDown className="size-5" aria-hidden />
      </button>
    </div>
  )
}

// ── findings ─────────────────────────────────────────────────────────────────

/**
 * The findings for this report, grouped by code, each one a tap away from the
 * line it is about.
 *
 * Grouped because seventeen E012s are one problem — an unedited starter — and
 * reading them as seventeen problems is a misreading the list can prevent. The
 * text is the engine's own; its refusals name the command that fixes them, and
 * a paraphrase throws away the only useful half.
 */
function FindingsSheet({
  open,
  onOpenChange,
  findings,
  stderr,
  desktop,
  onGoto,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  findings: readonly Finding[]
  stderr: string | null
  desktop: boolean
  onGoto: (finding: Finding) => void
}) {
  const groups = new Map<string, Finding[]>()
  for (const finding of findings) {
    const list = groups.get(finding.code)
    if (list) list.push(finding)
    else groups.set(finding.code, [finding])
  }

  const errors = findings.filter((finding) => finding.level === 'error').length

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side={desktop ? 'right' : 'bottom'}>
        <SheetHeader>
          <SheetTitle>What the rule says</SheetTitle>
          <SheetDescription>
            {findings.length === 0
              ? 'Nothing outstanding on this report.'
              : `${errors} ${errors === 1 ? 'error' : 'errors'}, ${findings.length - errors} ${findings.length - errors === 1 ? 'warning' : 'warnings'}. Tap one to go to the line.`}
          </SheetDescription>
        </SheetHeader>
        <SheetBody>
          {stderr && (
            <pre className="scroll-x mb-3 max-h-40 overflow-y-auto rounded-md border bg-muted p-3 font-mono text-[11px] whitespace-pre">
              {stderr}
            </pre>
          )}
          <ul className="flex flex-col gap-3">
            {[...groups.entries()].map(([code, items]) => (
              <li key={code} className="rounded-lg border">
                <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
                  <Badge variant={items[0].level === 'error' ? 'error' : 'warning'}>
                    {code}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {items.length} {items[0].level}
                    {items.length === 1 ? '' : 's'}
                  </span>
                </div>
                <ul className="divide-y">
                  {items.map((finding, index) => (
                    <li key={`${finding.path}:${finding.line}:${index}`}>
                      <button
                        type="button"
                        onClick={() => onGoto(finding)}
                        className="flex w-full flex-col items-start gap-1 px-3 py-3 text-left active:bg-accent"
                      >
                        <span className="text-sm leading-snug break-anywhere">
                          {finding.message}
                        </span>
                        <span className="font-mono text-[11px] text-muted-foreground break-anywhere">
                          {finding.path}:{finding.line}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </SheetBody>
      </SheetContent>
    </Sheet>
  )
}

// ── citing ───────────────────────────────────────────────────────────────────

/**
 * A URL in, a `@key` out.
 *
 * The server fetches the page, archives it with its sha256 and the moment it was
 * fetched, writes the entry into `sources.yml`, and names the key. Then the key
 * goes in at the cursor. That whole loop is one field and one button, because
 * every step it saves is a step between having a source and citing it.
 *
 * `created: false` is not a failure — the URL was already a source and kept its
 * key, which is exactly what should happen the second time you cite a page.
 */
function CiteSheet({
  open,
  onOpenChange,
  reportId,
  keyboardInset,
  beforeCite,
  onCited,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  reportId: string
  keyboardInset: number
  beforeCite: () => Promise<void>
  onCited: (key: string) => void
}) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; detail: string | null } | null>(null)

  useEffect(() => {
    if (open) {
      setUrl('')
      setError(null)
      setBusy(false)
    }
  }, [open])

  const submit = async (): Promise<void> => {
    const target = url.trim()
    if (!target || busy) return
    setBusy(true)
    setError(null)
    try {
      // Any pending edit goes first: `cite` writes into the report folder, and
      // an autosave landing after it could overwrite what it wrote.
      await beforeCite()
      const result = await guard((signal) => api.cite(reportId, target, signal))
      onCited(result.key)
    } catch (caught) {
      if (isAbort(caught)) return
      setError({
        message: errorText(caught),
        detail: caught instanceof ApiError ? caught.detail : null,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        // The sheet carries an input, so it has to sit on top of the keyboard
        // rather than behind it. `bottom` rather than a transform, because the
        // open and close animations own `translateY`.
        style={keyboardInset > 0 ? { bottom: keyboardInset } : undefined}
        className="lg:mx-auto lg:max-w-lg"
      >
        <SheetHeader>
          <SheetTitle>Cite a page</SheetTitle>
          <SheetDescription>
            The page is fetched and archived as it is right now, with its
            checksum and the date, and the key goes in at your cursor.
          </SheetDescription>
        </SheetHeader>
        <SheetBody>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void submit()
            }}
          >
            <label htmlFor="cite-url" className="sr-only">
              The URL to cite
            </label>
            <Input
              id="cite-url"
              type="url"
              inputMode="url"
              autoComplete="url"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="go"
              placeholder="https://…"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              disabled={busy}
            />
            {/* A submit button inside the form, so the keyboard's Go key works
                as well as the footer's. */}
            <button type="submit" className="sr-only">
              Fetch and cite
            </button>
          </form>

          {error && (
            <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/10 p-3">
              <p className="flex items-start gap-2 text-[13px] font-medium text-destructive break-anywhere">
                <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
                {error.message}
              </p>
              {error.detail && (
                <pre className="scroll-x mt-2 max-h-40 overflow-y-auto font-mono text-[11px] whitespace-pre text-destructive">
                  {error.detail}
                </pre>
              )}
            </div>
          )}
        </SheetBody>
        <SheetFooter>
          <Button
            variant="outline"
            size="lg"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button size="lg" onClick={() => void submit()} disabled={busy || !url.trim()}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Quote aria-hidden />}
            {busy ? 'Fetching…' : 'Fetch and cite'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

// ── the legend ───────────────────────────────────────────────────────────────

/**
 * What the four colours down the right-hand edge mean.
 *
 * It exists because the rail's own explanation is a `title` attribute, and a
 * `title` is a hover affordance that a phone can never show. A colour nobody
 * can name is decoration; this is the sentence that makes it a reading.
 */
function LegendSheet({
  open,
  onOpenChange,
  score,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  score: ReportScore | null
}) {
  const counts: Record<string, number> = {
    cited: score?.cited ?? 0,
    assessed: score?.assessed ?? 0,
    unmarked: score?.unmarked ?? 0,
    neutral: 0,
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="lg:mx-auto lg:max-w-lg">
        <SheetHeader>
          <SheetTitle>The rail</SheetTitle>
          <SheetDescription>
            Something is either cited, or it is an opinion. The blocks down the
            right-hand edge say which each line is, as of the last save.
          </SheetDescription>
        </SheetHeader>
        <SheetBody>
          <ul className="flex flex-col gap-3">
            {(['cited', 'assessed', 'unmarked', 'neutral'] as const).map((kind) => (
              <li key={kind} className="flex items-start gap-3">
                <span
                  className={cn('mt-1 h-4 w-1 shrink-0 rounded-full', `rail-${kind}`)}
                  aria-hidden
                />
                <div className="min-w-0">
                  <p className="text-sm break-anywhere">{RAIL_LABEL[kind]}</p>
                  {kind !== 'neutral' && (
                    <p className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
                      {counts[kind]} {counts[kind] === 1 ? 'line' : 'lines'}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </SheetBody>
      </SheetContent>
    </Sheet>
  )
}

// ── states before there is a buffer ──────────────────────────────────────────

function Empty({
  title,
  body,
  action,
}: {
  title: string
  body: string
  action?: ReactNode
}) {
  return (
    <div className="pane flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-sm text-center">
        <p className="text-sm font-medium">{title}</p>
        <p className="mt-1 text-sm text-muted-foreground break-anywhere">{body}</p>
        {action && <div className="mt-4 flex justify-center">{action}</div>}
      </div>
    </div>
  )
}

function Failure({
  title,
  error,
  onRetry,
}: {
  title: string
  error: ApiError | Error
  onRetry: () => void
}) {
  const detail = error instanceof ApiError ? error.detail : null
  return (
    <div className="pane h-full p-4">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-sm text-muted-foreground break-anywhere">{error.message}</p>
      {detail && (
        <pre className="scroll-x mt-3 max-h-56 overflow-y-auto rounded-md border bg-muted p-3 font-mono text-[11px] whitespace-pre">
          {detail}
        </pre>
      )}
      <Button size="lg" variant="outline" className="mt-4" onClick={onRetry}>
        <RefreshCw aria-hidden />
        Try again
      </Button>
    </div>
  )
}
